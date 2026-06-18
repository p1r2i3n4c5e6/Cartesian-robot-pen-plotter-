"""Right-hand "Multiple text input" panel.

Lets the user queue up several text strings (each with its own
font / colour / rotation / spacing) and drop them onto the canvas
without having to re-open the text dialog every time.

Each row in the list is independently editable.  The "Add to canvas"
button places the selected entry at the canvas centre; the user can
then drag it where they want.
"""

from __future__ import annotations

from typing import List, Optional

from PyQt5.QtCore import QPointF, Qt
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (
    QColorDialog, QComboBox, QDoubleSpinBox, QFontComboBox, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QSpinBox, QStyle, QVBoxLayout, QWidget,
)

from PyQt5.QtWidgets import QGraphicsTextItem


class _TextEntry:
    """In-memory record for one queued text string."""

    __slots__ = ("text", "font", "color", "rotation", "letter_spacing")

    def __init__(self, text: str = "Hello", font: Optional[QFont] = None,
                 color: Optional[QColor] = None, rotation: float = 0.0,
                 letter_spacing: float = 0.0):
        self.text = text
        self.font = font or QFont("Arial", 14)
        self.color = QColor(color) if color else QColor(0, 0, 0)
        self.rotation = float(rotation)
        self.letter_spacing = float(letter_spacing)

    def label(self) -> str:
        sample = self.text if len(self.text) <= 24 else self.text[:21] + "…"
        return f'"{sample}"  —  {self.font.family()} {self.font.pointSize()}pt'


