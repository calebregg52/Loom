BG = "#0f1115"
PANEL = "#151821"
RAISED = "#1c202b"
BORDER = "#262b38"
TEXT = "#e8eaf0"
MUTED = "#8b91a3"
ACCENT = "#7c6cff"
ACCENT_HOVER = "#9286ff"

STYLESHEET = f"""
* {{ font-family: "Segoe UI", "Inter", "Helvetica Neue", Arial, sans-serif; font-size: 13px; color: {TEXT}; }}
QMainWindow, QDialog, #content {{ background: {BG}; }}
#sidebar {{ background: {PANEL}; border-right: 1px solid {BORDER}; }}
#wordmark {{ font-size: 24px; font-weight: 700; letter-spacing: 1px; }}
#wordmarkDot {{ color: {ACCENT}; font-size: 24px; font-weight: 700; }}
#sectionLabel {{ color: {MUTED}; font-size: 11px; font-weight: 600; letter-spacing: 1.5px; }}
#muted, QLabel[muted="true"] {{ color: {MUTED}; }}
#pageTitle {{ font-size: 28px; font-weight: 700; }}
#pageSubtitle {{ color: {MUTED}; font-size: 13px; }}
#card {{ background: {RAISED}; border: 1px solid {BORDER}; border-radius: 12px; }}
#statValue {{ font-size: 26px; font-weight: 700; }}
#statLabel {{ color: {MUTED}; font-size: 12px; }}

QPushButton {{ background: {RAISED}; border: 1px solid {BORDER}; border-radius: 8px; padding: 8px 14px; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: {MUTED}; }}
QPushButton#primary {{ background: {ACCENT}; border: none; color: white; font-weight: 600; }}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#primary:disabled {{ background: {BORDER}; color: {MUTED}; }}
QPushButton#nav {{ background: transparent; border: none; text-align: left; padding: 8px 12px; border-radius: 8px; }}
QPushButton#nav:hover {{ background: {RAISED}; }}
QPushButton#nav:checked {{ background: {RAISED}; color: white; font-weight: 600; }}

QLineEdit {{ background: {RAISED}; border: 1px solid {BORDER}; border-radius: 8px; padding: 8px 12px; selection-background-color: {ACCENT}; }}
QLineEdit:focus {{ border-color: {ACCENT}; }}

QComboBox, QSpinBox {{ background: {RAISED}; border: 1px solid {BORDER}; border-radius: 8px; padding: 6px 10px; min-height: 20px; }}
QComboBox:hover, QSpinBox:hover {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {RAISED}; border: 1px solid {BORDER}; selection-background-color: {ACCENT}; outline: none; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; border: none; background: transparent; }}
QListWidget::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {MUTED}; background: transparent; }}
QListWidget::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: none; }}
QTreeWidget {{ background: transparent; border: none; outline: none; show-decoration-selected: 0; }}
QTreeWidget::item {{ padding: 6px 6px; border-radius: 8px; }}
QTreeWidget::item:hover {{ background: {RAISED}; }}
QTreeWidget::item:selected {{ background: {RAISED}; color: white; }}
QTreeWidget::branch {{ background: transparent; }}
QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ padding: 7px 10px; border-radius: 8px; }}
QListWidget::item:hover {{ background: {RAISED}; }}
QListWidget::item:selected {{ background: {RAISED}; color: white; }}

QTableView {{ background: {BG}; border: none; gridline-color: transparent; outline: none;
             selection-background-color: {RAISED}; selection-color: white; alternate-background-color: #12141a; }}
QTableView::item {{ padding: 6px 8px; border: none; }}
QHeaderView::section {{ background: {BG}; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER};
                        padding: 8px; font-size: 11px; font-weight: 600; letter-spacing: 1px; }}

QProgressBar {{ background: {RAISED}; border: none; border-radius: 3px; height: 6px; max-height: 6px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {MUTED}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 5px; min-width: 30px; }}
QToolTip {{ background: {RAISED}; color: {TEXT}; border: 1px solid {BORDER}; padding: 6px; }}
"""
