# server/main.py
import os, sys

# ★ common.py가 있는 프로젝트 루트(~/<repo>/)를 가장 먼저 sys.path에 추가
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from PySide6.QtWidgets import QApplication
from ui import ServerWindow

def main():
    app = QApplication(sys.argv)

    qss_path = os.path.join(os.path.dirname(__file__), "server.qss")
    try:
        with open(qss_path, "r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())
    except Exception:
        pass

    w = ServerWindow()
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
