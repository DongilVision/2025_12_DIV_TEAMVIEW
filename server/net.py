# server/net.py
import os, time, socket, select, threading, struct, json, tempfile, zipfile
import numpy as np, cv2
from mss import mss
from PySide6.QtCore import QThread, Signal, QStandardPaths

from utils import recv_exact, send_json
from common import DEFAULT_HOST, VIDEO_PORT, CONTROL_PORT, FILE_PORT, FRAME_FPS, JPEG_QUALITY

# ===== 영상 서버 =====
class VideoServer(QThread):
    sig_conn_changed = Signal(int)       # 현재 영상 연결 수
    sig_res_changed  = Signal(int, int)  # (w,h)
    sig_last_client  = Signal(str)       # 최근(마지막 accept) 클라이언트 IP
    sig_conn_start   = Signal(float)     # 첫 영상 연결 시작 ts(초). 0.0 → 리셋

    def __init__(self, host: str, port: int):
        super().__init__()
        self.host = host; self.port = port
        self._stop = threading.Event()
        self._clients: set[socket.socket] = set()
        self._addr_of: dict[socket.socket, str] = {}
        self._lock = threading.Lock()

    def run(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port)); srv.listen(8); srv.setblocking(False)

        sct = mss(); mon = sct.monitors[1]
        last_frame_ts = 0.0
        frame_interval = 1.0 / max(1, FRAME_FPS)

        try:
            while not self._stop.is_set():
                rlist, _, _ = select.select([srv] + list(self._clients), [], [], 0.01)
                for s in rlist:
                    if s is srv:
                        try:
                            c, addr = srv.accept(); c.setblocking(False)
                            ip = addr[0] if addr else ""
                            with self._lock:
                                self._clients.add(c)
                                self._addr_of[c] = ip
                                if len(self._clients) == 1:
                                    self.sig_conn_start.emit(time.time())   # 첫 연결 시작
                                if ip: self.sig_last_client.emit(ip)
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
                        packet = struct.pack(">III", len(blob), w, h) + blob
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
                self._clients.clear(); self._addr_of.clear()
                self.sig_conn_changed.emit(0)
                self.sig_conn_start.emit(0.0)
                self.sig_last_client.emit("")
            try: srv.close()
            except: pass

    def _drop(self, s: socket.socket):
        try: s.close()
        except: pass
        with self._lock:
            if s in self._clients:
                self._clients.remove(s)
                self._addr_of.pop(s, None)
                if len(self._clients) == 0:
                    self.sig_conn_start.emit(0.0)   # 모두 끊김 → 리셋
                    self.sig_last_client.emit("")
                self.sig_conn_changed.emit(len(self._clients))

    def force_disconnect_all(self):
        with self._lock:
            conns = list(self._clients)
        for s in conns:
            try: s.shutdown(socket.SHUT_RDWR)
            except Exception: pass
            try: s.close()
            except Exception: pass

    def stop(self): self._stop.set()

# ===== 제어 서버 =====
class ControlServer(QThread):
    # UI에서는 표시하지 않지만 강제 끊기에 사용
    def __init__(self, host: str, port: int):
        super().__init__()
        self.host = host; self.port = port
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._clients: set[socket.socket] = set()

    def run(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port)); srv.listen(8); srv.settimeout(0.5)
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

    # ---- Windows 입력 주입 ----
    import ctypes
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

    def _handle_conn(self, sock: socket.socket):
        with self._lock:
            self._clients.add(sock)
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
            with self._lock:
                self._clients.discard(sock)

    def _handle_msg(self, m: dict):
        t = m.get("t")
        if t == "mouse_move":
            self.SetCursorPos(int(m.get("x",0)), int(m.get("y",0))); return
        if t == "mouse_down":
            btn = m.get("btn","left")
            if btn=="left": self.mouse_event(self.MOUSEEVENTF_LEFTDOWN,0,0,0,0)
            elif btn=="right": self.mouse_event(self.MOUSEEVENTF_RIGHTDOWN,0,0,0,0)
            else: self.mouse_event(self.MOUSEEVENTF_MIDDLEDOWN,0,0,0,0); return
            return
        if t == "mouse_up":
            btn = m.get("btn","left")
            if btn=="left": self.mouse_event(self.MOUSEEVENTF_LEFTUP,0,0,0,0)
            elif btn=="right": self.mouse_event(self.MOUSEEVENTF_RIGHTUP,0,0,0,0)
            else: self.mouse_event(self.MOUSEEVENTF_MIDDLEUP,0,0,0,0); return
            return
        if t == "mouse_wheel":
            self.mouse_event(self.MOUSEEVENTF_WHEEL, 0,0, int(m.get("delta",0)), 0); return
        if t == "key":
            vk = int(m.get("vk", 0)); down = bool(m.get("down", True))
            if vk:
                self.keybd_event(vk, 0, 0 if down else self.KEYEVENTF_KEYUP, 0); return
            name = m.get("key","")
            if name == " ": name = "SPACE"
            up = name.upper()
            if len(up)==1 and ("A"<=up<="Z" or "0"<=up<="9"):
                self.keybd_event(ord(up), 0, 0 if down else self.KEYEVENTF_KEYUP, 0); return
            vk2 = self.VK_FALLBACK.get(up,0)
            if vk2: self.keybd_event(vk2, 0, 0 if down else self.KEYEVENTF_KEYUP, 0)

    def force_disconnect_all(self):
        with self._lock:
            conns = list(self._clients)
        for s in conns:
            try: s.shutdown(socket.SHUT_RDWR)
            except Exception: pass
            try: s.close()
            except Exception: pass

