APP_STYLESHEET = """
QMainWindow { background-color: #1e1e2e; }
QWidget { background-color: #24283b; color: #c0caf5; font-size: 12px; }
QTabWidget::pane { border: 1px solid #414868; background: #24283b; }
QTabBar::tab { background: #3b4261; color: #a9b1d6; padding: 8px 16px; margin: 2px; border-top-left-radius: 4px; border-top-right-radius: 4px; }
QTabBar::tab:selected { background: #414868; border-bottom: 2px solid #7dcfff; }
QPushButton { background-color: #565f89; border: none; padding: 8px 16px; border-radius: 4px; color: white; }
QPushButton:hover { background-color: #6d76a9; }
QListWidget, QLineEdit, QComboBox, QSpinBox, QTextEdit { background-color: #3b4261; border: 1px solid #414868; border-radius: 4px; padding: 5px; selection-background-color: #7aa2f7; }
QGroupBox { border: 1px solid #7aa2f7; border-radius: 5px; margin-top: 10px; padding-top: 15px; font-weight: bold; color: #a9b1d6; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
QTableWidget { gridline-color: #414868; }
QHeaderView::section { background-color: #3b4261; color: #c0caf5; border: 1px solid #414868; }
QProgressBar { border: 1px solid #414868; border-radius: 4px; text-align: center; background-color: #3b4261; color: white; }
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2ECC71, stop:1 #27AE60);
}
"""