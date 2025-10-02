# server_main.py
import sys, time, socket, select, threading, struct, json, os, ctypes, base64, hashlib, uuid
import numpy as np
import cv2
from mss import mss

from PySide6.QtCore import Qt, QTimer, QThread, Signal, QStandardPaths, QUrl, QByteArray, QObject
from PySide6.QtGui import QAction, QIcon, QGuiApplication, QImage, QPixmap, QClipboard
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

META_MIME = "application/x-remote-clip-meta"
SELF_ID = uuid.uuid4().hex

def sha256_bytes(b: bytes) -> str:
    h = hashlib.sha256(); h.update(b); return h.hexdigest()

def recv_exact(sock: socket.socket, n: int) -> bytes | None:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)

def get_clipboard_file_paths_win32(max_retries: int = 6, wait_ms: int = 80) -> list[str]:
    """Windows 클립보드에서 CF_HDROP 파일 목록 안전 획득."""
    if not (win32clipboard and win32con):
        return []
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
        sct = mss(); mon = sct.monitors[1]
        last_ts = 0.0; interval = 1.0 / max(1, FRAME_FPS)
        try:
            while not self._stop.is_set():
                rlist, _, _ = select.select([srv]+list(self._clients), [], [], 0.01)
                for s in rlist:
                    if s is srv:
                        try:
                            c,_ = srv.accept(); c.setblocking(False)
                            with self._lock:
                                self._clients.add(c); self.sig_conn_changed.emit(len(self._clients))
                        except BlockingIOError:
                            pass
                    else:
                        try:
                            if not s.recv(1): self._drop(s)
                        except (BlockingIOError, ConnectionResetError, OSError):
                            self._drop(s)
                now = time.time()
                if now - last_ts >= interval:
                    frame = np.array(sct.grab(mon))[:, :, :3]
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                    h, w, _ = frame.shape; self.sig_res_changed.emit(w, h)
                    ok, enc = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
                    if ok:
                        blob = enc.tobytes(); header = struct.pack(">III", len(blob), w, h); pkt = header + blob
                        drop=[]
                        with self._lock:
                            for c in self._clients:
                                try: c.sendall(pkt)
                                except OSError: drop.append(c)
                            for dc in drop: self._drop(dc)
                    last_ts = now
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
                self._clients.remove(s); self.sig_conn_changed.emit(len(self._clients))
    def stop(self): self._stop.set()

# ====== 제어 서버(양방향 메시지) ======
class ControlServer(QThread):
    sig_ctrl_conn = Signal(bool)
    sig_incoming  = Signal(dict)  # 클라이언트→서버 수신 이벤트(클립보드 등)

    def __init__(self, host: str, port: int):
        super().__init__(); self.host = host; self.port = port
        self._stop = threading.Event()
        self._clients = set(); self._lock = threading.Lock()

    def run(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port)); srv.listen(8); srv.settimeout(0.5)
        try:
            while not self._stop.is_set():
                try: c,_ = srv.accept()
                except socket.timeout: continue
                with self._lock: self._clients.add(c)
                self.sig_ctrl_conn.emit(True)
                threading.Thread(target=self._rx_loop, args=(c,), daemon=True).start()
        finally:
            try: srv.close()
            except: pass

    def _rx_loop(self, sock: socket.socket):
        try:
            sock.settimeout(1.0)
            while True:
                hdr = recv_exact(sock, 4)
                if not hdr: break
                jlen = struct.unpack(">I", hdr)[0]
                body = recv_exact(sock, jlen)
                if not body: break
                msg = json.loads(body.decode("utf-8","ignore"))
                self.sig_incoming.emit(msg)
        except Exception:
            pass
        finally:
            try: sock.close()
            except: pass
            with self._lock:
                if sock in self._clients: self._clients.remove(sock)
            self.sig_ctrl_conn.emit(False)

    def send_json_all(self, obj: dict):
        raw = json.dumps(obj).encode("utf-8"); head = struct.pack(">I", len(raw))
        with self._lock:
            dead=[]
            for c in self._clients:
                try: c.sendall(head+raw)
                except Exception: dead.append(c)
            for d in dead:
                try: d.close()
                except: pass
                self._clients.discard(d)

    def stop(self): self._stop.set()

