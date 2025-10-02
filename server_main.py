# server_main.py
import sys, time, socket, select, threading, struct, json, os, ctypes
import numpy as np
import cv2
from mss import mss

from PySide6.QtCore import Qt, QTimer, QThread, Signal, QStandardPaths
from PySide6.QtGui import QAction, QIcon, QGuiApplication, QMimeData, QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication, QMainWindow, QLabel, QWidget, QVBoxLayout, QSystemTrayIcon, QMenu
from PySide6.QtCore import QUrl

from common import DEFAULT_HOST, VIDEO_PORT, CONTROL_PORT, FILE_PORT, FRAME_FPS, JPEG_QUALITY, get_local_ip

# ===========================
# Windows 입력 주입 (SendInput)
# ===========================
from ctypes import wintypes

user32 = ctypes.windll.user32
SetCursorPos = user32.SetCursorPos
mouse_event  = user32.mouse_event

# 마우스 플래그
MOUSEEVENTF_LEFTDOWN   = 0x0002
MOUSEEVENTF_LEFTUP     = 0x0004
MOUSEEVENTF_RIGHTDOWN  = 0x0008
MOUSEEVENTF_RIGHTUP    = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP   = 0x0040
MOUSEEVENTF_WHEEL      = 0x0800

# SendInput 키 플래그
INPUT_KEYBOARD           = 1
KEYEVENTF_EXTENDEDKEY    = 0x0001
KEYEVENTF_KEYUP          = 0x0002
KEYEVENTF_SCANCODE       = 0x0008

# 32/64비트 호환 ULONG_PTR
ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk",       wintypes.WORD),
        ("wScan",     wintypes.WORD),
        ("dwFlags",   wintypes.DWORD),
        ("time",      wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]

class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT)]

class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("ii", _INPUTUNION)]

# API 시그니처
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype  = wintypes.UINT

user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.MapVirtualKeyW.restype  = wintypes.UINT
MapVirtualKeyW = user32.MapVirtualKeyW

# 확장키(EXTENDED)로 취급되어야 하는 VK 집합
EXTENDED_VKS = {
    0x21, 0x22, 0x23, 0x24,       # PGUP, PGDN, END, HOME
    0x25, 0x26, 0x27, 0x28,       # LEFT, UP, RIGHT, DOWN
    0x2D, 0x2E,                   # INSERT, DELETE
    0x5B, 0x5C, 0x5D,             # LWIN, RWIN, APPS
    0x6F,                         # NUMPAD DIVIDE
    # 필요 시 RCTRL(0xA3), RALT(0xA5) 등 추가 가능
}

# 문자열 fallback용 최소 VK 매핑
VK_FALLBACK = {
    "ESC":0x1B,"ENTER":0x0D,"BACK":0x08,"TAB":0x09,"SPACE":0x20,
    "LEFT":0x25,"UP":0x26,"RIGHT":0x27,"DOWN":0x28,
    "DELETE":0x2E,"HOME":0x24,"END":0x23,"PGUP":0x21,"PGDN":0x22,
    "SHIFT":0x10,"CTRL":0x11,"ALT":0x12,"WIN":0x5B,
    "HANGUL":0x15,"HANJA":0x19,
}

def inject_key(vk: int, down: bool) -> bool:
    """
    SendInput 기반 키 주입.
    - 기본은 스캔코드 경로(KEYEVENTF_SCANCODE)
    - 확장키는 KEYEVENTF_EXTENDEDKEY 세트
    - HANGUL/HANJA 등 스캔코드가 0인 키는 wVk 경로 사용
    """
    if not vk:
        return False

    flags = 0
    if not down:
        flags |= KEYEVENTF_KEYUP
    if vk in EXTENDED_VKS:
        flags |= KEYEVENTF_EXTENDEDKEY

    sc = MapVirtualKeyW(vk, 0)  # MAPVK_VK_TO_VSC
    use_sc = (sc != 0) and (vk not in (0x15, 0x19))  # VK_HANGUL, VK_HANJA는 wVk 사용

    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    if use_sc:
        flags |= KEYEVENTF_SCANCODE
        inp.ii.ki = KEYBDINPUT(0, sc, flags, 0, 0)
    else:
        inp.ii.ki = KEYBDINPUT(vk, 0, flags, 0, 0)

    sent = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    return sent == 1

# ================
# 공통 유틸
# ================
def recv_exact(sock: socket.socket, n: int) -> bytes | None:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)

