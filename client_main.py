# client_main.py
import sys, time, socket, threading, struct, json
import numpy as np
import cv2

from PySide6.QtCore import Qt, QThread, Signal, QPoint
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget, QLabel, QHBoxLayout, QVBoxLayout, QPushButton, QFrame, QLineEdit, QMessageBox

from common import VIDEO_PORT, CONTROL_PORT

def np_bgr_to_qimage(bgr: np.ndarray) -> QImage:
    h, w, _ = bgr.shape
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    return QImage(rgb.data, w, h, 3*w, QImage.Format_RGB888).copy()

# ---- 영상 수신 ----
class VideoClient(QThread):
    sig_status = Signal(float, int, bool)   # (fps, elapsed_sec, connected)
    sig_frame  = Signal(QImage, int, int)   # (QImage, w, h)

    def __init__(self, host: str, port: int):
        super().__init__()
        self.host = host; self.port = port
        self._stop = threading.Event()
        self._sock = None
        self._connected = False
        self._conn_ts = None
        self._frame_count = 0
        self._last_fps_ts = time.time()

    def run(self):
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(5.0); self._sock.connect((self.host, self.port))
            self._sock.settimeout(0.2)
            self._connected = True; self._conn_ts = time.time()
        except Exception:
            self.sig_status.emit(0.0, 0, False); return

        buf = bytearray()
        expected = None
        meta_wh = None  # (w,h)

        try:
            while not self._stop.is_set():
                try:
                    chunk = self._sock.recv(65536)
                    if not chunk:
                        self._connected = False; break
                    buf += chunk
                except socket.timeout:
                    pass
                except Exception:
                    self._connected = False; break

                # [len(4)][w(4)][h(4)] + data
                while True:
                    if expected is None:
                        if len(buf) >= 12:
                            data_len, w, h = struct.unpack(">III", buf[:12])
                            expected = data_len; meta_wh = (w,h)
                            del buf[:12]
                        else:
                            break
                    if expected is not None and len(buf) >= expected:
                        blob = bytes(buf[:expected]); del buf[:expected]; expected = None
                        arr = np.frombuffer(blob, dtype=np.uint8)
                        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                        if img is not None:
                            qimg = np_bgr_to_qimage(img)
                            self.sig_frame.emit(qimg, meta_wh[0], meta_wh[1])
                            self._frame_count += 1
                    else:
                        break

                now = time.time()
                if now - self._last_fps_ts >= 1.0:
                    fps = float(self._frame_count); self._frame_count = 0; self._last_fps_ts = now
                    elapsed = int(now - (self._conn_ts or now))
                    self.sig_status.emit(fps, elapsed, self._connected)
        finally:
            try:
                if self._sock: self._sock.close()
            except Exception: pass
            self._connected = False
            self.sig_status.emit(0.0, 0, False)

    def stop(self):
        self._stop.set()

# ---- 제어 송신 ----
class ControlClient:
    """길이프리픽스 JSON 송신 전용(간단 동기 송신)."""
    def __init__(self, host:str, port:int):
        self.host=host; self.port=port
        self.sock=None
        self.connect()

    def connect(self):
        try:
            if self.sock: self.sock.close()
        except: pass
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(3.0); self.sock.connect((self.host, self.port))
            self.sock.settimeout(None)
        except Exception:
            self.sock = None

    def send(self, obj:dict):
        if not self.sock:
            self.connect()
            if not self.sock: return
        try:
            body = json.dumps(obj).encode("utf-8")
            head = struct.pack(">I", len(body))
            self.sock.sendall(head+body)
        except Exception:
            try: self.sock.close()
            except: pass
            self.sock = None

# ---- 상태바 ----
class TopStatusBar(QFrame):
    def __init__(self):
        super().__init__()
        self.setFrameShape(QFrame.NoFrame); self.setFixedHeight(24)
        self.lbl_time = QLabel("경과 00:00:00"); self.lbl_fps=QLabel("FPS 0"); self.lbl_ip=QLabel("서버: -")
        lay = QHBoxLayout(); lay.setContentsMargins(8,0,8,0); lay.setSpacing(16)
        lay.addWidget(self.lbl_time); lay.addWidget(self.lbl_fps); lay.addWidget(self.lbl_ip); lay.addStretch(1)
        self.setLayout(lay)
    def update_time(self, seconds:int):
        h=seconds//3600; m=(seconds%3600)//60; s=seconds%60
        self.lbl_time.setText(f"경과 {h:02d}:{m:02d}:{s:02d}")
    def update_fps(self, fps:float): self.lbl_fps.setText(f"FPS {int(fps)}")
    def update_ip(self, ip_text:str): self.lbl_ip.setText(f"서버: {ip_text}")

