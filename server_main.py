# server_main.py
import sys, time, socket, select, threading, struct, json, os, ctypes
import numpy as np
import cv2
from mss import mss

from PySide6.QtCore import Qt, QTimer, QThread, Signal, QStandardPaths, QUrl, QMimeData
from PySide6.QtGui import QAction, QIcon, QGuiApplication
from PySide6.QtWidgets import QApplication, QMainWindow, QLabel, QWidget, QVBoxLayout, QSystemTrayIcon, QMenu

from common import DEFAULT_HOST, VIDEO_PORT, CONTROL_PORT, FILE_PORT, FRAME_FPS, JPEG_QUALITY, get_local_ip

# (옵션) Win32 클립보드 직접 접근
try:
    import win32clipboard, win32con
except Exception:
    win32clipboard = None
    win32con = None

# ====== Windows 입력 주입 ======
user32 = ctypes.windll.user32
SetCursorPos   = user32.SetCursorPos
mouse_event    = user32.mouse_event
keybd_event    = user32.keybd_event

MOUSEEVENTF_LEFTDOWN   = 0x0002
MOUSEEVENTF_LEFTUP     = 0x0004
MOUSEEVENTF_RIGHTDOWN  = 0x0008
MOUSEEVENTF_RIGHTUP    = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP   = 0x0040
MOUSEEVENTF_WHEEL      = 0x0800

KEYEVENTF_KEYUP = 0x0002

# 문자열 키 fallback (구버전 호환)
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

# ====== 현재 활성 탐색기 폴더 경로 ======
def get_active_explorer_folder() -> str | None:
    """포커스된 Windows 탐색기(Explorer) 창의 폴더 경로를 반환. 실패 시 None."""
    try:
        import win32com.client
    except Exception:
        return None
    try:
        hwnd_fore = user32.GetForegroundWindow()
        shell = win32com.client.Dispatch("Shell.Application")
        for w in shell.Windows():
            try:
                if int(w.HWND) == hwnd_fore and getattr(w, "Document", None):
                    folder = w.Document.Folder
                    if folder and folder.Self and os.path.isdir(folder.Self.Path):
                        return folder.Self.Path
            except Exception:
                continue
    except Exception:
        return None
    return None

# ====== 서버 클립보드 파일 목록 안정 조회 (재시도 포함) ======
def _get_clip_files_with_retry(max_try: int = 8, total_timeout: float = 0.6):
    """
    CF_HDROP(Win32) 우선, 실패 시 Qt 폴백. 잠김/지연 렌더링 대비 재시도.
    """
    start = time.time()
    attempt = 0
    paths = []
    last_err = None

    while attempt < max_try and (time.time() - start) < total_timeout and not paths:
        attempt += 1
        # 1) Win32 CF_HDROP
        if win32clipboard is not None and win32con is not None:
            try:
                win32clipboard.OpenClipboard()
                try:
                    if win32clipboard.IsClipboardFormatAvailable(win32con.CF_HDROP):
                        hdrops = win32clipboard.GetClipboardData(win32con.CF_HDROP)
                        if hdrops:
                            paths = [str(p) for p in hdrops]
                            break
                finally:
                    win32clipboard.CloseClipboard()
            except Exception as e:
                last_err = e
        # 2) Qt 폴백
        if not paths:
            try:
                cb = QGuiApplication.clipboard()
                md = cb.mimeData()
                urls = md.urls() if md else []
                paths = [u.toLocalFile() for u in urls if u.isLocalFile()]
            except Exception as e:
                last_err = e

        if not paths:
            time.sleep(0.05)  # 50ms 대기 후 재시도

    return paths, last_err

# ====== 영상 서버 ======
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

        sct = mss()
        mon = sct.monitors[1]
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

# ====== 제어 서버 ======
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

        # 마우스
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

        # 키보드 (vk 우선)
        if t == "key":
            vk = int(m.get("vk", 0))
            down = bool(m.get("down", True))
            if vk:
                keybd_event(vk, 0, 0 if down else KEYEVENTF_KEYUP, 0)
                return
            # 구버전 fallback
            name = m.get("key","")
            if not name: return
            if name == " ": name = "SPACE"
            up = name.upper()
            if len(up) == 1 and ("A"<=up<="Z" or "0"<=up<="9"):
                keybd_event(ord(up), 0, 0 if down else KEYEVENTF_KEYUP, 0); return
            vk2 = VK_FALLBACK.get(up, 0)
            if vk2:
                keybd_event(vk2, 0, 0 if down else KEYEVENTF_KEYUP, 0)
            return

        # 파일 붙여넣기 편의 (폴백 시 사용)
        if t == "set_clip_files":
            paths = m.get("paths", [])
            and_paste = bool(m.get("and_paste", False))
            if paths:
                mime = QMimeData()
                urls = [QUrl.fromLocalFile(p) for p in paths]
                mime.setUrls(urls)
                QGuiApplication.clipboard().setMimeData(mime)
                if and_paste:
                    keybd_event(0x11,0,0,0)         # CTRL down
                    keybd_event(0x56,0,0,0)         # 'V' down
                    keybd_event(0x56,0,KEYEVENTF_KEYUP,0)  # 'V' up
                    keybd_event(0x11,0,KEYEVENTF_KEYUP,0)  # CTRL up
            return

# ====== 파일 서버 ======
class FileServer(QThread):
    """
    - {"cmd":"active_folder"} → {"ok":true,"path":"C:\\..."}
    - {"cmd":"clip_meta"}     → {"ok":true/false,"files":[{"name","size"}...]}
    - {"cmd":"upload","files":[{name,size}...],"target_dir":"C:\\..."} + 본문
      → {"ok":true, "saved_dir":"...", "saved_paths":[...]}
    - {"cmd":"download_clip"} → 서버 클립보드 파일 목록/본문 스트리밍
    """
    def __init__(self, host: str, port: int):
        super().__init__()
        self.host = host; self.port = port
        self._stop = threading.Event()

    def run(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port)); srv.listen(3); srv.settimeout(0.5)
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

            if cmd == "active_folder":
                path = get_active_explorer_folder()
                resp = json.dumps({"ok": bool(path), "path": path or ""}).encode("utf-8")
                sock.sendall(struct.pack(">I", len(resp)) + resp); return

            if cmd == "clip_meta":
                paths, _ = _get_clip_files_with_retry()
                metas = []
                for p in paths:
                    try:
                        size = os.path.getsize(p)
                        metas.append({"name": os.path.basename(p), "size": int(size)})
                    except Exception:
                        pass
                resp = json.dumps({"ok": bool(metas), "files": metas}).encode("utf-8")
                sock.sendall(struct.pack(">I", len(resp)) + resp); return

            if cmd == "upload":
                self._handle_upload(sock, req); return

            if cmd == "download_clip":
                self._handle_download_clip(sock); return

        except Exception:
            pass
        finally:
            try: sock.close()
            except: pass

    def _handle_upload(self, sock, req):
        files = req.get("files", [])
        target_dir = req.get("target_dir") or ""
        if target_dir and os.path.isdir(target_dir):
            save_dir = target_dir
        else:
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
        paths, _ = _get_clip_files_with_retry()
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

# ====== 서버 윈도우 ======
class ServerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("원격화면 서버")
        self.setFixedSize(380, 210)
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
            left = self.lbl_conns.text().split("|")[0].strip()
            self.lbl_conns.setText(f"{left} | 제어 연결: {'OK' if ctrl_ok else '-'}")
        else:
            right = self.lbl_conns.text().split("|")[1].strip() if "|" in self.lbl_conns.text() else "제어 연결: -"
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
