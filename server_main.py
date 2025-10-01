# server_main.py
import sys, time, socket, select, threading, struct, json, ctypes
import numpy as np
import cv2
from mss import mss

from PySide6.QtCore import Qt, QTimer, QThread, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMainWindow, QLabel, QWidget, QVBoxLayout, QSystemTrayIcon, QMenu

from common import DEFAULT_HOST, VIDEO_PORT, CONTROL_PORT, FRAME_FPS, JPEG_QUALITY, get_local_ip

# ====== Windows 입력 주입 유틸 ======
user32 = ctypes.windll.user32
SetCursorPos   = user32.SetCursorPos
mouse_event    = user32.mouse_event
keybd_event    = user32.keybd_event

# 마우스 플래그
MOUSEEVENTF_MOVE      = 0x0001
MOUSEEVENTF_LEFTDOWN  = 0x0002
MOUSEEVENTF_LEFTUP    = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP   = 0x0010
MOUSEEVENTF_MIDDLEDOWN= 0x0020
MOUSEEVENTF_MIDDLEUP  = 0x0040
MOUSEEVENTF_WHEEL     = 0x0800

# 키 플래그
KEYEVENTF_KEYUP = 0x0002

# 간단 VK 매핑(필요 시 확장)
VK = {
    "ESC": 0x1B, "ENTER": 0x0D, "BACK": 0x08, "TAB": 0x09,
    "LEFT": 0x25, "UP": 0x26, "RIGHT": 0x27, "DOWN": 0x28,
    "SPACE": 0x20, "DELETE": 0x2E, "HOME":0x24, "END":0x23, "PGUP":0x21, "PGDN":0x22,
}
def to_vk(key: str) -> int:
    if len(key) == 1:
        ch = key.upper()
        if "A" <= ch <= "Z": return ord(ch)
        if "0" <= ch <= "9": return ord(ch)
    return VK.get(key.upper(), 0)

# ====== 영상 서버 ======
class VideoServer(QThread):
    sig_conn_changed = Signal(int)
    sig_res_changed  = Signal(int, int)  # (w,h)

    def __init__(self, host: str, port: int):
        super().__init__()
        self.host = host; self.port = port
        self._stop = threading.Event()
        self._clients = set(); self._lock = threading.Lock()

    def run(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port)); srv.listen(5); srv.setblocking(False)

        sct = mss()
        mon = sct.monitors[1]  # 주 모니터
        last_frame_ts = 0.0
        frame_interval = 1.0 / max(1, FRAME_FPS)

        try:
            while not self._stop.is_set():
                rlist, _, _ = select.select([srv] + list(self._clients), [], [], 0.01)
                for s in rlist:
                    if s is srv:
                        try:
                            conn,_ = srv.accept()
                            conn.setblocking(False)
                            with self._lock:
                                self._clients.add(conn)
                                self.sig_conn_changed.emit(len(self._clients))
                        except BlockingIOError:
                            pass
                    else:
                        try:
                            data = s.recv(1)
                            if not data:
                                self._drop_client(s)
                        except (BlockingIOError, ConnectionResetError, OSError):
                            self._drop_client(s)

                now = time.time()
                if now - last_frame_ts >= frame_interval:
                    frame = np.array(sct.grab(mon))[:, :, :3]
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                    h, w, _ = frame.shape
                    self.sig_res_changed.emit(w, h)
                    ok, enc = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
                    if ok:
                        blob = enc.tobytes()
                        # 헤더: [len(4)][w(4)][h(4)] + data
                        header = struct.pack(">III", len(blob), w, h)
                        packet = header + blob
                        drop = []
                        with self._lock:
                            for c in self._clients:
                                try: c.sendall(packet)
                                except OSError: drop.append(c)
                            for dc in drop: self._drop_client(dc)
                    last_frame_ts = now
        finally:
            with self._lock:
                for c in list(self._clients):
                    try: c.close()
                    except: pass
                self._clients.clear()
            try: srv.close()
            except: pass

    def _drop_client(self, s):
        try: s.close()
        except: pass
        with self._lock:
            if s in self._clients:
                self._clients.remove(s)
                self.sig_conn_changed.emit(len(self._clients))

    def stop(self):
        self._stop.set()

