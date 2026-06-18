"""Advanced text dialog — dark-theme aware preview."""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (
    QCheckBox, QColorDialog, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFontComboBox, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLabel,
    QPushButton, QSlider, QSpinBox, QTextEdit, QVBoxLayout,
)


class TextPropertiesDialog(QDialog):
    """Insert / edit a text element with full font control.

    The preview adapts to the chosen text colour so it works on a dark
    UI: the canvas where the text will end up is white paper, but the
    *dialog* preview uses a contrast-aware backdrop so light pen
    colours (yellow, cyan) stay readable.
    """

    def __init__(self, parent=None, initial_color=QColor(0, 0, 0)):
        super().__init__(parent)
        self.setWindowTitle("Insert Text")
        self.setMinimumSize(540, 460)
        self.text_color = QColor(initial_color)
        self.selected_font = QFont("Arial", 24)
        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        text_group = QGroupBox("Text")
        text_layout = QVBoxLayout()
        self.text_input = QTextEdit()
        self.text_input.setMaximumHeight(80)
        self.text_input.setPlaceholderText("Enter your text here...")
        self.text_input.textChanged.connect(self._update_preview)
        text_layout.addWidget(self.text_input)
        text_group.setLayout(text_layout)
        root.addWidget(text_group)

        font_group = QGroupBox("Font Properties")
        font_layout = QFormLayout()

        self.font_combo = QFontComboBox()
        self.font_combo.setCurrentFont(QFont("Arial"))
        self.font_combo.currentFontChanged.connect(self._update_font)
        font_layout.addRow("Family:", self.font_combo)

        size_row = QHBoxLayout()
        self.size_spin = QSpinBox()
        self.size_spin.setRange(6, 200)
        self.size_spin.setValue(24)
        self.size_spin.setSuffix(" pt")
        self.size_spin.valueChanged.connect(self._update_font)
        self.size_slider = QSlider(Qt.Horizontal)
        self.size_slider.setRange(6, 200)
        self.size_slider.setValue(24)
        self.size_slider.valueChanged.connect(self.size_spin.setValue)
        self.size_spin.valueChanged.connect(self.size_slider.setValue)
        size_row.addWidget(self.size_spin)
        size_row.addWidget(self.size_slider)
        font_layout.addRow("Size:", size_row)

        style_row = QHBoxLayout()
        self.bold_check = QCheckBox("Bold")
        self.italic_check = QCheckBox("Italic")
        self.underline_check = QCheckBox("Underline")
        self.strikeout_check = QCheckBox("Strikeout")
        for cb in (self.bold_check, self.italic_check,
                   self.underline_check, self.strikeout_check):
            cb.stateChanged.connect(self._update_font)
            style_row.addWidget(cb)
        font_layout.addRow("Style:", style_row)

        color_row = QHBoxLayout()
        self.color_preview = QFrame()
        self.color_preview.setFixedSize(60, 28)
        self._refresh_color_swatch()
        color_btn = QPushButton("Choose Color...")
        color_btn.clicked.connect(self._pick_color)
        color_row.addWidget(self.color_preview)
        color_row.addWidget(color_btn)
        color_row.addStretch()
        font_layout.addRow("Color:", color_row)

        self.letter_spacing = QDoubleSpinBox()
        self.letter_spacing.setRange(-5, 20)
        self.letter_spacing.setSingleStep(0.5)
        self.letter_spacing.setSuffix(" px")
        self.letter_spacing.valueChanged.connect(self._update_font)
        font_layout.addRow("Letter Spacing:", self.letter_spacing)

        self.rotation_spin = QSpinBox()
        self.rotation_spin.setRange(-360, 360)
        self.rotation_spin.setSuffix("°")
        font_layout.addRow("Rotation:", self.rotation_spin)

        font_group.setLayout(font_layout)
        root.addWidget(font_group)

        preview_group = QGroupBox("Preview (paper colour)")
        preview_layout = QVBoxLayout()
        self.preview_label = QLabel("Sample Text")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumHeight(80)
        preview_layout.addWidget(self.preview_label)
        preview_group.setLayout(preview_layout)
        root.addWidget(preview_group)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._update_font()

    # ------------------------------------------------------------------
    def _refresh_color_swatch(self) -> None:
        self.color_preview.setStyleSheet(
            f"background-color: {self.text_color.name()};"
            f" border: 2px solid #585b70; border-radius: 3px;"
        )

    def _pick_color(self) -> None:
        color = QColorDialog.getColor(self.text_color, self, "Select Text Color")
        if color.isValid():
            self.text_color = color
            self._refresh_color_swatch()
            self._update_preview()

    def _update_font(self) -> None:
        font = self.font_combo.currentFont()
        font.setPointSize(self.size_spin.value())
        font.setBold(self.bold_check.isChecked())
        font.setItalic(self.italic_check.isChecked())
        font.setUnderline(self.underline_check.isChecked())
        font.setStrikeOut(self.strikeout_check.isChecked())
        font.setLetterSpacing(QFont.AbsoluteSpacing, self.letter_spacing.value())
        self.selected_font = font
        self._update_preview()

    def _update_preview(self) -> None:
        text = self.text_input.toPlainText() or "Sample Text"
        self.preview_label.setText(text)
        self.preview_label.setFont(self.selected_font)
        # White paper background, but make sure light pen colours stay
        # visible by adding a subtle backdrop frame.
        bg = "#fafafa"
        if self.text_color.lightness() > 200:
            bg = "#1e1e2e"  # invert for very light pens
        self.preview_label.setStyleSheet(
            f"QLabel {{"
            f"  background-color: {bg};"
            f"  color: {self.text_color.name()};"
            f"  border: 2px dashed #585b70;"
            f"  border-radius: 4px;"
            f"  padding: 10px;"
            f"}}"
        )

    # ------------------------------------------------------------------
    def get_properties(self) -> dict:
        return {
            "text": self.text_input.toPlainText(),
            "font": self.selected_font,
            "color": self.text_color,
            "rotation": self.rotation_spin.value(),
            "letter_spacing": self.letter_spacing.value(),
        }