class MultiTextPanel(QWidget):
    """Right-side panel — manage many text strings at once."""

    def __init__(self, canvas, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self._entries: List[_TextEntry] = []
        self._build()
        # Seed with a single editable example so the panel is never blank.
        self._entries.append(_TextEntry("Sample text"))
        self._refresh_list()
        self.list_widget.setCurrentRow(0)
        self._populate_editor_from_selection()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        title = QLabel("Multiple Text Inputs")
        title.setStyleSheet(
            "background: transparent; color: #89b4fa;"
            " font-weight: bold; font-size: 13px;"
        )
        layout.addWidget(title)

        hint = QLabel(
            "Queue several strings, then drop them on the canvas.\n"
            "Each entry keeps its own font, colour and rotation."
        )
        hint.setStyleSheet("color: #a6adc8; font-size: 10px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet(
            "QListWidget { background: #1e1e2e; color: #cdd6f4;"
            "  border: 1px solid #45475a; border-radius: 3px; }"
            "QListWidget::item:selected { background: #89b4fa;"
            "  color: #1e1e2e; }"
        )
        self.list_widget.currentRowChanged.connect(
            self._on_selection_changed
        )
        layout.addWidget(self.list_widget, 2)

        list_btns = QHBoxLayout()
        self.add_btn = QPushButton(" New")
        self.add_btn.setIcon(self.style().standardIcon(QStyle.SP_FileIcon))
        self.add_btn.clicked.connect(self._add_entry)
        self.dup_btn = QPushButton(" Duplicate")
        self.dup_btn.setIcon(
            self.style().standardIcon(QStyle.SP_DialogSaveButton)
        )
        self.dup_btn.clicked.connect(self._duplicate_entry)
        self.del_btn = QPushButton(" Remove")
        self.del_btn.setIcon(self.style().standardIcon(QStyle.SP_TrashIcon))
        self.del_btn.clicked.connect(self._remove_entry)
        for b in (self.add_btn, self.dup_btn, self.del_btn):
            b.setStyleSheet(
                "QPushButton { background: #45475a; color: #cdd6f4;"
                "  padding: 5px 10px; border: 1px solid #585b70;"
                "  border-radius: 3px; }"
                "QPushButton:hover { background: #585b70; color: #fff; }"
            )
            list_btns.addWidget(b)
        layout.addLayout(list_btns)

        # ---- editor ---------------------------------------------------
        editor = QGroupBox("Edit selected entry")
        editor_layout = QVBoxLayout()

        editor_layout.addWidget(QLabel("Text:"))
        self.text_edit = QLineEdit()
        self.text_edit.setStyleSheet(
            "QLineEdit { background: #181825; color: #cdd6f4;"
            "  padding: 6px; border: 1px solid #45475a;"
            "  border-radius: 3px; }"
        )
        self.text_edit.textEdited.connect(self._editor_changed)
        editor_layout.addWidget(self.text_edit)

        editor_layout.addWidget(QLabel("Font family:"))
        self.font_combo = QFontComboBox()
        self.font_combo.currentFontChanged.connect(self._font_family_changed)
        editor_layout.addWidget(self.font_combo)

        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Size (pt):"))
        self.size_spin = QSpinBox()
        self.size_spin.setRange(4, 200)
        self.size_spin.setValue(14)
        self.size_spin.valueChanged.connect(self._font_size_changed)
        size_row.addWidget(self.size_spin)
        size_row.addWidget(QLabel("  Letter sp.:"))
        self.spacing_spin = QDoubleSpinBox()
        self.spacing_spin.setRange(-5.0, 20.0)
        self.spacing_spin.setSingleStep(0.5)
        self.spacing_spin.valueChanged.connect(self._spacing_changed)
        size_row.addWidget(self.spacing_spin)
        editor_layout.addLayout(size_row)

        rot_row = QHBoxLayout()
        rot_row.addWidget(QLabel("Rotation (°):"))
        self.rotation_spin = QDoubleSpinBox()
        self.rotation_spin.setRange(-360.0, 360.0)
        self.rotation_spin.setSingleStep(5.0)
        self.rotation_spin.valueChanged.connect(self._rotation_changed)
        rot_row.addWidget(self.rotation_spin)
        rot_row.addWidget(QLabel("  Style:"))
        self.style_combo = QComboBox()
        self.style_combo.addItems(["Normal", "Bold", "Italic", "Bold Italic"])
        self.style_combo.currentIndexChanged.connect(self._style_changed)
        rot_row.addWidget(self.style_combo)
        editor_layout.addLayout(rot_row)

        col_row = QHBoxLayout()
        col_row.addWidget(QLabel("Colour:"))
        self.color_btn = QPushButton(" Pick colour")
        self.color_btn.clicked.connect(self._pick_color)
        col_row.addWidget(self.color_btn)
        editor_layout.addLayout(col_row)

        self.preview = QLabel("Preview")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(50)
        self.preview.setStyleSheet(
            "background: #ffffff; color: black;"
            " border: 1px solid #45475a; border-radius: 3px;"
            " padding: 6px;"
        )
        editor_layout.addWidget(self.preview)

        editor.setLayout(editor_layout)
        layout.addWidget(editor, 3)

        # ---- place / batch buttons -----------------------------------
        place_btns = QHBoxLayout()
        self.place_btn = QPushButton(" Add to canvas")
        self.place_btn.setIcon(
            self.style().standardIcon(QStyle.SP_ArrowRight)
        )
        self.place_btn.setStyleSheet(
            "QPushButton { background: #4CAF50; color: white;"
            "  font-weight: bold; padding: 8px 14px;"
            "  border-radius: 4px; }"
            "QPushButton:hover { background: #45a049; color: white; }"
        )
        self.place_btn.clicked.connect(self._place_selected)
        place_btns.addWidget(self.place_btn)

        self.place_all_btn = QPushButton(" Add ALL")
        self.place_all_btn.setIcon(
            self.style().standardIcon(QStyle.SP_DialogApplyButton)
        )
        self.place_all_btn.setStyleSheet(
            "QPushButton { background: #2196F3; color: white;"
            "  font-weight: bold; padding: 8px 14px;"
            "  border-radius: 4px; }"
            "QPushButton:hover { background: #1976D2; color: white; }"
        )
        self.place_all_btn.clicked.connect(self._place_all)
        place_btns.addWidget(self.place_all_btn)
        layout.addLayout(place_btns)

        layout.addStretch()

    # ------------------------------------------------------------------
    # Entry list operations
    # ------------------------------------------------------------------
    def _refresh_list(self) -> None:
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for entry in self._entries:
            QListWidgetItem(entry.label(), self.list_widget)
        self.list_widget.blockSignals(False)

    def _selected_entry(self) -> Optional[_TextEntry]:
        idx = self.list_widget.currentRow()
        if 0 <= idx < len(self._entries):
            return self._entries[idx]
        return None

    def _add_entry(self) -> None:
        self._entries.append(_TextEntry("New text"))
        self._refresh_list()
        self.list_widget.setCurrentRow(len(self._entries) - 1)

    def _duplicate_entry(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        clone = _TextEntry(
            text=entry.text,
            font=QFont(entry.font),
            color=QColor(entry.color),
            rotation=entry.rotation,
            letter_spacing=entry.letter_spacing,
        )
        self._entries.append(clone)
        self._refresh_list()
        self.list_widget.setCurrentRow(len(self._entries) - 1)

    def _remove_entry(self) -> None:
        idx = self.list_widget.currentRow()
        if 0 <= idx < len(self._entries):
            del self._entries[idx]
            self._refresh_list()
            new_idx = max(0, idx - 1) if self._entries else -1
            self.list_widget.setCurrentRow(new_idx)
            self._populate_editor_from_selection()

    # ------------------------------------------------------------------
    # Editor binding
    # ------------------------------------------------------------------
    def _on_selection_changed(self, _row: int) -> None:
        self._populate_editor_from_selection()

    def _populate_editor_from_selection(self) -> None:
        entry = self._selected_entry()
        widgets = (self.text_edit, self.font_combo, self.size_spin,
                   self.spacing_spin, self.rotation_spin,
                   self.style_combo, self.color_btn,
                   self.place_btn, self.dup_btn, self.del_btn)
        for w in widgets:
            w.setEnabled(entry is not None)
        if entry is None:
            self.preview.setText("(no entry)")
            return

        # Block signals while we sync widgets so we don't re-fire writes.
        for w in (self.text_edit, self.font_combo, self.size_spin,
                  self.spacing_spin, self.rotation_spin, self.style_combo):
            w.blockSignals(True)
        self.text_edit.setText(entry.text)
        self.font_combo.setCurrentFont(entry.font)
        self.size_spin.setValue(max(4, entry.font.pointSize()))
        self.spacing_spin.setValue(entry.letter_spacing)
        self.rotation_spin.setValue(entry.rotation)
        bold = entry.font.bold()
        italic = entry.font.italic()
        self.style_combo.setCurrentIndex(
            (1 if bold else 0) + (2 if italic else 0)
        )
        for w in (self.text_edit, self.font_combo, self.size_spin,
                  self.spacing_spin, self.rotation_spin, self.style_combo):
            w.blockSignals(False)

        self._refresh_preview()
        self._refresh_color_button()

    def _refresh_color_button(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        c = entry.color
        self.color_btn.setStyleSheet(
            f"QPushButton {{ background: rgb({c.red()},{c.green()},{c.blue()});"
            "  color: white; font-weight: bold; padding: 6px;"
            "  border: 1px solid #585b70; border-radius: 3px; }"
        )

    def _refresh_preview(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            self.preview.setText("(no entry)")
            return
        self.preview.setFont(entry.font)
        c = entry.color
        self.preview.setStyleSheet(
            f"background: #ffffff;"
            f" color: rgb({c.red()},{c.green()},{c.blue()});"
            "  border: 1px solid #45475a; border-radius: 3px;"
            "  padding: 6px;"
        )
        self.preview.setText(entry.text or " ")

    # ------------------------------------------------------------------
    # Editor → entry sync
    # ------------------------------------------------------------------
    def _editor_changed(self, text: str) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        entry.text = text
        self._update_current_label()
        self._refresh_preview()

    def _font_family_changed(self, font: QFont) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        new_font = QFont(font)
        new_font.setPointSize(max(4, self.size_spin.value()))
        new_font.setBold(self.style_combo.currentIndex() in (1, 3))
        new_font.setItalic(self.style_combo.currentIndex() in (2, 3))
        entry.font = new_font
        self._update_current_label()
        self._refresh_preview()

    def _font_size_changed(self, size: int) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        f = QFont(entry.font)
        f.setPointSize(max(4, size))
        entry.font = f
        self._update_current_label()
        self._refresh_preview()

    def _spacing_changed(self, value: float) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        entry.letter_spacing = float(value)

    def _rotation_changed(self, value: float) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        entry.rotation = float(value)

    def _style_changed(self, idx: int) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        f = QFont(entry.font)
        f.setBold(idx in (1, 3))
        f.setItalic(idx in (2, 3))
        entry.font = f
        self._refresh_preview()

    def _pick_color(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        color = QColorDialog.getColor(entry.color, self, "Pick text colour")
        if color.isValid():
            entry.color = color
            self._refresh_color_button()
            self._refresh_preview()

    def _update_current_label(self) -> None:
        idx = self.list_widget.currentRow()
        entry = self._selected_entry()
        if entry is None or idx < 0:
            return
        item = self.list_widget.item(idx)
        if item is not None:
            item.setText(entry.label())

    # ------------------------------------------------------------------
    # Canvas placement
    # ------------------------------------------------------------------
    def _drop_entry_on_canvas(self, entry: _TextEntry,
                              offset: int = 0) -> None:
        if not entry.text:
            return
        center = self.canvas.mm_to_scene(
            self.canvas.plot_width_mm / 2 + offset * 4,
            self.canvas.plot_height_mm / 2 - offset * 6,
        )
        text_item = QGraphicsTextItem(entry.text)
        text_item.setDefaultTextColor(entry.color)
        text_item.setFont(entry.font)
        text_item.setPos(center)
        text_item.setRotation(entry.rotation)
        text_item.setFlag(QGraphicsTextItem.ItemIsMovable, True)
        text_item.setFlag(QGraphicsTextItem.ItemIsSelectable, True)
        self.canvas.scene.addItem(text_item)
        self.canvas.strokes.append({
            "type": "text",
            "color": QColor(entry.color),
            "text": entry.text,
            "pos": QPointF(center),
            "font": QFont(entry.font),
            "rotation": entry.rotation,
            "letter_spacing": entry.letter_spacing,
            "item": text_item,
        })

    def _place_selected(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        self._drop_entry_on_canvas(entry)

    def _place_all(self) -> None:
        for i, entry in enumerate(self._entries):
            self._drop_entry_on_canvas(entry, offset=i)
