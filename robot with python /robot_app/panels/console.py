"""Streaming console + progress bar.

Console explicitly sets *both* foreground and background so the green
text stays readable when the global stylesheet cascades.

Every quick-action button shows an icon, a human-readable label, and a
tooltip that explains the underlying GRBL command — no more cryptic
``$$`` / ``$X`` buttons that only experts can decode.
"""

from PyQt5.QtCore import QSize
from PyQt5.QtWidgets import (
    QHBoxLayout, QLineEdit, QProgressBar, QPushButton, QStyle, QTextEdit,
    QVBoxLayout, QWidget,
)


class ConsolePanel(QWidget):
    """Live console for talking to the GRBL controller.

    The bottom toolbar holds four labelled GRBL shortcuts:

    * **Status** (``?``) – ask the machine where it is and what it is doing.
    * **Settings** (``$$``) – dump every configuration parameter.
    * **Unlock** (``$X``) – clear an ALARM state after a limit / e-stop hit.
    * **Home** (``$H``) – run the homing cycle.
    """

    def __init__(self, serial_worker, parent=None):
        super().__init__(parent)
        self.serial_worker = serial_worker
        # Track whether a stream is in flight so we can ignore stray
        # progress events that would otherwise reset the bar.
        self._stream_active = False
        self._last_total = 0
        self._last_pct = 0
        self._build()
        self.serial_worker.data_received.connect(self.append_line)
        self.serial_worker.progress_updated.connect(self.update_progress)
        self.serial_worker.streaming_finished.connect(self._on_stream_finished)

    def _build(self) -> None:
        layout = QVBoxLayout(self)

        self.progress = QProgressBar()
        self.progress.setStyleSheet(
            "QProgressBar { border: 1px solid #45475a; border-radius: 3px;"
            "  background: #313244; color: #cdd6f4;"
            "  text-align: center; }"
            "QProgressBar::chunk { background-color: #4CAF50; border-radius: 2px; }"
        )
        layout.addWidget(self.progress)

        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setStyleSheet(
            "QTextEdit {"
            "  background-color: #0d0d0d; color: #4af04a;"
            "  font-family: 'Courier New', monospace; font-size: 11px;"
            "  border: 1px solid #45475a; border-radius: 3px;"
            "  selection-background-color: #4af04a;"
            "  selection-color: #0d0d0d;"
            "}"
        )
        layout.addWidget(self.console)

        input_row = QHBoxLayout()
        self.cmd_input = QLineEdit()
        self.cmd_input.setPlaceholderText("GRBL command (e.g. $$, $H, ?, G0 X10)")
        self.cmd_input.returnPressed.connect(self.send_command)
        self.cmd_input.setStyleSheet(
            "QLineEdit {"
            "  background-color: #181825; color: #4af04a;"
            "  font-family: 'Courier New', monospace;"
            "  padding: 5px; border: 1px solid #45475a; border-radius: 3px;"
            "}"
            "QLineEdit:focus { border: 2px solid #89b4fa; }"
        )
        send_btn = QPushButton(" Send")
        send_btn.setIcon(self.style().standardIcon(QStyle.SP_ArrowForward))
        send_btn.setIconSize(QSize(16, 16))
        send_btn.setToolTip("Send the typed GRBL / G-code command")
        send_btn.setStyleSheet(
            "QPushButton { background: #45475a; color: #cdd6f4;"
            "  padding: 6px 14px; border: 1px solid #585b70; border-radius: 3px; }"
            "QPushButton:hover { background: #585b70; color: #ffffff; }"
        )
        send_btn.clicked.connect(self.send_command)
        input_row.addWidget(self.cmd_input)
        input_row.addWidget(send_btn)
        layout.addLayout(input_row)

        # ---- Quick GRBL command shortcuts -------------------------------
        # Each entry: (visible label, command, Qt standard icon, tooltip)
        quick_actions = [
            (
                "Status",
                "?",
                QStyle.SP_FileDialogContentsView,
                "<b>Query Machine Status</b><br>"
                "Sends <code>?</code> to GRBL.<br>"
                "Returns current state (Idle / Run / Hold / Alarm) and "
                "machine position. Used to refresh the DRO.",
            ),
            (
                "Settings",
                "$$",
                QStyle.SP_FileDialogDetailedView,
                "<b>View All Settings</b><br>"
                "Sends <code>$$</code> to GRBL.<br>"
                "Lists every configuration parameter ($0, $1, …) "
                "including steps/mm, feed limits, soft limits.",
            ),
            (
                "Unlock",
                "$X",
                QStyle.SP_DialogResetButton,
                "<b>Clear Alarm State</b><br>"
                "Sends <code>$X</code> to GRBL.<br>"
                "Use after a limit-switch hit, soft-limit violation, "
                "or emergency stop. Coordinates are not trusted until "
                "you home the machine again.",
            ),
            (
                "Home",
                "$H",
                QStyle.SP_DirHomeIcon,
                "<b>Run Homing Cycle</b><br>"
                "Sends <code>$H</code> to GRBL.<br>"
                "All enabled axes move toward their limit switches, "
                "back off by the pull-off distance, and machine "
                "coordinates are set. Make sure the path is clear!",
            ),
        ]

        quick_row = QHBoxLayout()
        for label, cmd, icon_enum, tooltip in quick_actions:
            btn = QPushButton(f" {label}  ({cmd})")
            btn.setIcon(self.style().standardIcon(icon_enum))
            btn.setIconSize(QSize(18, 18))
            btn.setToolTip(tooltip)
            btn.setStyleSheet(
                "QPushButton {"
                "  background: #45475a; color: #cdd6f4;"
                "  padding: 7px 10px; border: 1px solid #585b70;"
                "  border-radius: 4px; font-weight: 500;"
                "  text-align: left;"
                "}"
                "QPushButton:hover {"
                "  background: #585b70; color: #ffffff;"
                "  border: 1px solid #89b4fa;"
                "}"
                "QPushButton:pressed {"
                "  background: #89b4fa; color: #1e1e2e;"
                "}"
            )
            btn.clicked.connect(
                lambda _ch, c=cmd: self.serial_worker.send_raw(c)
            )
            quick_row.addWidget(btn)
        layout.addLayout(quick_row)

    # ------------------------------------------------------------------
    def send_command(self) -> None:
        cmd = self.cmd_input.text().strip()
        if not cmd:
            return
        self.append_line(f">>> {cmd}")
        self.serial_worker.send_raw(cmd)
        self.cmd_input.clear()

    def append_line(self, text: str) -> None:
        self.console.append(text)
        # Cap history so the Pi doesn't run out of RAM during long jobs
        if self.console.document().lineCount() > 800:
            cursor = self.console.textCursor()
            cursor.movePosition(cursor.Start)
            cursor.movePosition(cursor.Down, cursor.KeepAnchor, 200)
            cursor.removeSelectedText()
        scrollbar = self.console.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def update_progress(self, current: int, total: int) -> None:
        """Update the streaming progress bar.

        Treat ``current=0, total=N`` as the start-of-stream sentinel and
        REFUSE to roll the bar backward once we've crossed >0 %.  cncjs
        likes to re-broadcast post-job status events with weird counters
        — we no longer let those repaint the bar to zero.
        """
        if total <= 0:
            return  # nothing meaningful, leave the bar alone
        # New stream begins -> first event with current=0 resets state.
        if current == 0 and (not self._stream_active
                             or total != self._last_total):
            self._stream_active = True
            self._last_total = total
            self._last_pct = 0
            self.progress.setValue(0)
            self.progress.setFormat(f"0/{total} (0%)")
            return
        pct = int((current / total) * 100) if total else 0
        # Never let the percentage decrease while a job is active
        if pct < self._last_pct and self._stream_active:
            return
        self._last_pct = pct
        self._last_total = total
        self.progress.setValue(pct)
        self.progress.setFormat(f"{current}/{total} ({pct}%)")

    def _on_stream_finished(self, success: bool, message: str) -> None:
        """Lock the bar at 100 % on success and stop accepting updates."""
        self._stream_active = False
        if success:
            self.progress.setValue(100)
            self.progress.setFormat(
                f"✓ {self._last_total}/{self._last_total} (100%)"
            )
        # Failure / abort — keep the last good value so the user can see
        # how far along the job got.