# ====== 파일 서버 ======
class FileServer(QThread):
    """
    cmd:
      - {"cmd":"active_folder"} → {"ok":true,"path":"C:\\..."} 또는 {"ok":false}
      - {"cmd":"upload","files":[{name,size}...],"target_dir":"C:\\..."} + 본문
         → {"ok":true,"saved_dir":"...","saved_paths":[...]}
      - {"cmd":"download_clip"} → 헤더{"files":[{name,size}...]} + 본문 스트리밍 (서버 현재 클립보드 파일)
      - {"cmd":"probe_clip"}   → {"ok":true,"count":N,"files":[...]}  (서버 클립보드 파일 개요)
      - {"cmd":"clip_text","text":str,"meta":{...}}
      - {"cmd":"clip_image","png_b64":str,"meta":{...}}
      - {"cmd":"set_clip_files","paths":[...],"meta":{...}}  # 외부에서 직접 호출할 수도 있게
    """
    def __init__(self, host: str, port: int):
        super().__init__(); self.host=host; self.port=port; self._stop=threading.Event()
    def run(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR,1)
        srv.bind((self.host,self.port)); srv.listen(5); srv.settimeout(0.5)
        try:
            while not self._stop.is_set():
                try: c,_ = srv.accept()
                except socket.timeout: continue
                threading.Thread(target=self._handle, args=(c,), daemon=True).start()
        finally:
            try: srv.close()
            except: pass
    def stop(self): self._stop.set()

    def _handle(self, sock: socket.socket):
        try:
            hdr = recv_exact(sock,4)
            if not hdr: return
            jlen = struct.unpack(">I", hdr)[0]
            jraw = recv_exact(sock, jlen)
            if not jraw: return
            req = json.loads(jraw.decode("utf-8","ignore")); cmd=req.get("cmd","")

            if cmd=="active_folder":
                path = get_active_explorer_folder()
                raw = json.dumps({"ok":bool(path),"path":path or ""}).encode("utf-8")
                sock.sendall(struct.pack(">I",len(raw))+raw); return

            if cmd=="probe_clip":
                paths = get_clipboard_file_paths_win32()
                metas=[]
                for p in paths:
                    try:
                        if os.path.isfile(p):
                            size=os.path.getsize(p)
                            metas.append({"name":os.path.basename(p),"size":int(size)})
                    except Exception: pass
                resp = json.dumps({"ok":True,"count":len(metas),"files":metas}).encode("utf-8")
                sock.sendall(struct.pack(">I",len(resp))+resp); return

            if cmd=="upload": self._upload(sock, req); return
            if cmd=="download_clip": self._download_clip(sock); return

            if cmd=="clip_text":
                apply_clip_text(req.get("text",""), req.get("meta") or {})
                ack=b"{}"; sock.sendall(struct.pack(">I",len(ack))+ack); return
            if cmd=="clip_image":
                b64=req.get("png_b64",""); data = base64.b64decode(b64) if b64 else b""
                apply_clip_image(data, req.get("meta") or {})
                ack=b"{}"; sock.sendall(struct.pack(">I",len(ack))+ack); return
            if cmd=="set_clip_files":
                paths=req.get("paths") or []; apply_clip_files(paths, req.get("meta") or {})
                ack=b"{}"; sock.sendall(struct.pack(">I",len(ack))+ack); return

        except Exception:
            pass
        finally:
            try: sock.close()
            except: pass

    def _upload(self, sock, req):
        files = req.get("files",[]); target = req.get("target_dir") or ""
        if target and os.path.isdir(target): save_dir = target
        else:
            base = QStandardPaths.writableLocation(QStandardPaths.DownloadLocation) or os.path.expanduser("~/Downloads")
            save_dir = os.path.join(base, "RemoteDrop")
        os.makedirs(save_dir, exist_ok=True)
        saved=[]
        for meta in files:
            name=os.path.basename(meta.get("name","file")); size=int(meta.get("size",0))
            dst=os.path.join(save_dir,name)
            with open(dst,"wb") as f:
                remain=size
                while remain>0:
                    buf = sock.recv(min(1024*256, remain))
                    if not buf: raise ConnectionError("file stream interrupted")
                    f.write(buf); remain-=len(buf)
            saved.append(dst)
        ack=json.dumps({"ok":True,"saved_dir":save_dir,"saved_paths":saved}).encode("utf-8")
        sock.sendall(struct.pack(">I",len(ack))+ack)

    def _download_clip(self, sock):
        cb = QGuiApplication.clipboard(); md = cb.mimeData(); urls = md.urls() if md else []
        paths = [u.toLocalFile() for u in urls if u.isLocalFile()]
        # pywin32 보강
        if not paths:
            paths = get_clipboard_file_paths_win32()
        metas=[]
        for p in paths:
            try:
                size=os.path.getsize(p)
                metas.append({"name":os.path.basename(p),"size":int(size),"path":p})
            except Exception: pass
        head=json.dumps({"cmd":"download_clip","files":[{"name":m["name"],"size":m["size"]} for m in metas]}).encode("utf-8")
        sock.sendall(struct.pack(">I",len(head))+head)
        for m in metas:
            with open(m["path"],"rb") as f:
                while True:
                    b=f.read(1024*256)
                    if not b: break
                    sock.sendall(b)

