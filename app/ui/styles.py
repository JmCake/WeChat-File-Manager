WINDOW_STYLE = """
QMainWindow, QWidget#workspace {
    background: #f6f7f9;
    color: #202631;
}
QWidget#sidebar {
    background: #ffffff;
    border: 1px solid #e2e7ed;
    border-radius: 9px;
}
QLabel#brand { font-size: 18px; font-weight: 700; color: #182230; }
QLabel#pageTitle { font-size: 23px; font-weight: 700; color: #182230; }
QLabel#taskStatus { color: #526275; }
QLabel[sideHeading="true"] {
    font-size: 11px;
    font-weight: 600;
    color: #718094;
    padding: 7px 7px 3px 7px;
    background: transparent;
}
QPushButton {
    padding: 5px 9px;
    border: 1px solid #d7dde5;
    border-radius: 6px;
    background: #ffffff;
    color: #243143;
}
QPushButton:hover { background: #ecf2f8; }
QPushButton:disabled { background: #edf0f4; color: #647083; }
QPushButton[nav="true"], QPushButton[sideTool="true"] {
    text-align: left;
    padding: 7px 10px;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
}
QPushButton[nav="true"]:hover, QPushButton[sideTool="true"]:hover {
    background: #eef3f9;
}
QPushButton[nav="true"]:checked {
    background: #e5effb;
    color: #164374;
    border-left: 3px solid #2866a7;
    font-weight: 700;
}
QPushButton[kind="primary"]:enabled {
    background: #245d9d;
    color: white;
    border-color: #245d9d;
}
QPushButton[kind="danger"]:enabled {
    background: #fff4ee;
    color: #8c3523;
    border-color: #d8a69a;
}
QLineEdit, QComboBox, QDoubleSpinBox, QTextEdit {
    padding: 4px;
    border: 1px solid #d7dde5;
    border-radius: 5px;
    background: white;
}
QLineEdit:focus, QComboBox:focus, QTextEdit:focus {
    border-color: #4383bf;
}
QTableView, QTreeWidget {
    background: white;
    alternate-background-color: #f7f9fb;
    border: 1px solid #dce2e9;
    border-radius: 5px;
    selection-background-color: #daeafa;
    selection-color: #182230;
}
QTreeWidget::item:hover { background: #edf4fb; }
QHeaderView::section {
    padding: 5px;
    background: #edf1f5;
    border: 0;
    border-right: 1px solid #e0e5ec;
    color: #28374b;
}
"""
