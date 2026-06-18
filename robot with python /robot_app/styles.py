"""Application stylesheet — fully dark with explicit text colours.

Bug fix from ``robot2.py``:
many per-widget stylesheets only set ``background-color`` and let the
default Qt palette pick the foreground.  On Ubuntu / Pi OS the default
fg is **black**, which produced unreadable buttons in the toolbox,
console pen rack, etc.  Every selector here sets *both* ``background``
and ``color`` so text never disappears on hover or focus.
"""

from .config import IS_RPI

_BASE_FONT_SIZE = "10px" if IS_RPI else "11px"
_BASE_PADDING = "4px" if IS_RPI else "6px"


INDUSTRIAL_STYLE = f"""
/* ------- root canvases ------- */
QMainWindow, QDialog {{
    background-color: #1e1e2e;
    color: #cdd6f4;
}}
QWidget {{
    background-color: transparent;
    color: #cdd6f4;
    font-family: 'Segoe UI', 'Ubuntu', 'DejaVu Sans', sans-serif;
    font-size: {_BASE_FONT_SIZE};
}}

/* ------- Dock widgets ------- */
QDockWidget {{ color: #cdd6f4; font-weight: bold; }}
QDockWidget::title {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #313244, stop:1 #181825);
    color: #cdd6f4;
    padding: 6px;
    border-bottom: 2px solid #89b4fa;
}}

/* ------- Menus / toolbars ------- */
QMenuBar {{ background: #313244; color: #cdd6f4; padding: 3px; }}
QMenuBar::item {{ background: transparent; color: #cdd6f4; padding: 5px 10px; }}
QMenuBar::item:selected {{ background: #45475a; color: #f5f5f5; border-radius: 3px; }}
QMenu {{ background: #313244; color: #cdd6f4; border: 1px solid #45475a; }}
QMenu::item {{ background: transparent; color: #cdd6f4; padding: 5px 22px; }}
QMenu::item:selected {{ background: #89b4fa; color: #1e1e2e; }}

QToolBar {{
    background: #313244;
    color: #cdd6f4;
    spacing: 4px; padding: 4px;
    border-bottom: 1px solid #45475a;
}}
QToolButton {{
    color: #cdd6f4;
    background: #45475a;
    padding: {_BASE_PADDING} 10px;
    border-radius: 4px;
    border: 1px solid transparent;
}}
QToolButton:hover {{
    background: #585b70;
    color: #ffffff;
    border: 1px solid #89b4fa;
}}
QToolButton:pressed {{
    background: #89b4fa;
    color: #1e1e2e;
}}

QStatusBar {{
    background: #181825;
    color: #a6adc8;
    border-top: 1px solid #45475a;
}}

/* ------- Group boxes ------- */
QGroupBox {{
    color: #89b4fa;
    background: #1e1e2e;
    font-weight: bold;
    border: 1px solid #45475a;
    border-radius: 5px;
    margin-top: 12px;
    padding: 8px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px; padding: 0 5px;
    background: #1e1e2e;
    color: #89b4fa;
}}

/* ------- Labels ------- */
QLabel {{ background: transparent; color: #cdd6f4; }}

/* ------- Inputs ------- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QFontComboBox, QTextEdit {{
    background: #313244;
    color: #cdd6f4;
    selection-background-color: #89b4fa;
    selection-color: #1e1e2e;
    padding: 4px;
    border: 1px solid #45475a;
    border-radius: 4px;
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QComboBox:focus, QFontComboBox:focus, QTextEdit:focus {{
    border: 2px solid #89b4fa;
}}
QComboBox::drop-down, QFontComboBox::drop-down {{ border: none; }}
QComboBox QAbstractItemView {{
    background: #313244;
    color: #cdd6f4;
    selection-background-color: #89b4fa;
    selection-color: #1e1e2e;
    border: 1px solid #45475a;
}}

/* ------- Checkbox / radio ------- */
QCheckBox, QRadioButton {{ color: #cdd6f4; spacing: 6px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 14px; height: 14px;
    border: 1px solid #45475a;
    background-color: #313244;
}}
QCheckBox::indicator {{ border-radius: 3px; }}
QRadioButton::indicator {{ border-radius: 7px; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background-color: #a6e3a1;
    border-color: #a6e3a1;
}}

/* ------- Push buttons (default look) ------- */
QPushButton {{
    background: #45475a;
    color: #cdd6f4;
    padding: {_BASE_PADDING} 12px;
    border: 1px solid #585b70;
    border-radius: 4px;
    font-weight: 500;
}}
QPushButton:hover {{
    background: #585b70;
    color: #ffffff;
    border: 1px solid #89b4fa;
}}
QPushButton:pressed {{ background: #89b4fa; color: #1e1e2e; }}
QPushButton:disabled {{ background: #313244; color: #6c7086; border-color: #45475a; }}

/* ------- Tabs ------- */
QTabWidget::pane {{ border: 1px solid #45475a; background: #1e1e2e; }}
QTabBar::tab {{
    background: #313244;
    color: #cdd6f4;
    padding: 6px 14px;
    border: 1px solid #45475a;
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
}}
QTabBar::tab:selected {{ background: #89b4fa; color: #1e1e2e; font-weight: bold; }}
QTabBar::tab:hover:!selected {{ background: #45475a; color: #ffffff; }}

/* ------- Scrollbars ------- */
QScrollBar:vertical {{ background: #1e1e2e; width: 10px; border: none; }}
QScrollBar::handle:vertical {{
    background: #45475a; border-radius: 5px; min-height: 25px;
}}
QScrollBar::handle:vertical:hover {{ background: #585b70; }}
QScrollBar:horizontal {{ background: #1e1e2e; height: 10px; border: none; }}
QScrollBar::handle:horizontal {{
    background: #45475a; border-radius: 5px; min-width: 25px;
}}
QScrollBar::handle:horizontal:hover {{ background: #585b70; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}

/* ------- Slider ------- */
QSlider::groove:horizontal {{
    border: 1px solid #45475a;
    height: 5px;
    background: #313244;
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: #89b4fa;
    border: 1px solid #89b4fa;
    width: 14px;
    margin: -5px 0;
    border-radius: 7px;
}}

/* ------- Progress bar ------- */
QProgressBar {{
    border: 1px solid #45475a;
    border-radius: 3px;
    background: #313244;
    color: #cdd6f4;
    text-align: center;
}}
QProgressBar::chunk {{ background: #4CAF50; border-radius: 2px; }}

/* ------- Tooltip ------- */
QToolTip {{
    background-color: #181825;
    color: #f9e2af;
    border: 1px solid #89b4fa;
    padding: 4px 8px;
}}
"""