# ====== 유틸: 활성 탐색기 폴더 ======
def get_active_explorer_folder() -> str | None:
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
                    doc = getattr(w, "Document", None); folder = getattr(doc, "Folder", None)
                    self_obj = getattr(folder, "Self", None); path = getattr(self_obj, "Path", None)
                    if path and os.path.isdir(path): return path
            except Exception: continue
    except Exception:
        return None
    return None

# ====== 로컬 클립보드 적용기 ======
def apply_meta(mime, meta: dict):
    try:
        raw = json.dumps(meta).encode("utf-8")
        mime.setData(META_MIME, QByteArray(raw))
    except Exception:
        pass

def apply_clip_text(text: str, meta: dict):
    mime = QClipboardMime()
    mime.set_text(text); apply_meta(mime, meta)
    QGuiApplication.clipboard().setMimeData(mime.qmime)

def apply_clip_image(png_bytes: bytes, meta: dict):
    img = QImage.fromData(png_bytes, "PNG")
    if img.isNull(): return
    mime = QClipboardMime()
    mime.set_image(img); apply_meta(mime, meta)
    QGuiApplication.clipboard().setMimeData(mime.qmime)

def apply_clip_files(paths: list[str], meta: dict):
    urls=[QUrl.fromLocalFile(p) for p in paths if os.path.exists(p)]
    if not urls: return
    mime = QClipboardMime()
    mime.set_urls(urls); apply_meta(mime, meta)
    QGuiApplication.clipboard().setMimeData(mime.qmime)

class QClipboardMime:
    """QMimeData helper"""
    from PySide6.QtCore import QMimeData
    def __init__(self):
        from PySide6.QtCore import QMimeData
        self.qmime = QMimeData()
    def set_text(self, t:str):
        self.qmime.setText(t)
    def set_image(self, img: QImage):
        self.qmime.setImageData(img)
    def set_urls(self, urls:list[QUrl]):
        self.qmime.setUrls(urls)

# ====== 클립보드 감시/브로드캐스트 ======
class ClipboardSync(QObject):
    """서버 쪽: 로컬(서버) 클립보드 변경 시 클라이언트에게 브로드캐스트"""
    def __init__(self, ctrl: ControlServer):
        super().__init__()
        self.ctrl = ctrl
        self.seq = 0
        self.last_hash = ""
        self._debounce = QTimer(self); self._debounce.setSingleShot(True); self._debounce.setInterval(150)
        self._debounce.timeout.connect(self.on_debounced)
        QGuiApplication.clipboard().dataChanged.connect(self.on_changed)

    def on_changed(self):
        self._debounce.start()

    def on_debounced(self):
        cb = QGuiApplication.clipboard(); md = cb.mimeData()
        if not md: return

        # 루프 방지: 내가 보낸 메타는 무시
        try:
            if md.hasFormat(META_MIME):
                meta = json.loads(bytes(md.data(META_MIME)).decode("utf-8","ignore"))
                if meta.get("origin_id")==SELF_ID:
                    return
        except Exception:
            pass

        # 텍스트
        if md.hasText():
            text = md.text()
            payload = text.encode("utf-8")
            h = sha256_bytes(payload)
            if h == self.last_hash: return
            self.last_hash = h; self.seq += 1
            meta={"origin_id": SELF_ID, "seq": self.seq, "sha256": h, "type":"text"}
            self.ctrl.send_json_all({"t":"clip_text","text":text,"meta":meta})
            return

        # 이미지
        img = cb.image()
        if not img.isNull():
            ba = QByteArray()
            from PySide6.QtCore import QBuffer, QIODevice
            buf = QBuffer(ba); buf.open(QIODevice.WriteOnly); img.save(buf, "PNG")
            png = bytes(ba); h = sha256_bytes(png)
            if h == self.last_hash: return
            self.last_hash = h; self.seq += 1
            meta={"origin_id": SELF_ID, "seq": self.seq, "sha256": h, "type":"image"}
            b64 = base64.b64encode(png).decode("ascii")
            self.ctrl.send_json_all({"t":"clip_image","png_b64":b64,"meta":meta})
            return

        # 파일(CF_HDROP)
        urls = md.urls()
        paths = [u.toLocalFile() for u in urls if u.isLocalFile()]
        if not paths and win32clipboard:
            # 보강
            paths = get_clipboard_file_paths_win32()
        if paths:
            # 파일 목록 해시(이름+사이즈)
            try:
                items=[]
                for p in paths:
                    sz = os.path.getsize(p) if os.path.isfile(p) else 0
                    items.append(f"{p}|{sz}")
                payload = "\n".join(sorted(items)).encode("utf-8")
                h = sha256_bytes(payload)
                if h == self.last_hash: return
                self.last_hash = h; self.seq += 1
                meta={"origin_id": SELF_ID, "seq": self.seq, "sha256": h, "type":"files"}
            except Exception:
                meta={"origin_id": SELF_ID, "seq": self.seq, "type":"files"}

            # 클라이언트에게 “서버 클립보드에 파일 있음” 알림 → 클라이언트가 FILE_PORT로 다운로드 후 자신의 클립보드 설정
            self.ctrl.send_json_all({"t":"clip_files","meta":meta})
            return

