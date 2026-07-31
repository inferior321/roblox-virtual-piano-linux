"""Colours and the Qt stylesheet.

The palette comes from the instrument itself rather than from anywhere else:
ebony and ivory for the keys, brass for the hardware, and the deep red of
action felt for anything live. Brass is the only bright colour in the program,
so it only ever marks two things - the control you're using and the notes that
are sounding.
"""

INK = "#16130F"        # window background, ebony
PANEL = "#221D18"      # raised surfaces
PANEL_HI = "#2C2620"   # inputs, hover
LINE = "#3A322A"       # hairlines
IVORY = "#EDE7DA"      # primary text, white keys
MUTED = "#9A9086"      # secondary text
AMETHYST = "#C08FFA"      # accent: active control, held note
# The dim shade backs selections and highlights under IVORY text, so it is a
# genuinely darker, quieter purple rather than the accent turned down: at the
# accent's own saturation it would shout behind every selected row.
AMETHYST_DIM = "#6A48A8"
FELT = "#8C3A30"       # live / warning
GREEN = "#6E8A5F"      # in range, ready
EBONY = "#0E0C0A"      # black keys

MONO = "'JetBrains Mono', 'DejaVu Sans Mono', 'Liberation Mono', monospace"
SANS = "'Inter', 'Ubuntu', 'DejaVu Sans', sans-serif"

STYLESHEET = f"""
QWidget {{
    background: {INK};
    color: {IVORY};
    font-family: {SANS};
    font-size: 13px;
}}
QMainWindow, QDialog {{ background: {INK}; }}

QLabel#Title {{
    font-size: 21px;
    font-weight: 600;
    color: {IVORY};
    padding: 0;
}}
QLabel#Subtitle {{
    color: {MUTED};
    font-family: {MONO};
    font-size: 12px;
}}
QLabel#SectionLabel {{
    color: {MUTED};
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 1.4px;
    text-transform: uppercase;
}}
QLabel#Clock {{
    font-family: {MONO};
    font-size: 13px;
    color: {MUTED};
}}
QLabel#Warn {{ color: {FELT}; }}
QLabel#Ok {{ color: {GREEN}; }}

QFrame#Card {{
    background: {PANEL};
    border: 1px solid {LINE};
    border-radius: 6px;
}}
QFrame#Rule {{ background: {LINE}; max-height: 1px; border: none; }}

QPushButton {{
    background: {PANEL_HI};
    border: 1px solid {LINE};
    border-radius: 4px;
    padding: 6px 14px;
    color: {IVORY};
}}
QPushButton:hover {{ border-color: {AMETHYST_DIM}; }}
QPushButton:pressed {{ background: {PANEL}; }}
QPushButton:disabled {{ color: {MUTED}; border-color: {PANEL_HI}; }}
QPushButton#Transport {{
    font-size: 14px;
    font-weight: 600;
    padding: 9px 26px;
    min-width: 108px;
}}
QPushButton#Transport:checked, QPushButton#Transport[live="true"] {{
    background: {AMETHYST};
    border-color: {AMETHYST};
    color: {INK};
}}

QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {{
    background: {PANEL_HI};
    border: 1px solid {LINE};
    border-radius: 4px;
    padding: 5px 8px;
    selection-background-color: {AMETHYST_DIM};
}}
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus {{
    border-color: {AMETHYST};
}}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{
    background: {PANEL_HI};
    border: 1px solid {LINE};
    selection-background-color: {AMETHYST_DIM};
}}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{
    width: 14px; height: 14px;
    border: 1px solid {LINE};
    border-radius: 3px;
    background: {PANEL_HI};
}}
QCheckBox::indicator:checked {{ background: {AMETHYST}; border-color: {AMETHYST}; }}

QSlider::groove:horizontal {{
    height: 4px;
    background: {PANEL_HI};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{ background: {AMETHYST_DIM}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {AMETHYST};
    width: 12px;
    margin: -5px 0;
    border-radius: 6px;
}}

QTreeView {{
    background: {PANEL};
    border: 1px solid {LINE};
    border-radius: 6px;
    outline: none;
    show-decoration-selected: 1;
}}
QTreeView::item {{ padding: 3px 2px; border-radius: 3px; }}
QTreeView::item:hover {{ background: {PANEL_HI}; }}
QTreeView::item:selected {{ background: {AMETHYST_DIM}; color: {IVORY}; }}

QTabWidget::pane {{
    border: 1px solid {LINE};
    border-radius: 6px;
    background: {PANEL};
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    color: {MUTED};
    padding: 7px 16px;
    border: none;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{ color: {IVORY}; border-bottom-color: {AMETHYST}; }}
QTabBar::tab:hover {{ color: {IVORY}; }}

QTableWidget {{
    background: {PANEL};
    border: 1px solid {LINE};
    border-radius: 6px;
    gridline-color: {LINE};
    selection-background-color: {AMETHYST_DIM};
}}
QHeaderView::section {{
    background: {PANEL_HI};
    color: {MUTED};
    border: none;
    border-bottom: 1px solid {LINE};
    padding: 6px;
    font-size: 11px;
    letter-spacing: 1px;
}}

QListWidget {{
    background: {PANEL};
    border: 1px solid {LINE};
    border-radius: 6px;
    outline: none;
}}
QMenu {{
    background: {PANEL};
    color: {IVORY};
    border: 1px solid {LINE};
    padding: 4px;
}}
QMenu::item {{ padding: 6px 22px; border-radius: 3px; }}
QMenu::item:selected {{ background: {AMETHYST_DIM}; color: {IVORY}; }}
QMenu::item:disabled {{ color: {MUTED}; }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: 4px 8px; }}

QListWidget::item {{ padding: 5px 4px; }}
QListWidget::item:selected {{ background: {AMETHYST_DIM}; }}

QStatusBar {{ background: {PANEL}; border-top: 1px solid {LINE}; color: {MUTED}; }}
QStatusBar::item {{ border: none; }}

QScrollBar:vertical {{ background: transparent; width: 9px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {LINE}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {AMETHYST_DIM}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 9px; }}
QScrollBar::handle:horizontal {{ background: {LINE}; border-radius: 4px; min-width: 30px; }}

QToolTip {{
    background: {PANEL_HI};
    color: {IVORY};
    border: 1px solid {AMETHYST_DIM};
    padding: 4px 6px;
}}
"""
