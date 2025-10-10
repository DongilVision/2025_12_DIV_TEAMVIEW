# server/ui.py
import time
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QLabel, QVBoxLayout, QHBoxLayout, QPushButton, QStatusBar
)
from PySide6.QtGui import QPixmap, QColor, QPainter, QBrush

from net import VideoServer, ControlServer, FileServer
from utils import hms
from common import DEFAULT_HOST, VIDEO_PORT, CONTROL_PORT, FILE_PORT, get_local_ip

def make_dot_pix(color: QColor, d: int = 10) -> QPixmap:
    pm = QPixmap(d, d); pm.fill(Qt.transparent)
    p = QPainter(pm); p.setRenderHint(QPainter.Antialiasing, True)
    p.setBrush(QBrush(color)); p.setPen(Qt.NoPen)
    p.drawEllipse(0, 0, d, d); p.end()
    return pm

class ServerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("원격화면 서버")
        self.setFixedSize(400, 230)

        self.start_ts = time.time()
        self.video_conn_start_ts: float = 0.0
        self.last_client_ip: str = "-"
        self.ip = get_local_ip()

        # 네트워크 스레드
        self.video = VideoServer(DEFAULT_HOST, VIDEO_PORT)
        self.ctrl  = ControlServer(DEFAULT_HOST, CONTROL_PORT)  # UI표시는 안 하지만 입력 처리를 위해 구동
        self.files = FileServer(DEFAULT_HOST, FILE_PORT)

        # ===== UI =====
        title = QLabel("원격 서버 실행 중", alignment=Qt.AlignCenter)
        title.setObjectName("Title")

        self.lbl_ip   = QLabel(f"서버 IP: {self.ip}", alignment=Qt.AlignCenter)
        self.lbl_up   = QLabel("서버 실행 시간: 00:00:00", alignment=Qt.AlignCenter)

        # 영상 연결 점등 + 끊기 버튼
        self.dot = QLabel(); self.dot.setFixedSize(12,12)
        self._dot_red   = make_dot_pix(QColor("#e11d48"))
        self._dot_green = make_dot_pix(QColor("#10b981"))
        self.dot.setPixmap(self._dot_red)

        self.btn_kick = QPushButton("연결 끊기")
        self.btn_kick.setObjectName("DisconnectBtn")
        self.btn_kick.setEnabled(False)
        self.btn_kick.clicked.connect(self.on_kick)

        row_top = QHBoxLayout(); row_top.setContentsMargins(0,0,0,0); row_top.setSpacing(10)
        row_top.addStretch(1)
        row_top.addWidget(QLabel("영상 연결"))
        row_top.addWidget(self.dot)
        row_top.addSpacing(12)
        row_top.addWidget(self.btn_kick)
        row_top.addStretch(1)
        w_top = QWidget(); w_top.setLayout(row_top)

        self.lbl_elapsed     = QLabel("연결 경과: --:--:--", alignment=Qt.AlignCenter)
        self.lbl_client_ip   = QLabel("클라이언트 IP: -", alignment=Qt.AlignCenter)
        self.lbl_video_count = QLabel("연결된 클라이언트 수: 0", alignment=Qt.AlignCenter)

        v = QVBoxLayout()
        v.setContentsMargins(16,16,12,10); v.setSpacing(8)
        v.addWidget(title)
        v.addWidget(self.lbl_ip)
        v.addWidget(self.lbl_up)
        v.addWidget(w_top)
        v.addWidget(self.lbl_elapsed)
        v.addWidget(self.lbl_client_ip)
        # v.addWidget(self.lbl_video_count)

        wrap = QWidget(); wrap.setLayout(v)
        self.setCentralWidget(wrap)
        self.setStatusBar(QStatusBar(self))

        # ===== 시그널 연결 =====
        self.video.sig_conn_changed.connect(self.on_video_conn_changed)
        self.video.sig_last_client.connect(self.on_last_client)
        self.video.sig_conn_start.connect(self.on_video_conn_start)

        # ===== 타이머 =====
        self.timer = QTimer(self); self.timer.timeout.connect(self._on_tick); self.timer.start(1000)

    # ---- 버튼: 강제 끊기 (영상+제어 모두 종료) ----
    def on_kick(self):
        self.btn_kick.setEnabled(False)
        try: self.video.force_disconnect_all()
        except Exception: pass
        try: self.ctrl.force_disconnect_all()
        except Exception: pass


    # ---- 신호 핸들러 ----
    def on_video_conn_changed(self, n: int):
        self.lbl_video_count.setText(f"연결된 클라이언트 수: {n}")
        ok = n > 0
        self.dot.setPixmap(self._dot_green if ok else self._dot_red)
        self.btn_kick.setEnabled(ok)
        if not ok:
            # 모두 끊김 → 표시 리셋
            self.video_conn_start_ts = 0.0
            self.last_client_ip = "-"
            self.lbl_elapsed.setText("연결 경과: --:--:--")
            self.lbl_client_ip.setText("클라이언트 IP: -")

    def on_last_client(self, ip: str):
        if ip:
            self.last_client_ip = ip
            self.lbl_client_ip.setText(f"클라이언트 IP: {ip}")

    def on_video_conn_start(self, ts: float):
        self.video_conn_start_ts = float(ts or 0.0)

    # ---- 타이머 ----
    def _on_tick(self):
        self.lbl_up.setText(f"서버 실행 시간: {hms(int(time.time() - self.start_ts))}")
        if self.video_conn_start_ts > 0:
            self.lbl_elapsed.setText(f"연결 경과: {hms(int(time.time() - self.video_conn_start_ts))}")

    # ---- 생명주기 ----
    def showEvent(self, e):
        super().showEvent(e)
        self.video.start(); self.ctrl.start(); self.files.start()

    def closeEvent(self, e):
        try: self.video.stop(); self.video.wait(1500)
        except Exception: pass
        try: self.ctrl.stop();  self.ctrl.wait(1500)
        except Exception: pass
        try: self.files.stop(); self.files.wait(1500)
        except Exception: pass
        super().closeEvent(e)