# ================
# 영상 서버
# ================
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
                    frame4 = np.array(sct.grab(mon))  # BGRA
                    frame  = cv2.cvtColor(frame4, cv2.COLOR_BGRA2BGR)
                    h, w, _ = frame.shape
                    self.sig_res_changed.emit(w, h)
                    ok, enc = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
                    if ok:
                        blob = enc.tobytes()
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

    def stop(self): self._stop.set()

# ================
# 제어 서버 (키/마우스/클립보드 보조)
# ================
class ControlServer(QThread):
    sig_ctrl_conn = Signal(bool)

    def __init__(self, host: str, port: int):
        super().__init__()
        self.host = host; self.port = port
        self._stop = threading.Event()

    def run(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port)); srv.listen(4); srv.settimeout(0.5)
        try:
            while not self._stop.is_set():
                try:
                    c,_ = srv.accept()
                except socket.timeout:
                    continue
                t = threading.Thread(target=self._handle_conn, args=(c,), daemon=True)
                t.start()
                self.sig_ctrl_conn.emit(True)
        finally:
            try: srv.close()
            except: pass

    def stop(self): self._stop.set()

    def _handle_conn(self, sock: socket.socket):
        try:
            sock.settimeout(1.0)
            while True:
                hdr = recv_exact(sock, 4)
                if not hdr: break
                jlen = struct.unpack(">I", hdr)[0]
                body = recv_exact(sock, jlen)
                if not body: break
                msg = json.loads(body.decode("utf-8", errors="ignore"))
                self._handle_msg(msg)
        except Exception:
            pass
        finally:
            try: sock.close()
            except: pass
            self.sig_ctrl_conn.emit(False)

    def _handle_msg(self, m: dict):
        t = m.get("t")

        # ---- 마우스 ----
        if t == "mouse_move":
            x = int(m.get("x", 0)); y = int(m.get("y", 0))
            SetCursorPos(x, y); return

        if t == "mouse_down":
            btn = m.get("btn","left")
            if btn=="left":  mouse_event(MOUSEEVENTF_LEFTDOWN, 0,0,0,0)
            elif btn=="right": mouse_event(MOUSEEVENTF_RIGHTDOWN,0,0,0,0)
            elif btn=="middle":mouse_event(MOUSEEVENTF_MIDDLEDOWN,0,0,0,0)
            return

        if t == "mouse_up":
            btn = m.get("btn","left")
            if btn=="left":  mouse_event(MOUSEEVENTF_LEFTUP, 0,0,0,0)
            elif btn=="right": mouse_event(MOUSEEVENTF_RIGHTUP,0,0,0,0)
            elif btn=="middle":mouse_event(MOUSEEVENTF_MIDDLEUP,0,0,0,0)
            return

        if t == "mouse_wheel":
            delta = int(m.get("delta", 0))
            mouse_event(MOUSEEVENTF_WHEEL, 0,0,delta,0); return

        # ---- 키보드 (vk 우선 전송) ----
        if t == "key":
            vk   = int(m.get("vk", 0))
            down = bool(m.get("down", True))
            if vk:
                inject_key(vk, down)
                return

            # 문자열 fallback (레거시)
            name = (m.get("key","") or "").upper()
            if name == " ": name = "SPACE"
            if len(name) == 1 and ("A" <= name <= "Z" or "0" <= name <= "9"):
                inject_key(ord(name), down); return
            v2 = VK_FALLBACK.get(name, 0)
            if v2:
                inject_key(v2, down)
            return

        # ---- 파일 붙여넣기 보조: 서버 클립보드에 파일 목록 세팅 + 필요 시 Ctrl+V 자동 주입 ----
        if t == "set_clip_files":
            paths = m.get("paths", [])
            and_paste = bool(m.get("and_paste", False))
            if paths:
                mime = QMimeData()
                urls = [QUrl.fromLocalFile(p) for p in paths]
                mime.setUrls(urls)
                QGuiApplication.clipboard().setMimeData(mime)
                if and_paste:
                    inject_key(0x11, True)    # CTRL down
                    inject_key(0x56, True)    # 'V' down
                    inject_key(0x56, False)   # 'V' up
                    inject_key(0x11, False)   # CTRL up
            return

