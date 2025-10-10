# client/main.py
import os
import sys

# ★ 프로젝트 루트(~/<repo>/)를 sys.path에 추가
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from PySide6.QtWidgets import QApplication, QDialog
from ui import ConnectDialog, ClientWindow

def main():
    app = QApplication(sys.argv)

    qss_path = os.path.join(os.path.dirname(__file__), "client.qss")
    try:
        with open(qss_path, "r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())
    except Exception:
        pass

    default_ip = sys.argv[1] if len(sys.argv) > 1 else None
    dlg = ConnectDialog(default_ip=default_ip)
    if dlg.exec() != QDialog.Accepted:
        sys.exit(0)

    server_ip = dlg.ed_ip.text().strip()
    w = ClientWindow(server_ip)
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