# ===== 파일 서버 =====
class FileServer(QThread):
    def __init__(self, host: str, port: int):
        super().__init__()
        self.host = host; self.port = port
        self._stop = threading.Event()

    def run(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port)); srv.listen(8); srv.settimeout(0.5)
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

            if cmd == "ls":                      self._handle_ls(sock, req)
            elif cmd == "upload_to":             self._handle_upload_to(sock, req)
            elif cmd == "upload_tree_to":        self._handle_upload_tree_to(sock, req)
            elif cmd == "download_paths":        self._handle_download_paths(sock, req)
            elif cmd == "download_tree_paths":   self._handle_download_tree_paths(sock, req)
            elif cmd == "download_paths_as_zip": self._handle_download_paths_as_zip(sock, req)
        except Exception:
            pass
        finally:
            try: sock.close()
            except: pass

    # 이하 handlers 동일(생략 없이 사용)
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
        target_dir = os.path.abspath(req.get("target_dir",""))
        files = req.get("files", [])
        saved = []
        try:
            if not target_dir: raise ValueError("target_dir required")
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

    def _handle_upload_tree_to(self, sock, req):
        target_dir = os.path.abspath(req.get("target_dir",""))
        files = req.get("files", [])
        try:
            if not target_dir: raise ValueError("target_dir required")
            os.makedirs(target_dir, exist_ok=True)
            for m in files:
                rel  = m.get("rel","")
                size = int(m.get("size",0))
                if not rel: raise ValueError("rel required")
                dst = os.path.join(target_dir, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst, "wb") as f:
                    remain = size
                    while remain > 0:
                        chunk = sock.recv(min(1024*256, remain))
                        if not chunk: raise ConnectionError("file stream interrupted")
                        f.write(chunk); remain -= len(chunk)
            send_json(sock, {"ok": True, "saved_root": target_dir})
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

    def _handle_download_tree_paths(self, sock, req):
        paths = [os.path.abspath(p) for p in req.get("paths",[])]
        files = []
        for p in paths:
            if os.path.isfile(p):
                try:
                    files.append({"rel": os.path.basename(p), "size": int(os.path.getsize(p)), "path": p})
                except Exception:
                    pass
            elif os.path.isdir(p):
                base = os.path.basename(p.rstrip("\\/")) or p
                for root, _dirs, fnames in os.walk(p):
                    for fn in fnames:
                        fp = os.path.join(root, fn)
                        try:
                            rel_sub = os.path.relpath(fp, p)
                            rel = os.path.join(base, rel_sub)
                            files.append({"rel": rel, "size": int(os.path.getsize(fp)), "path": fp})
                        except Exception:
                            pass
        send_json(sock, {"ok": True, "files":[{"rel":m["rel"],"size":m["size"]} for m in files]})
        for m in files:
            with open(m["path"], "rb") as f:
                while True:
                    buf = f.read(1024*256)
                    if not buf: break
                    sock.sendall(buf)

    def _handle_download_paths_as_zip(self, sock, req):
        paths = [os.path.abspath(p) for p in req.get("paths",[])]
        zip_name = req.get("zip_name") or f"server_bundle_{int(time.time())}.zip"
        tmpdir = QStandardPaths.writableLocation(QStandardPaths.TempLocation) or tempfile.gettempdir()
        os.makedirs(tmpdir, exist_ok=True)
        zpath = os.path.join(tmpdir, zip_name)
        try:
            with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
                for p in paths:
                    if os.path.isfile(p):
                        zf.write(p, arcname=os.path.basename(p))
                    elif os.path.isdir(p):
                        base = os.path.basename(p.rstrip("\\/")) or p
                        for root, _dirs, fnames in os.walk(p):
                            for fn in fnames:
                                fp = os.path.join(root, fn)
                                rel_sub = os.path.relpath(fp, p)
                                zf.write(fp, arcname=os.path.join(base, rel_sub))
            size = os.path.getsize(zpath)
            send_json(sock, {"ok": True, "zip_name": zip_name, "size": int(size)})
            with open(zpath, "rb") as f:
                while True:
                    buf = f.read(1024*256)
                    if not buf: break
                    sock.sendall(buf)
        except Exception as ex:
            send_json(sock, {"ok": False, "error": str(ex)})
        finally:
            try:
                if os.path.exists(zpath): os.remove(zpath)
            except Exception:
                pass