# ---- 원격 뷰 라벨 (입력 이벤트 포착) ----
class ViewerLabel(QLabel):
    sig_mouse = Signal(dict)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.keep_aspect = True
        self.remote_size = (0,0)  # (w,h)

    def set_keep_aspect(self, on:bool): self.keep_aspect = on
    def set_remote_size(self, w:int, h:int): self.remote_size = (w,h)

    # 라벨 내부 좌표 -> 원격 스크린 좌표 변환
    def map_to_remote(self, p: QPoint) -> tuple[int,int]:
        rw, rh = self.remote_size
        if rw<=0 or rh<=0: return (0,0)
        lw, lh = self.width(), self.height()
        if self.keep_aspect:
            # 라벨 안에 비율 유지로 맞춘 경우: 여백을 고려
            r = min(lw / rw, lh / rh)
            vw = int(rw * r); vh = int(rh * r)
            ox = (lw - vw)//2; oy = (lh - vh)//2
            x = (p.x() - ox); y = (p.y() - oy)
            if x < 0: x = 0
            if y < 0: y = 0
            if vw>0 and vh>0:
                rx = int(x * rw / vw); ry = int(y * rh / vh)
            else:
                rx, ry = 0, 0
        else:
            # 라벨 전체에 강제 맞춤(비율 무시)
            rx = int(p.x() * rw / max(1,lw))
            ry = int(p.y() * rh / max(1,lh))
        # 화면 경계 클램프
        rx = max(0, min(rx, rw-1)); ry = max(0, min(ry, rh-1))
        return (rx, ry)

    # ---- 마우스 ----
    def mouseMoveEvent(self, e):  self.sig_mouse.emit({"t":"move","x":e.position().x(),"y":e.position().y()})
    def mousePressEvent(self, e):
        btn = "left" if e.button()==Qt.LeftButton else "right" if e.button()==Qt.RightButton else "middle"
        self.sig_mouse.emit({"t":"down","btn":btn,"x":e.position().x(),"y":e.position().y()})
    def mouseReleaseEvent(self, e):
        btn = "left" if e.button()==Qt.LeftButton else "right" if e.button()==Qt.RightButton else "middle"
        self.sig_mouse.emit({"t":"up","btn":btn,"x":e.position().x(),"y":e.position().y()})
    def wheelEvent(self, e):
        delta = e.angleDelta().y()  # 120 단위
        self.sig_mouse.emit({"t":"wheel","delta":delta})

