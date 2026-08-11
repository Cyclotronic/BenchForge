"""
Fluent Theme Tokens & Stylesheet Builder (`theme.py`)

Single source of truth for BenchForge Studio's visual language.

The design register is "professional Windows engineering tool" — closer to
Visual Studio or SQL Server Management Studio than to Windows Settings:
squarer corners, tight row density, restrained use of colour, and semantic
state colours kept strictly separate from the accent hue.

Usage:
    from theme import build_qss, palette_for_scheme, DATA_FONT

    app.setStyleSheet(build_qss(palette_for_scheme(app)))

Qt Style Sheets support neither `box-shadow` nor `color-mix()`, so every
tinted border and surface below is pre-computed as a literal hex value.
"""

from typing import Dict

# --------------------------------------------------------------------------
# Palettes
# --------------------------------------------------------------------------

LIGHT: Dict[str, str] = {
    "bg":        "#F3F5F7",   # window ground
    "surface":   "#FFFFFF",   # panels, toolbar, status bar
    "surface2":  "#F7F9FA",   # inputs, alternating rows, table headers
    "surface3":  "#EEF1F4",   # navigation rail
    "line":      "#D9E0E6",   # structural borders
    "lineSoft":  "#E8ECF0",   # row separators
    "ink":       "#16202A",   # primary text
    "ink2":      "#485966",   # secondary text, labels
    "ink3":      "#788895",   # tertiary text, hints, disabled
    "accent":    "#0F6CBD",
    "accentHv":  "#0C5B9F",   # accent hover
    "accentSf":  "#E8F1FA",   # accent tint surface
    "accentLn":  "#A8CBE9",   # accent tint border
    "good":      "#0E7C4A",
    "goodSf":    "#E7F4ED",
    "goodLn":    "#A5D4BC",
    "bad":       "#C0392F",
    "badSf":     "#FBEDEC",
    "badLn":     "#E7B4AF",
    "warn":      "#9A6700",
    "warnSf":    "#FDF4E3",
    "menubar":   "#F7F9FA",
}

DARK: Dict[str, str] = {
    "bg":        "#141A1F",   # deep slate — deliberately not black
    "surface":   "#1B232A",
    "surface2":  "#1F2830",
    "surface3":  "#232D36",
    "line":      "#29333C",
    "lineSoft":  "#222B33",
    "ink":       "#E6ECF1",
    "ink2":      "#A1B1BD",
    "ink3":      "#6C7E8B",
    "accent":    "#4CABE8",
    "accentHv":  "#6BBCF0",
    "accentSf":  "#172938",
    "accentLn":  "#2C4F6C",
    "good":      "#3BD98A",
    "goodSf":    "#102B20",
    "goodLn":    "#1E5540",
    "bad":       "#FF6B5E",
    "badSf":     "#321A18",
    "badLn":     "#6B322C",
    "warn":      "#E3B341",
    "warnSf":    "#2C2312",
    "menubar":   "#171E24",
}

# --------------------------------------------------------------------------
# Metrics — the density decisions live here, not scattered through the UI
# --------------------------------------------------------------------------

# Geometry follows the softer Fluent register: rounded panels and controls,
# with room to breathe. The application chrome (menu bar, grouped toolbar,
# banded grids, multi-pane status bar) stays in the professional register.
RADIUS_PANEL = 8
RADIUS_CTL = 5
RADIUS_CHIP = 4
RADIUS_PILL = 999   # status indicator only

ROW_HEIGHT = 28
CONTROL_HEIGHT = 32
TOOLBAR_HEIGHT = 44
RAIL_WIDTH = 214

UI_FONT = '"Segoe UI Variable Text", "Segoe UI", sans-serif'
DATA_FONT = '"Cascadia Mono", "Consolas", monospace'

# Point sizes (Qt uses pt for fonts; these match the mockup's px sizes at 96 DPI)
FONT_PT = 10
FONT_PT_SMALL = 9

# Status indicator glow. QSS has no box-shadow, so gui_qt applies this via a
# QGraphicsDropShadowEffect; the radius lives here so all geometry is in one file.
LED_GLOW_RADIUS = 16


def resolve_data_font() -> str:
    """
    Returns the first available monospace family.

    Cascadia Mono ships with Windows 11 and recent Windows 10, but is absent
    on older builds. Consolas is present on every supported Windows version.
    """
    try:
        from PySide6.QtGui import QFontDatabase
        families = set(QFontDatabase.families())
        for candidate in ("Cascadia Mono", "Cascadia Code", "Consolas"):
            if candidate in families:
                return candidate
    except Exception:
        pass
    return "Consolas"