# ====== 서버 UI ======
class ServerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("원격화면 서버"); self.setFixedSize(380, 220)
        self.start_ts = time.time(); self.ip = get_local_ip()

        self.video = VideoServer(DEFAULT_HOST, VIDEO_PORT)
        self.ctrl  = ControlServer(DEFAULT_HOST, CONTROL_PORT)
        self.files = FileServer(DEFAULT_HOST, FILE_PORT)

        self.ctrl.sig_ctrl_conn.connect(self.on_ctrl_conn)
        self.ctrl.sig_incoming.connect(self.on_ctrl_incoming)

        self.clip_sync = ClipboardSync(self.ctrl)

        self.lbl_ip = QLabel(f"서버 IP: {self.ip}  V:{VIDEO_PORT} / C:{CONTROL_PORT} / F:{FILE_PORT}", alignment=Qt.AlignCenter)
        self.lbl_res = QLabel("원격 해상도: - x -", alignment=Qt.AlignCenter)
        self.lbl_uptime = QLabel("연결 시간: 00:00:00", alignment=Qt.AlignCenter)
        self.lbl_conns = QLabel("제어 연결: 0", alignment=Qt.AlignCenter)

        v=QVBoxLayout()
        v.addWidget(QLabel("서버 실행 중", alignment=Qt.AlignCenter))
        v.addWidget(self.lbl_ip); v.addWidget(self.lbl_res); v.addWidget(self.lbl_uptime); v.addWidget(self.lbl_conns)
        wrap=QWidget(); wrap.setLayout(v); self.setCentralWidget(wrap)

        self.timer = QTimer(self); self.timer.timeout.connect(self.update_uptime); self.timer.start(500)
        self.tray = QSystemTrayIcon(self); self.tray.setIcon(QIcon.fromTheme("application-exit"))
        menu = QMenu(); act = QAction("종료", self); act.triggered.connect(self.close); menu.addAction(act)
        self.tray.setContextMenu(menu); self.tray.show()

    # 수신 제어/클립보드 메시지 처리(클라이언트→서버)
    def on_ctrl_incoming(self, m: dict):
        t = m.get("t")
        if t=="mouse_move":
            SetCursorPos(int(m.get("x",0)), int(m.get("y",0))); return
        if t=="mouse_down":
            btn=m.get("btn","left")
            mouse_event({ "left":MOUSEEVENTF_LEFTDOWN, "right":MOUSEEVENTF_RIGHTDOWN, "middle":MOUSEEVENTF_MIDDLEDOWN }.get(btn,MOUSEEVENTF_LEFTDOWN),0,0,0,0); return
        if t=="mouse_up":
            btn=m.get("btn","left")
            mouse_event({ "left":MOUSEEVENTF_LEFTUP, "right":MOUSEEVENTF_RIGHTUP, "middle":MOUSEEVENTF_MIDDLEUP }.get(btn,MOUSEEVENTF_LEFTUP),0,0,0,0); return
        if t=="mouse_wheel":
            mouse_event(MOUSEEVENTF_WHEEL,0,0,int(m.get("delta",0)),0); return
        if t=="key":
            vk=int(m.get("vk",0)); down=bool(m.get("down",True))
            if vk: keybd_event(vk,0,0 if down else KEYEVENTF_KEYUP,0); return

        # ---- 클립보드 동기화(클라이언트→서버로) ----
        if t=="clip_text":
            apply_clip_text(m.get("text",""), m.get("meta") or {}); return
        if t=="clip_image":
            b64=m.get("png_b64") or ""; data=base64.b64decode(b64) if b64 else b""
            apply_clip_image(data, m.get("meta") or {}); return
        if t=="set_clip_files":
            apply_clip_files(m.get("paths") or [], m.get("meta") or {}); return

    def on_ctrl_conn(self, ok: bool):
        cur = self.lbl_conns.text().split(":")[-1].strip()
        try: n = int(cur)
        except: n = 0
        n = n + 1 if ok else max(0, n-1)
        self.lbl_conns.setText(f"제어 연결: {n}")

    def update_uptime(self):
        el = int(time.time()-self.start_ts); h=el//3600; m=(el%3600)//60; s=el%60
        self.lbl_uptime.setText(f"연결 시간: {h:02d}:{m:02d}:{s:02d}")

    def showEvent(self, e):
        super().showEvent(e); self.video.start(); self.ctrl.start(); self.files.start()
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
