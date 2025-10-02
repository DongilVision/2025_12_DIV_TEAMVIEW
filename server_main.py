# server_main.py
import sys, time, socket, select, threading, struct, json, os, ctypes
import numpy as np
import cv2
from mss import mss

from PySide6.QtCore import Qt, QTimer, QThread, Signal, QStandardPaths, QUrl, QMimeData
from PySide6.QtGui import QAction, QIcon, QGuiApplication
from PySide6.QtWidgets import QApplication, QMainWindow, QLabel, QWidget, QVBoxLayout, QSystemTrayIcon, QMenu

from common import DEFAULT_HOST, VIDEO_PORT, CONTROL_PORT, FILE_PORT, FRAME_FPS, JPEG_QUALITY, get_local_ip

# ===== Windows 입력 주입 =====
user32 = ctypes.windll.user32
SetCursorPos = user32.SetCursorPos
mouse_event  = user32.mouse_event
keybd_event  = user32.keybd_event

MOUSEEVENTF_LEFTDOWN   = 0x0002
MOUSEEVENTF_LEFTUP     = 0x0004
MOUSEEVENTF_RIGHTDOWN  = 0x0008
MOUSEEVENTF_RIGHTUP    = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP   = 0x0040
MOUSEEVENTF_WHEEL      = 0x0800

KEYEVENTF_KEYUP = 0x0002

VK_FALLBACK = {
    "ESC":0x1B,"ENTER":0x0D,"BACK":0x08,"TAB":0x09,"SPACE":0x20,
    "LEFT":0x25,"UP":0x26,"RIGHT":0x27,"DOWN":0x28,
    "DELETE":0x2E,"HOME":0x24,"END":0x23,"PGUP":0x21,"PGDN":0x22,
    "SHIFT":0x10,"CTRL":0x11,"ALT":0x12,"WIN":0x5B,
    "HANGUL":0x15,"HANJA":0x19,
}

def recv_exact(sock: socket.socket, n: int) -> bytes | None:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)

def send_json(sock: socket.socket, obj: dict):
    raw = json.dumps(obj).encode("utf-8")
    sock.sendall(struct.pack(">I", len(raw)) + raw)

# ===== 영상 서버 =====
class VideoServer(QThread):
    sig_conn_changed = Signal(int)
    sig_res_changed  = Signal(int, int)

    def __init__(self, host: str, port: int):
        super().__init__()
        self.host = host; self.port = port
        self._stop = threading.Event()
        self._clients = set(); self._lock = threading.Lock()

    def run(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port)); srv.listen(5); srv.setblocking(False)

        sct = mss(); mon = sct.monitors[1]
        last_frame_ts = 0.0
        frame_interval = 1.0 / max(1, FRAME_FPS)

        try:
            while not self._stop.is_set():
                rlist, _, _ = select.select([srv] + list(self._clients), [], [], 0.01)
                for s in rlist:
                    if s is srv:
                        try:
                            c,_ = srv.accept(); c.setblocking(False)
                            with self._lock:
                                self._clients.add(c)
                                self.sig_conn_changed.emit(len(self._clients))
                        except BlockingIOError:
                            pass
                    else:
                        try:
                            if not s.recv(1): self._drop(s)
                        except (BlockingIOError, ConnectionResetError, OSError):
                            self._drop(s)

                now = time.time()
                if now - last_frame_ts >= frame_interval:
                    frame = np.array(sct.grab(mon))[:, :, :3]
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
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
                            for dc in drop: self._drop(dc)
                    last_frame_ts = now
        finally:
            with self._lock:
                for c in list(self._clients):
                    try: c.close()
                    except: pass
                self._clients.clear()
            try: srv.close()
            except: pass

    def _drop(self, s):
        try: s.close()
        except: pass
        with self._lock:
            if s in self._clients:
                self._clients.remove(s)
                self.sig_conn_changed.emit(len(self._clients))

    def stop(self): self._stop.set()

# ===== 제어 서버 =====
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
                threading.Thread(target=self._handle_conn, args=(c,), daemon=True).start()
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
        if t == "mouse_move":
            SetCursorPos(int(m.get("x",0)), int(m.get("y",0))); return
        if t == "mouse_down":
            btn = m.get("btn","left")
            if btn=="left": mouse_event(MOUSEEVENTF_LEFTDOWN,0,0,0,0)
            elif btn=="right": mouse_event(MOUSEEVENTF_RIGHTDOWN,0,0,0,0)
            else: mouse_event(MOUSEEVENTF_MIDDLEDOWN,0,0,0,0)
            return
        if t == "mouse_up":
            btn = m.get("btn","left")
            if btn=="left": mouse_event(MOUSEEVENTF_LEFTUP,0,0,0,0)
            elif btn=="right": mouse_event(MOUSEEVENTF_RIGHTUP,0,0,0,0)
            else: mouse_event(MOUSEEVENTF_MIDDLEUP,0,0,0,0)
            return
        if t == "mouse_wheel":
            mouse_event(MOUSEEVENTF_WHEEL, 0,0, int(m.get("delta",0)), 0); return

        if t == "key":
            vk = int(m.get("vk", 0)); down = bool(m.get("down", True))
            if vk:
                keybd_event(vk, 0, 0 if down else KEYEVENTF_KEYUP, 0); return
            name = m.get("key","")
            if name == " ": name = "SPACE"
            up = name.upper()
            if len(up)==1 and ("A"<=up<="Z" or "0"<=up<="9"):
                keybd_event(ord(up), 0, 0 if down else KEYEVENTF_KEYUP, 0); return
            vk2 = VK_FALLBACK.get(up,0)
            if vk2: keybd_event(vk2, 0, 0 if down else KEYEVENTF_KEYUP, 0)
            return

        if t == "set_clip_files":
            paths = m.get("paths", [])
            and_paste = bool(m.get("and_paste", False))
            if paths:
                mime = QMimeData()
                mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
                QGuiApplication.clipboard().setMimeData(mime)
                if and_paste:
                    keybd_event(0x11,0,0,0); keybd_event(0x56,0,0,0)
                    keybd_event(0x56,0,KEYEVENTF_KEYUP,0); keybd_event(0x11,0,KEYEVENTF_KEYUP,0)
            return