# ====== 제어 서버 ======
class ControlServer(QThread):
    """클라이언트로부터 JSON 제어 메시지 수신 → 서버 PC에 입력 주입."""
    sig_ctrl_conn = Signal(bool)

    def __init__(self, host: str, port: int):
        super().__init__()
        self.host = host; self.port = port
        self._stop = threading.Event()
        self._sock = None
        self._conns = set()
        self._lock = threading.Lock()

    def run(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port)); srv.listen(2); srv.setblocking(False)
        try:
            while not self._stop.is_set():
                rlist, _, _ = select.select([srv] + list(self._conns), [], [], 0.02)
                for s in rlist:
                    if s is srv:
                        try:
                            c,_=srv.accept(); c.setblocking(False)
                            with self._lock: self._conns.add(c)
                            self.sig_ctrl_conn.emit(True)
                        except BlockingIOError:
                            pass
                    else:
                        try:
                            # 길이프리픽스(4바이트) JSON
                            header = s.recv(4, socket.MSG_PEEK)
                            if not header:
                                self._drop(s); continue
                            if len(header) < 4: continue
                            ln = struct.unpack(">I", header)[0]
                            total = 4 + ln
                            if len(s.recv(0, socket.MSG_PEEK)) < 0: pass  # no-op
                            if len(s.recv(0, socket.MSG_PEEK)) is not None: pass
                            buf = s.recv(total)
                            if not buf:
                                self._drop(s); continue
                            # buf = [4바이트][json]
                            ln = struct.unpack(">I", buf[:4])[0]
                            body = buf[4:4+ln]
                            msg = json.loads(body.decode("utf-8", errors="ignore"))
                            self._handle_msg(msg)
                        except BlockingIOError:
                            pass
                        except Exception:
                            self._drop(s)
        finally:
            with self._lock:
                for c in list(self._conns):
                    try: c.close()
                    except: pass
                self._conns.clear()
            try: srv.close()
            except: pass

    def _drop(self, s):
        try: s.close()
        except: pass
        with self._lock:
            if s in self._conns:
                self._conns.remove(s)
                self.sig_ctrl_conn.emit(False)

    # ---- 실제 입력 주입 ----
    def _handle_msg(self, m: dict):
        t = m.get("t")
        if t == "mouse_move":
            x = int(m.get("x", 0)); y = int(m.get("y", 0))
            SetCursorPos(x, y)
        elif t == "mouse_down":
            btn = m.get("btn","left")
            if btn=="left":  mouse_event(MOUSEEVENTF_LEFTDOWN, 0,0,0,0)
            elif btn=="right": mouse_event(MOUSEEVENTF_RIGHTDOWN,0,0,0,0)
            elif btn=="middle":mouse_event(MOUSEEVENTF_MIDDLEDOWN,0,0,0,0)
        elif t == "mouse_up":
            btn = m.get("btn","left")
            if btn=="left":  mouse_event(MOUSEEVENTF_LEFTUP, 0,0,0,0)
            elif btn=="right": mouse_event(MOUSEEVENTF_RIGHTUP,0,0,0,0)
            elif btn=="middle":mouse_event(MOUSEEVENTF_MIDDLEUP,0,0,0,0)
        elif t == "mouse_wheel":
            delta = int(m.get("delta", 0))  # 120 단위
            mouse_event(MOUSEEVENTF_WHEEL, 0,0,delta,0)
        elif t == "key_down":
            vk = to_vk(m.get("key",""))
            if vk: keybd_event(vk, 0, 0, 0)
        elif t == "key_up":
            vk = to_vk(m.get("key",""))
            if vk: keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)

    def stop(self):
        self._stop.set()

# ====== 서버 윈도우 ======
class ServerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("원격화면 서버")
        self.setFixedSize(300, 170)
        self.start_ts = time.time()
        self.ip = get_local_ip()

        self.video = VideoServer(DEFAULT_HOST, VIDEO_PORT)
        self.ctrl  = ControlServer(DEFAULT_HOST, CONTROL_PORT)
        self.video.sig_conn_changed.connect(self.on_video_conn)
        self.video.sig_res_changed.connect(self.on_res)
        self.ctrl.sig_ctrl_conn.connect(self.on_ctrl_conn)

        self.lbl_ip = QLabel(f"서버 IP: {self.ip}  V:{VIDEO_PORT} / C:{CONTROL_PORT}", alignment=Qt.AlignCenter)
        self.lbl_res = QLabel("원격 해상도: - x -", alignment=Qt.AlignCenter)
        self.lbl_uptime = QLabel("연결 시간: 00:00:00", alignment=Qt.AlignCenter)
        self.lbl_conns = QLabel("영상 연결 수: 0 | 제어 연결: -", alignment=Qt.AlignCenter)

        v = QVBoxLayout()
        v.addWidget(QLabel("서버 실행 중", alignment=Qt.AlignCenter))
        v.addWidget(self.lbl_ip)
        v.addWidget(self.lbl_res)
        v.addWidget(self.lbl_uptime)
        v.addWidget(self.lbl_conns)
        wrap = QWidget(); wrap.setLayout(v)
        self.setCentralWidget(wrap)

        self.timer = QTimer(self); self.timer.timeout.connect(self.update_uptime); self.timer.start(500)

        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(QIcon.fromTheme("application-exit"))
        menu = QMenu(); act_quit = QAction("종료", self); act_quit.triggered.connect(self.close)
        menu.addAction(act_quit); self.tray.setContextMenu(menu); self.tray.show()

    def on_video_conn(self, n:int): self._update_conn_label(n, None)
    def on_ctrl_conn(self, ok:bool): self._update_conn_label(None, ok)
    def on_res(self, w:int, h:int): self.lbl_res.setText(f"원격 해상도: {w} x {h}")

    def _update_conn_label(self, vid_cnt, ctrl_ok):
        txt = self.lbl_conns.text()
        # 현재 상태 파싱 없이 간단 덮어쓰기
        if vid_cnt is None:
            parts = self.lbl_conns.text().split("|")
            left = parts[0].strip()
            self.lbl_conns.setText(f"{left} | 제어 연결: {'OK' if ctrl_ok else '-'}")
        elif ctrl_ok is None:
            parts = self.lbl_conns.text().split("|")
            right = parts[1].strip() if len(parts)>1 else "제어 연결: -"
            self.lbl_conns.setText(f"영상 연결 수: {vid_cnt} | {right}")

    def update_uptime(self):
        elapsed = int(time.time() - self.start_ts)
        h=elapsed//3600; m=(elapsed%3600)//60; s=elapsed%60
        self.lbl_uptime.setText(f"연결 시간: {h:02d}:{m:02d}:{s:02d}")

    def showEvent(self, e):
        super().showEvent(e); self.video.start(); self.ctrl.start()

    def closeEvent(self, e):
        self.video.stop(); self.video.wait(1500)
        self.ctrl.stop();  self.ctrl.wait(1500)
        super().closeEvent(e)

def main():
    app = QApplication(sys.argv)
    w = ServerWindow(); w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