# ---- 메인 윈도우 ----
class ClientWindow(QMainWindow):
    def __init__(self, server_ip: str):
        super().__init__()
        self.setWindowTitle("원격 뷰어 클라이언트")
        self.resize(1000, 700)
        self.server_ip = server_ip

        self.topbar = TopStatusBar(); self.topbar.update_ip(f"{self.server_ip}:V{VIDEO_PORT}/C{CONTROL_PORT}")
        self.btn_full = QPushButton("전체크기"); self.btn_full.clicked.connect(self.on_fullscreen)
        self.btn_keep = QPushButton("원격해상도유지"); self.btn_keep.setCheckable(True); self.btn_keep.setChecked(True)

        self.ed_ip = QLineEdit(self.server_ip); self.ed_ip.setFixedWidth(160)
        self.btn_re = QPushButton("재연결"); self.btn_re.clicked.connect(self.on_reconnect)

        self.view = ViewerLabel("원격 화면 수신 대기")
        self.view.setAlignment(Qt.AlignCenter)
        self.view.setStyleSheet("background:#202020; color:#DDDDDD;")
        self.view.sig_mouse.connect(self.on_mouse_local)

        ctrl = QHBoxLayout(); ctrl.setContentsMargins(8,4,8,4); ctrl.setSpacing(8)
        ctrl.addWidget(self.btn_full); ctrl.addWidget(self.btn_keep); ctrl.addStretch(1)
        ctrl.addWidget(QLabel("서버 IP:")); ctrl.addWidget(self.ed_ip); ctrl.addWidget(self.btn_re)

        v = QVBoxLayout(); v.addWidget(self.topbar); v.addLayout(ctrl); v.addWidget(self.view, 1)
        wrap = QWidget(); wrap.setLayout(v); self.setCentralWidget(wrap)

        # 네트워크
        self.vc = VideoClient(self.server_ip, VIDEO_PORT); self.vc.sig_status.connect(self.on_status); self.vc.sig_frame.connect(self.on_frame); self.vc.start()
        self.cc = ControlClient(self.server_ip, CONTROL_PORT)

        # 입력 포커스(키 입력 송신)
        self.view.setFocusPolicy(Qt.StrongFocus)

        # 이벤트
        self.btn_keep.clicked.connect(self.on_keep_toggle)

    # ---- 상태/프레임 ----
    def on_status(self, fps:float, elapsed:int, connected:bool):
        self.topbar.update_fps(fps); self.topbar.update_time(elapsed if connected else 0)
        if not connected: self.view.setText("연결 끊김")

    def on_frame(self, qimg:QImage, w:int, h:int):
        self.view.set_remote_size(w,h)
        self.redraw(qimg)

    def redraw(self, qimg:QImage):
        pm = QPixmap.fromImage(qimg)
        mode_keep = self.btn_keep.isChecked()
        self.view.set_keep_aspect(mode_keep)
        scaled = pm.scaled(self.view.size(), Qt.KeepAspectRatio if mode_keep else Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        self.view.setPixmap(scaled)

    def resizeEvent(self, e):
        if self.view.pixmap() and not self.view.pixmap().isNull():
            self.redraw(self.view.pixmap().toImage())
        super().resizeEvent(e)

    # ---- 마우스/키 → 제어 송신 ----
    def on_mouse_local(self, ev:dict):
        # 라벨 좌표를 원격 해상도 좌표로 변환
        cursor = QPoint(int(ev.get("x",0)), int(ev.get("y",0)))
        rx, ry = self.view.map_to_remote(cursor)

        t = ev.get("t")
        if t == "move":
            self.cc.send({"t":"mouse_move", "x":rx, "y":ry})
        elif t == "down":
            self.cc.send({"t":"mouse_move", "x":rx, "y":ry})
            self.cc.send({"t":"mouse_down", "btn": ev.get("btn","left")})
        elif t == "up":
            self.cc.send({"t":"mouse_up", "btn": ev.get("btn","left")})
        elif t == "wheel":
            # delta는 120 단위
            self.cc.send({"t":"mouse_wheel", "delta": int(ev.get("delta",0))})

    def keyPressEvent(self, e):
        key = self._qtkey_to_str(e)
        if key: self.cc.send({"t":"key_down", "key":key})

    def keyReleaseEvent(self, e):
        key = self._qtkey_to_str(e)
        if key: self.cc.send({"t":"key_up", "key":key})

    def _qtkey_to_str(self, e) -> str:
        k = e.key()
        if 0x20 <= k <= 0x7E:
            return chr(k)
        mapping = {
            Qt.Key_Escape:"ESC", Qt.Key_Return:"ENTER", Qt.Key_Enter:"ENTER",
            Qt.Key_Backspace:"BACK", Qt.Key_Tab:"TAB", Qt.Key_Space:"SPACE",
            Qt.Key_Left:"LEFT", Qt.Key_Right:"RIGHT", Qt.Key_Up:"UP", Qt.Key_Down:"DOWN",
            Qt.Key_Delete:"DELETE", Qt.Key_Home:"HOME", Qt.Key_End:"END",
            Qt.Key_PageUp:"PGUP", Qt.Key_PageDown:"PGDN",
        }
        return mapping.get(k, "")

    # ---- 기타 ----
    def on_keep_toggle(self, checked:bool):
        if self.view.pixmap():
            self.redraw(self.view.pixmap().toImage())

    def on_fullscreen(self):
        if self.isFullScreen(): self.showNormal()
        else: self.showFullScreen()

    def on_reconnect(self):
        ip = self.ed_ip.text().strip()
        if not ip:
            QMessageBox.warning(self,"알림","IP를 입력하세요."); return
        self.server_ip = ip; self.topbar.update_ip(f"{self.server_ip}:V{VIDEO_PORT}/C{CONTROL_PORT}")
        try: self.vc.terminate()
        except: pass
        self.vc = VideoClient(self.server_ip, VIDEO_PORT); self.vc.sig_status.connect(self.on_status); self.vc.sig_frame.connect(self.on_frame); self.vc.start()
        self.cc = ControlClient(self.server_ip, CONTROL_PORT)

    def closeEvent(self, e):
        try: self.vc.stop(); self.vc.wait(1000)
        except: pass
        super().closeEvent(e)

def main():
    server_ip = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    app = QApplication(sys.argv)
    w = ClientWindow(server_ip); w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