# ================
# 파일 서버
# ================
class FileServer(QThread):
    """
    업로드(클→서):  [4][json: {"cmd":"upload","files":[{"name":..., "size":...}, ...]}] + 파일 데이터
      저장: %USERPROFILE%/Downloads/RemoteDrop
      응답: {"ok":True, "saved_dir":..., "saved_paths":[fullpath,...]}

    다운로드(서→클): {"cmd":"download_clip"} 요청 시
      응답 헤더: {"cmd":"download_clip","files":[{"name":..., "size":...}, ...]}
      이후 본문 스트리밍
    """
    def __init__(self, host: str, port: int):
        super().__init__()
        self.host = host; self.port = port
        self._stop = threading.Event()

    def run(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port)); srv.listen(2); srv.settimeout(0.5)
        try:
            while not self._stop.is_set():
                try:
                    c, _ = srv.accept()
                except socket.timeout:
                    continue
                threading.Thread(target=self._handle_conn, args=(c,), daemon=True).start()
        finally:
            try: srv.close()
            except: pass

    def stop(self): self._stop.set()

    def _handle_conn(self, sock: socket.socket):
        try:
            hdr = recv_exact(sock, 4)
            if not hdr: return
            jlen = struct.unpack(">I", hdr)[0]
            jraw = recv_exact(sock, jlen)
            if not jraw: return
            req = json.loads(jraw.decode("utf-8", errors="ignore"))
            cmd = req.get("cmd","")

            if cmd == "upload":
                self._handle_upload(sock, req)
            elif cmd == "download_clip":
                self._handle_download_clip(sock)
        except Exception:
            pass
        finally:
            try: sock.close()
            except: pass

    def _handle_upload(self, sock, req):
        files = req.get("files", [])
        base_dir = QStandardPaths.writableLocation(QStandardPaths.DownloadLocation) or os.path.expanduser("~/Downloads")
        save_dir = os.path.join(base_dir, "RemoteDrop")
        os.makedirs(save_dir, exist_ok=True)

        saved_paths = []
        for meta in files:
            name = os.path.basename(meta.get("name","file"))
            size = int(meta.get("size", 0))
            dst  = os.path.join(save_dir, name)
            with open(dst, "wb") as f:
                remain = size
                while remain > 0:
                    chunk = sock.recv(min(1024*256, remain))
                    if not chunk: raise ConnectionError("file stream interrupted")
                    f.write(chunk); remain -= len(chunk)
            saved_paths.append(dst)

        ack = json.dumps({"ok": True, "saved_dir": save_dir, "saved_paths": saved_paths}).encode("utf-8")
        sock.sendall(struct.pack(">I", len(ack)) + ack)

    def _handle_download_clip(self, sock):
        cb = QGuiApplication.clipboard()
        md = cb.mimeData()
        urls = md.urls() if md else []
        paths = [u.toLocalFile() for u in urls if u.isLocalFile()]

        metas = []
        for p in paths:
            try:
                size = os.path.getsize(p)
                metas.append({"name": os.path.basename(p), "size": int(size), "path": p})
            except Exception:
                pass

        head = json.dumps({"cmd":"download_clip", "files":[{"name":m["name"],"size":m["size"]} for m in metas]}).encode("utf-8")
        sock.sendall(struct.pack(">I", len(head)) + head)
        for m in metas:
            with open(m["path"], "rb") as f:
                while True:
                    buf = f.read(1024*256)
                    if not buf: break
                    sock.sendall(buf)

# ================
# 서버 윈도우(UI)
# ================
class ServerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("원격화면 서버")
        self.setFixedSize(360, 200)
        self.start_ts = time.time()
        self.ip = get_local_ip()

        self.video = VideoServer(DEFAULT_HOST, VIDEO_PORT)
        self.ctrl  = ControlServer(DEFAULT_HOST, CONTROL_PORT)
        self.files = FileServer(DEFAULT_HOST, FILE_PORT)
        self.video.sig_conn_changed.connect(self.on_video_conn)
        self.video.sig_res_changed.connect(self.on_res)
        self.ctrl.sig_ctrl_conn.connect(self.on_ctrl_conn)

        self.lbl_ip = QLabel(f"서버 IP: {self.ip}  V:{VIDEO_PORT} / C:{CONTROL_PORT} / F:{FILE_PORT}", alignment=Qt.AlignCenter)
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
        super().showEvent(e)
        self.video.start(); self.ctrl.start(); self.files.start()

    def closeEvent(self, e):
        self.video.stop(); self.video.wait(1500)
        self.ctrl.stop();  self.ctrl.wait(1500)
        self.files.stop(); self.files.wait(1500)
        super().closeEvent(e)

def main():
    app = QApplication(sys.argv)
    w = ServerWindow(); w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