# ===== 파일/디렉토리 서버 =====
class FileServer(QThread):
    """
    프로토콜(요청/응답 모두 JSON 길이프리픽스):
    - {"cmd":"ls","path": "<abs or empty>"} → {"ok":true,"path": "<norm abs>", "items":[{"name":..., "is_dir":bool, "size":int, "mtime":float}]}
    - {"cmd":"upload_to","target_dir":"<abs>","files":[{"name":..., "size":...}, ...]} + [본문 스트림...]
      → {"ok":true,"saved":[ "<abs>", ... ]}
    - {"cmd":"download_paths","paths":[ "<abs>", ... ]}
      → 먼저 {"ok":true,"files":[{"name":..., "size":...}, ...]} 후 본문 스트림 연속 전송
    (기존 download_clip, upload 등은 생략해도 무방하나 남겨둬도 문제 없음)
    """
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

            if cmd == "ls":
                self._handle_ls(sock, req)
            elif cmd == "upload_to":
                self._handle_upload_to(sock, req)
            elif cmd == "download_paths":
                self._handle_download_paths(sock, req)
        except Exception:
            pass
        finally:
            try: sock.close()
            except: pass

    def _handle_ls(self, sock, req):
        path = req.get("path") or os.path.expanduser("~")
        path = os.path.abspath(path)
        items = []
        try:
            with os.scandir(path) as it:
                for e in it:
                    try:
                        st = e.stat()
                        items.append({
                            "name": e.name,
                            "is_dir": e.is_dir(),
                            "size": int(st.st_size),
                            "mtime": float(st.st_mtime),
                        })
                    except Exception:
                        pass
            send_json(sock, {"ok": True, "path": path, "items": items})
        except Exception as ex:
            send_json(sock, {"ok": False, "error": str(ex)})

    def _handle_upload_to(self, sock, req):
        target_dir = req.get("target_dir","")
        files = req.get("files", [])
        saved = []
        try:
            if not target_dir: raise ValueError("target_dir required")
            target_dir = os.path.abspath(target_dir)
            os.makedirs(target_dir, exist_ok=True)
            for m in files:
                name = os.path.basename(m.get("name","file"))
                size = int(m.get("size",0))
                dst = os.path.join(target_dir, name)
                with open(dst, "wb") as f:
                    remain = size
                    while remain > 0:
                        chunk = sock.recv(min(1024*256, remain))
                        if not chunk: raise ConnectionError("file stream interrupted")
                        f.write(chunk); remain -= len(chunk)
                saved.append(dst)
            send_json(sock, {"ok": True, "saved": saved})
        except Exception as ex:
            send_json(sock, {"ok": False, "error": str(ex)})

    def _handle_download_paths(self, sock, req):
        paths = [os.path.abspath(p) for p in req.get("paths",[])]
        metas = []
        for p in paths:
            if os.path.isfile(p):
                try:
                    metas.append({"name": os.path.basename(p), "size": int(os.path.getsize(p)), "path": p})
                except Exception:
                    pass
        send_json(sock, {"ok": True, "files":[{"name":m["name"],"size":m["size"]} for m in metas]})
        for m in metas:
            with open(m["path"], "rb") as f:
                while True:
                    buf = f.read(1024*256)
                    if not buf: break
                    sock.sendall(buf)

# ===== 서버 UI =====
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
        v.addWidget(self.lbl_ip); v.addWidget(self.lbl_res)
        v.addWidget(self.lbl_uptime); v.addWidget(self.lbl_conns)
        wrap = QWidget(); wrap.setLayout(v); self.setCentralWidget(wrap)

        self.timer = QTimer(self); self.timer.timeout.connect(self.update_uptime); self.timer.start(500)

        self.tray = QSystemTrayIcon(self); self.tray.setIcon(QIcon.fromTheme("application-exit"))
        menu = QMenu(); act_quit = QAction("종료", self); act_quit.triggered.connect(self.close)
        menu.addAction(act_quit); self.tray.setContextMenu(menu); self.tray.show()

    def on_video_conn(self, n:int): self._update_conn_label(n, None)
    def on_ctrl_conn(self, ok:bool): self._update_conn_label(None, ok)
    def on_res(self, w:int, h:int): self.lbl_res.setText(f"원격 해상도: {w} x {h}")

    def _update_conn_label(self, vid_cnt, ctrl_ok):
        if vid_cnt is None:
            left = self.lbl_conns.text().split("|")[0].strip()
            self.lbl_conns.setText(f"{left} | 제어 연결: {'OK' if ctrl_ok else '-'}")
        else:
            right = self.lbl_conns.text().split("|")[1].strip() if "|" in self.lbl_conns.text() else "제어 연결: -"
            self.lbl_conns.setText(f"영상 연결 수: {vid_cnt} | {right}")

    def update_uptime(self):
        e = int(time.time() - self.start_ts)
        self.lbl_uptime.setText(f"연결 시간: {e//3600:02d}:{(e%3600)//60:02d}:{e%60:02d}")

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
