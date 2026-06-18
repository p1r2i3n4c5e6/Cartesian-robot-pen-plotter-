"""GUI dock-panels split out of the monolithic ``robot2.py``.

Each panel has a single, focused responsibility so they can be edited
without dragging the whole UI around.
"""

from .calibration import CalibrationPanel
from .color_palette import ColorPalette
from .console import ConsolePanel
from .homing import HomingPanel
from .jog import JogPanel
from .multi_text import MultiTextPanel
from .pen_changer import PenChangerPanel
from .safety import SafetyControlPanel
from .serial_panel import SerialPanel
from .settings_panel import SettingsPanel
from .tool_panel import ToolPanel

__all__ = [
    "CalibrationPanel",
    "ColorPalette",
    "ConsolePanel",
    "HomingPanel",
    "JogPanel",
    "MultiTextPanel",
    "PenChangerPanel",
    "SafetyControlPanel",
    "SerialPanel",
    "SettingsPanel",
    "ToolPanel",
]
