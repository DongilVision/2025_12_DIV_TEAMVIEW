# server_main.py
import sys, time, socket, select, threading, struct, json, os, ctypes
import numpy as np
import cv2
from mss import mss

from PySide6.QtCore import Qt, QTimer, QThread, Signal, QStandardPaths, QUrl, QMimeData
from PySide6.QtGui import QAction, QIcon, QGuiApplication
from PySide6.QtWidgets import QApplication, QMainWindow, QLabel, QWidget, QVBoxLayout, QSystemTrayIcon, QMenu

from common import DEFAULT_HOST, VIDEO_PORT, CONTROL_PORT, FILE_PORT, FRAME_FPS, JPEG_QUALITY, get_local_ip

# ====== (추가) pywin32: 서버 클립보드 파일 경로 안전 획득용 ======
try:
    import win32clipboard
    import win32con
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

# ====== 서버: 현재 활성 탐색기 폴더(포커스 창) 조회 ======
def get_active_explorer_folder() -> str | None:
    """
    서버 PC에서 전면(포커스) 탐색기 창의 폴더 경로 반환. 실패 시 None.
    (win32com.client 사용; 미설치시 None)
    """
    try:
        import win32com.client
    except Exception:
        return None
    try:
        hwnd_fore = user32.GetForegroundWindow()
        shell = win32com.client.Dispatch("Shell.Application")
        for w in shell.Windows():
            try:
                if int(w.HWND) == hwnd_fore:
                    doc = getattr(w, "Document", None)
                    folder = getattr(doc, "Folder", None)
                    self_obj = getattr(folder, "Self", None)
                    path = getattr(self_obj, "Path", None)
                    if path and os.path.isdir(path):
                        return path
            except Exception:
                continue
    except Exception:
        return None
    return None

# ====== (중요) 서버 클립보드(CF_HDROP)에서 파일 경로 안전 취득 ======
def get_clipboard_file_paths_win32(max_retries: int = 6, wait_ms: int = 80) -> list[str]:
    """
    Windows 클립보드에서 CF_HDROP(파일 목록)을 pywin32로 안전하게 획득.
    GUI 스레드가 아니어도 사용 가능. 잠금 충돌 시 재시도.
    """
    if not (win32clipboard and win32con):
        return []  # pywin32 미설치 시 빈 리스트
    for _ in range(max_retries):
        try:
            win32clipboard.OpenClipboard()
            try:
                if win32clipboard.IsClipboardFormatAvailable(win32con.CF_HDROP):
                    files = win32clipboard.GetClipboardData(win32con.CF_HDROP)
                    if isinstance(files, (list, tuple)):
                        return [f for f in files if isinstance(f, str)]
            finally:
                win32clipboard.CloseClipboard()
            break
        except Exception:
            time.sleep(wait_ms / 1000.0)
    return []

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

        # ---- 키보드 (vk 우선) ----
        if t == "key":
            vk = int(m.get("vk", 0))
            down = bool(m.get("down", True))
            if vk:
                keybd_event(vk, 0, 0 if down else KEYEVENTF_KEYUP, 0)
                return
            # 구버전 fallback: "key" 문자열
            name = m.get("key","")
            if not name: return
            if name == " ": name = "SPACE"
            up = name.upper()
            if len(up) == 1 and ("A"<=up<="Z" or "0"<=up<="9"):
                keybd_event(ord(up), 0, 0 if down else KEYEVENTF_KEYUP, 0)
                return
            vk2 = VK_FALLBACK.get(up, 0)
            if vk2:
                keybd_event(vk2, 0, 0 if down else KEYEVENTF_KEYUP, 0)
            return

        # ---- 파일 붙여넣기 편의 (폴백 시 사용) ----
        if t == "set_clip_files":
            paths = m.get("paths", [])
            and_paste = bool(m.get("and_paste", False))
            if paths:
                mime = QMimeData()
                urls = [QUrl.fromLocalFile(p) for p in paths]
                mime.setUrls(urls)
                QGuiApplication.clipboard().setMimeData(mime)
                if and_paste:
                    # Ctrl+V 시퀀스
                    keybd_event(0x11,0,0,0)  # CTRL down
                    keybd_event(0x56,0,0,0)  # 'V' down
                    keybd_event(0x56,0,KEYEVENTF_KEYUP,0)  # 'V' up
                    keybd_event(0x11,0,KEYEVENTF_KEYUP,0)  # CTRL up
            return

# ====== 파일 서버 ======
class FileServer(QThread):
    """
    요청 종류:
    - {"cmd":"active_folder"}  → 현재 활성 탐색기 폴더 경로 반환
      응답: {"ok":true, "path":"C:\\..."} 또는 {"ok":false}

    - {"cmd":"upload","files":[{name,size}...], "target_dir":"C:\\..."} + 본문
      응답: {"ok":true, "saved_dir": "...", "saved_paths":[...]}
      target_dir 없으면 Downloads/RemoteDrop으로 저장(폴백)

    - {"cmd":"download_clip"}  → 서버 클립보드(CF_HDROP) 파일 목록/본문 스트리밍
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
                resp = {"ok": bool(path), "path": path or ""}
                raw = json.dumps(resp).encode("utf-8")
                sock.sendall(struct.pack(">I", len(raw)) + raw)
                return

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
        """
        서버(Windows)의 현재 클립보드에 있는 파일들(CF_HDROP)을
        헤더(JSON) + 바디(파일 스트림)로 전송.
        ※ QClipboard 대신 pywin32(win32clipboard)로 읽어 스레드 안전성 확보.
        """
        # 1) 클립보드에서 파일 목록 읽기 (스레드 안전, 재시도)
        paths = get_clipboard_file_paths_win32()

        # 2) 메타 구성
        metas = []
        for p in paths:
            try:
                if os.path.isfile(p):
                    size = os.path.getsize(p)
                    metas.append({"name": os.path.basename(p), "size": int(size), "path": p})
            except Exception:
                pass

        # 3) 헤더 전송
        head = json.dumps({
            "cmd": "download_clip",
            "files": [{"name": m["name"], "size": m["size"]} for m in metas]
        }).encode("utf-8")
        sock.sendall(struct.pack(">I", len(head)) + head)

        # 4) 본문 전송
        for m in metas:
            with open(m["path"], "rb") as f:
                while True:
                    buf = f.read(1024 * 256)
                    if not buf:
                        break
                    sock.sendall(buf)

# ====== 서버 UI ======
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
            parts = self.lbl_conns.text().split("|")
            left = parts[0].strip()
            self.lbl_conns.setText(f"{left} | 제어 연결: {'OK' if ctrl_ok else '-'}")
        elif ctrl_ok is None:
            parts = self.lbl_conns.text().split("|")
            right = self.lbl_conns.text().split("|")[1].strip() if len(parts)>1 else "제어 연결: -"
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