def palette_for_scheme(app=None) -> Dict[str, str]:
    """
    Returns DARK or LIGHT according to the operating system colour scheme.

    Falls back to DARK when the scheme cannot be determined, which matches the
    application's historical appearance.
    """
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication
        app = app or QGuiApplication.instance()
        if app is not None:
            scheme = app.styleHints().colorScheme()
            if scheme == Qt.ColorScheme.Light:
                return LIGHT
            if scheme == Qt.ColorScheme.Dark:
                return DARK
    except Exception:
        pass
    return DARK


# --------------------------------------------------------------------------
# Stylesheet
# --------------------------------------------------------------------------

def build_qss(p: Dict[str, str]) -> str:
    """Builds the complete application stylesheet from a palette dict."""
    mono = resolve_data_font()

    return f"""
/* ===== base ============================================================ */
QWidget {{
    background-color: {p['bg']};
    color: {p['ink']};
    font-family: {UI_FONT};
    font-size: {FONT_PT}pt;
}}
QMainWindow, QDialog {{ background-color: {p['bg']}; }}
QLabel {{ background: transparent; color: {p['ink']}; }}
QLabel[role="caption"] {{ color: {p['ink3']}; font-size: {FONT_PT_SMALL}pt; }}
QLabel[role="fieldLabel"] {{ color: {p['ink2']}; font-size: {FONT_PT_SMALL}pt; font-weight: 600; }}
QToolTip {{
    background-color: {p['surface']};
    color: {p['ink']};
    border: 1px solid {p['line']};
    padding: 4px 7px;
}}

/* ===== menu bar ======================================================== */
QMenuBar {{
    background-color: {p['menubar']};
    color: {p['ink2']};
    border-bottom: 1px solid {p['line']};
    padding: 1px 2px;
}}
QMenuBar::item {{ background: transparent; padding: 4px 10px; }}
QMenuBar::item:selected {{ background-color: {p['accentSf']}; color: {p['accent']}; }}
QMenu {{
    background-color: {p['surface']};
    border: 1px solid {p['line']};
    padding: 4px;
}}
QMenu::item {{ padding: 5px 26px 5px 22px; border-radius: {RADIUS_CHIP}px; }}
QMenu::item:selected {{ background-color: {p['accentSf']}; color: {p['accent']}; }}
QMenu::item:disabled {{ color: {p['ink3']}; }}
QMenu::separator {{ height: 1px; background: {p['lineSoft']}; margin: 4px 8px; }}

/* ===== toolbar ========================================================= */
QToolBar {{
    background-color: {p['surface']};
    border: none;
    border-bottom: 1px solid {p['line']};
    padding: 5px 8px;
    spacing: 5px;
}}
QToolBar::separator {{
    width: 1px;
    background: {p['line']};
    margin: 3px 5px;
}}

/* ===== buttons ========================================================= */
QPushButton {{
    background-color: {p['surface']};
    color: {p['ink']};
    border: 1px solid {p['line']};
    border-radius: {RADIUS_CTL}px;
    padding: 4px 11px;
    min-height: {CONTROL_HEIGHT - 10}px;
    font-weight: 500;
}}
QPushButton:hover {{ background-color: {p['surface2']}; border-color: {p['ink3']}; }}
QPushButton:pressed {{ background-color: {p['surface3']}; }}
QPushButton:disabled {{ color: {p['ink3']}; border-color: {p['lineSoft']}; background-color: {p['surface2']}; }}

QPushButton[kind="accent"] {{
    background-color: {p['accent']};
    color: #FFFFFF;
    border: 1px solid {p['accentHv']};
    font-weight: 600;
}}
QPushButton[kind="accent"]:hover {{ background-color: {p['accentHv']}; }}
QPushButton[kind="accent"]:disabled {{
    background-color: {p['surface2']}; color: {p['ink3']}; border-color: {p['lineSoft']};
}}

QPushButton[kind="danger"] {{
    background-color: {p['badSf']};
    color: {p['bad']};
    border: 1px solid {p['badLn']};
    font-weight: 600;
}}
QPushButton[kind="danger"]:hover {{ border-color: {p['bad']}; }}
QPushButton[kind="danger"]:disabled {{
    background-color: {p['surface2']}; color: {p['ink3']}; border-color: {p['lineSoft']};
}}

QPushButton[kind="ghost"] {{ background: transparent; border-color: transparent; color: {p['ink2']}; }}
QPushButton[kind="ghost"]:hover {{ background-color: {p['surface2']}; border-color: {p['line']}; }}

/* segmented control — checkable buttons in an exclusive group */
QPushButton[kind="segment"] {{
    background-color: {p['surface2']};
    color: {p['ink2']};
    border: 1px solid {p['line']};
    border-radius: {RADIUS_CTL}px;
    padding: 4px 12px;
    font-weight: 500;
}}
QPushButton[kind="segment"]:checked {{
    background-color: {p['accentSf']};
    color: {p['accent']};
    border-color: {p['accentLn']};
    font-weight: 650;
}}

/* ===== inputs ========================================================== */
QLineEdit, QSpinBox, QComboBox {{
    background-color: {p['surface2']};
    color: {p['ink']};
    border: 1px solid {p['line']};
    border-bottom: 1px solid {p['ink3']};
    border-radius: {RADIUS_CTL}px;
    padding: 3px 8px;
    min-height: {CONTROL_HEIGHT - 10}px;
    selection-background-color: {p['accent']};
    selection-color: #FFFFFF;
}}
QLineEdit, QSpinBox {{ font-family: "{mono}"; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{
    background-color: {p['surface']};
    border-bottom: 2px solid {p['accent']};
    padding-bottom: 2px;
}}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{ color: {p['ink3']}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox::down-arrow {{
    image: none;
    border-left: 3px solid transparent;
    border-right: 3px solid transparent;
    border-top: 4px solid {p['ink3']};
    margin-right: 6px;
}}
QComboBox QAbstractItemView {{
    background-color: {p['surface']};
    color: {p['ink']};
    border: 1px solid {p['line']};
    selection-background-color: {p['accentSf']};
    selection-color: {p['accent']};
    outline: none;
}}
QSpinBox::up-button, QSpinBox::down-button {{ width: 15px; background: {p['surface3']}; border: none; }}

QRadioButton, QCheckBox {{ background: transparent; color: {p['ink']}; spacing: 6px; }}

/* ===== navigation rail ================================================= */
QListWidget#NavRail {{
    background-color: {p['surface3']};
    border: none;
    border-right: 1px solid {p['line']};
    outline: none;
    padding: 5px 5px;
}}
QListWidget#NavRail::item {{
    color: {p['ink2']};
    border: 1px solid transparent;
    border-radius: {RADIUS_CTL}px;
    padding: 5px 8px;
    margin: 1px 0;
}}
QListWidget#NavRail::item:hover {{ background-color: {p['surface2']}; }}
QListWidget#NavRail::item:selected {{
    background-color: {p['surface']};
    color: {p['ink']};
    border: 1px solid {p['line']};
    border-left: 2px solid {p['accent']};
    font-weight: 600;
}}
QListWidget#NavRail::item:disabled {{
    color: {p['ink3']};
    background: transparent;
    font-size: {FONT_PT_SMALL}pt;
    font-weight: 650;
}}

/* ===== panels ========================================================== */
QFrame#Panel {{
    background-color: {p['surface']};
    border: 1px solid {p['line']};
    border-radius: {RADIUS_PANEL}px;
}}
QFrame#PanelGood {{
    background-color: {p['surface']};
    border: 1px solid {p['line']};
    border-left: 3px solid {p['good']};
    border-radius: {RADIUS_PANEL}px;
}}
QFrame#PanelBad {{
    background-color: {p['surface']};
    border: 1px solid {p['line']};
    border-left: 3px solid {p['bad']};
    border-radius: {RADIUS_PANEL}px;
}}
QFrame#Tile {{
    background-color: {p['surface']};
    border: 1px solid {p['line']};
    border-radius: {RADIUS_PANEL}px;
}}
QLabel[role="panelTitle"] {{
    color: {p['ink2']};
    font-size: {FONT_PT_SMALL}pt;
    font-weight: 650;
    letter-spacing: 1px;
}}
QLabel[role="tileKey"] {{ color: {p['ink3']}; font-size: {FONT_PT_SMALL}pt; font-weight: 620; }}
QLabel[role="tileValue"] {{ color: {p['ink']}; font-family: "{mono}"; font-size: 15pt; font-weight: 600; }}
QLabel[role="tileValueGood"] {{ color: {p['good']}; font-family: "{mono}"; font-size: 15pt; font-weight: 600; }}
QLabel[role="tileUnit"] {{ color: {p['ink3']}; font-size: {FONT_PT_SMALL}pt; font-weight: 500; }}
QFrame[role="hline"] {{ background: {p['lineSoft']}; border: none; max-height: 1px; }}

/* ===== status indicator (LED) ========================================== */
QLabel#StatusLed {{
    border-radius: {RADIUS_PILL}px;
    padding: 6px 14px;
    font-size: {FONT_PT_SMALL}pt;
    font-weight: 600;
}}
QLabel#StatusLed[state="running"] {{
    color: {p['good']};
    background-color: {p['goodSf']};
    border: 1px solid {p['goodLn']};
}}
QLabel#StatusLed[state="stopped"] {{
    color: {p['ink3']};
    background-color: {p['surface2']};
    border: 1px solid {p['line']};
}}

/* ===== data grids ====================================================== */
QTableWidget, QTableView {{
    background-color: {p['surface']};
    alternate-background-color: {p['surface2']};
    color: {p['ink']};
    border: 1px solid {p['line']};
    border-radius: {RADIUS_PANEL}px;
    gridline-color: transparent;
    outline: none;
    selection-background-color: {p['accentSf']};
    selection-color: {p['ink']};
}}
QTableWidget::item {{ padding: 2px 6px; border-bottom: 1px solid {p['lineSoft']}; }}
QTableWidget::item:selected {{ background-color: {p['accentSf']}; color: {p['ink']}; }}
QHeaderView {{ background-color: {p['surface2']}; }}
QHeaderView::section {{
    background-color: {p['surface2']};
    color: {p['ink2']};
    padding: 4px 7px;
    border: none;
    border-bottom: 1px solid {p['line']};
    border-right: 1px solid {p['lineSoft']};
    font-weight: 620;
    font-size: {FONT_PT_SMALL}pt;
}}
QHeaderView::section:last {{ border-right: none; }}
QHeaderView::down-arrow, QHeaderView::up-arrow {{ width: 8px; height: 8px; }}
QTableCornerButton::section {{ background-color: {p['surface2']}; border: none; }}

QListWidget, QTextEdit, QPlainTextEdit {{
    background-color: {p['surface']};
    color: {p['ink']};
    border: 1px solid {p['line']};
    border-radius: {RADIUS_PANEL}px;
    outline: none;
}}
QTextEdit, QPlainTextEdit {{ font-family: "{mono}"; }}
QListWidget::item {{ padding: 3px 7px; border-radius: {RADIUS_CHIP}px; }}
QListWidget::item:selected {{ background-color: {p['accentSf']}; color: {p['accent']}; }}

/* ===== splitter / dock ================================================= */
QSplitter::handle {{ background-color: {p['line']}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}
QDockWidget {{ titlebar-close-icon: none; titlebar-normal-icon: none; color: {p['ink2']}; }}
QDockWidget::title {{
    background-color: {p['surface2']};
    border-bottom: 1px solid {p['line']};
    padding: 4px 8px;
    font-size: {FONT_PT_SMALL}pt;
    font-weight: 650;
}}

/* ===== slider ========================================================== */
QSlider::groove:horizontal {{ height: 3px; background: {p['line']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {p['accent']}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {p['accent']};
    border: 1px solid {p['accentHv']};
    width: 11px;
    height: 11px;
    margin: -5px 0;
    border-radius: {RADIUS_CHIP}px;
}}
QSlider::handle:horizontal:hover {{ background: {p['accentHv']}; }}

/* ===== scrollbars ====================================================== */
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {p['line']}; border-radius: 5px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {p['ink3']}; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {p['line']}; border-radius: 5px; min-width: 28px; }}
QScrollBar::handle:horizontal:hover {{ background: {p['ink3']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ===== status bar ====================================================== */
QStatusBar {{
    background-color: {p['surface']};
    color: {p['ink3']};
    border-top: 1px solid {p['line']};
    font-size: {FONT_PT_SMALL}pt;
}}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{
    color: {p['ink3']};
    padding: 0 9px;
    border-right: 1px solid {p['lineSoft']};
}}
QStatusBar QLabel[role="statusEnd"] {{
    border-right: none;
    font-family: "{mono}";
}}
"""
