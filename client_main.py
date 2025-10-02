# client_main.py
import sys, time, socket, struct, json, os, ctypes, base64, hashlib, threading, uuid
import numpy as np
import cv2

from PySide6.QtCore import Qt, QThread, Signal, QPoint, QTimer, QByteArray, QObject
from PySide6.QtGui import QImage, QPixmap, QGuiApplication, QClipboard
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget, QLabel, QHBoxLayout, QVBoxLayout, QPushButton, QFrame, QLineEdit, QMessageBox

from common import VIDEO_PORT, CONTROL_PORT, FILE_PORT

META_MIME = "application/x-remote-clip-meta"
SELF_ID = uuid.uuid4().hex

# ---- 유틸 ----
def np_bgr_to_qimage(bgr: np.ndarray) -> QImage:
    h, w, _ = bgr.shape
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    return QImage(rgb.data, w, h, 3*w, QImage.Format_RGB888).copy()

def recv_exact(sock: socket.socket, n: int) -> bytes | None:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)

def sha256_bytes(b: bytes) -> str:
    h = hashlib.sha256(); h.update(b); return h.hexdigest()

# ---- Qt Key -> Windows VK ----
VK = {
    "F1":0x70,"F2":0x71,"F3":0x72,"F4":0x73,"F5":0x74,"F6":0x75,"F7":0x76,"F8":0x77,
    "F9":0x78,"F10":0x79,"F11":0x7A,"F12":0x7B,"F13":0x7C,"F14":0x7D,"F15":0x7E,"F16":0x7F,
    "F17":0x80,"F18":0x81,"F19":0x82,"F20":0x83,"F21":0x84,"F22":0x85,"F23":0x86,"F24":0x87,
    "ESC":0x1B,"TAB":0x09,"ENTER":0x0D,"BACK":0x08,"SPACE":0x20,
    "LEFT":0x25,"UP":0x26,"RIGHT":0x27,"DOWN":0x28,
    "INSERT":0x2D,"DELETE":0x2E,"HOME":0x24,"END":0x23,"PGUP":0x21,"PGDN":0x22,
    "CAPSLOCK":0x14,"NUMLOCK":0x90,"SCROLLLOCK":0x91,"PRINT":0x2C,"PAUSE":0x13,
    "SHIFT":0x10,"CTRL":0x11,"ALT":0x12,"WIN":0x5B,"RWIN":0x5C,"APPS":0x5D,
    "HANGUL":0x15,"HANJA":0x19,
    "NP0":0x60,"NP1":0x61,"NP2":0x62,"NP3":0x63,"NP4":0x64,"NP5":0x65,"NP6":0x66,"NP7":0x67,"NP8":0x68,"NP9":0x69,
    "NP_MUL":0x6A,"NP_ADD":0x6B,"NP_SEP":0x6C,"NP_SUB":0x6D,"NP_DEC":0x6E,"NP_DIV":0x6F,
    "OEM_1":0xBA,"OEM_PLUS":0xBB,"OEM_COMMA":0xBC,"OEM_MINUS":0xBD,"OEM_PERIOD":0xBE,"OEM_2":0xBF,
    "OEM_3":0xC0,"OEM_4":0xDB,"OEM_5":0xDC,"OEM_6":0xDD,"OEM_7":0xDE,
}
def qt_to_vk(e) -> int:
    k=e.key(); mods=e.modifiers()
    if k==Qt.Key_Control: return VK["CTRL"]
    if k==Qt.Key_Shift:   return VK["SHIFT"]
    if k==Qt.Key_Alt:     return VK["ALT"]
    if k==Qt.Key_Meta:    return VK["WIN"]
    if k==Qt.Key_Space:   return VK["SPACE"]
    if k==Qt.Key_Tab:     return VK["TAB"]
    if k in (Qt.Key_Return,Qt.Key_Enter): return VK["ENTER"]
    if k==Qt.Key_Backspace: return VK["BACK"]
    if k==Qt.Key_Escape:    return VK["ESC"]
    if k==Qt.Key_Left: return VK["LEFT"]
    if k==Qt.Key_Right:return VK["RIGHT"]
    if k==Qt.Key_Up:   return VK["UP"]
    if k==Qt.Key_Down: return VK["DOWN"]
    if k==Qt.Key_Insert: return VK["INSERT"]
    if k==Qt.Key_Delete:  return VK["DELETE"]
    if k==Qt.Key_Home:    return VK["HOME"]
    if k==Qt.Key_End:     return VK["END"]
    if k==Qt.Key_PageUp:   return VK["PGUP"]
    if k==Qt.Key_PageDown: return VK["PGDN"]
    if k==Qt.Key_CapsLock:  return VK["CAPSLOCK"]
    if k==Qt.Key_NumLock:   return VK["NUMLOCK"]
    if k==Qt.Key_ScrollLock:return VK["SCROLLLOCK"]
    if k==Qt.Key_Print:     return VK["PRINT"]
    if k==Qt.Key_Pause:     return VK["PAUSE"]
    if k==Qt.Key_Menu:      return VK["APPS"]
    if k==Qt.Key_Hangul:    return VK["HANGUL"]
    if k==Qt.Key_Hangul_Hanja: return VK["HANJA"]
    if Qt.Key_F1 <= k <= Qt.Key_F24: return VK["F"+str(k - Qt.Key_F1 + 1)]
    if (mods & Qt.KeypadModifier):
        if k==Qt.Key_0: return VK["NP0"]
        if k==Qt.Key_1: return VK["NP1"]
        if k==Qt.Key_2: return VK["NP2"]
        if k==Qt.Key_3: return VK["NP3"]
        if k==Qt.Key_4: return VK["NP4"]
        if k==Qt.Key_5: return VK["NP5"]
        if k==Qt.Key_6: return VK["NP6"]
        if k==Qt.Key_7: return VK["NP7"]
        if k==Qt.Key_8: return VK["NP8"]
        if k==Qt.Key_9: return VK["NP9"]
        if k==Qt.Key_Asterisk: return VK["NP_MUL"]
        if k==Qt.Key_Plus:     return VK["NP_ADD"]
        if k==Qt.Key_Minus:    return VK["NP_SUB"]
        if k==Qt.Key_Slash:    return VK["NP_DIV"]
        if k==Qt.Key_Period:   return VK["NP_DEC"]
        if k in (Qt.Key_Return,Qt.Key_Enter): return VK["ENTER"]
    if Qt.Key_0 <= k <= Qt.Key_9: return ord(str(k - Qt.Key_0))
    if Qt.Key_A <= k <= Qt.Key_Z: return ord(chr(k))
    if k==Qt.Key_Semicolon: return VK["OEM_1"]
    if k==Qt.Key_Equal:     return VK["OEM_PLUS"]
    if k==Qt.Key_Comma:     return VK["OEM_COMMA"]
    if k==Qt.Key_Minus:     return VK["OEM_MINUS"]
    if k==Qt.Key_Period:    return VK["OEM_PERIOD"]
    if k==Qt.Key_Slash:     return VK["OEM_2"]
    if k==Qt.Key_QuoteLeft: return VK["OEM_3"]
    if k==Qt.Key_BracketLeft:  return VK["OEM_4"]
    if k==Qt.Key_Backslash:    return VK["OEM_5"]
    if k==Qt.Key_BracketRight: return VK["OEM_6"]
    if k==Qt.Key_Apostrophe:   return VK["OEM_7"]
    return 0

# ---- 로컬(클라이언트) 탐색기 폴더 감지: 커서 아래 창 ----
def get_local_explorer_folder_under_cursor() -> str | None:
    try:
        import win32com.client
    except Exception:
        return None
    try:
        user32 = ctypes.windll.user32
        class POINT(ctypes.Structure):
            _fields_=[("x", ctypes.c_long), ("y", ctypes.c_long)]
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        hwnd = user32.WindowFromPoint(pt)
        GA_ROOT = 2
        hwnd_top = user32.GetAncestor(hwnd, GA_ROOT)
        shell = win32com.client.Dispatch("Shell.Application")
        for w in shell.Windows():
            try:
                if int(w.HWND) == hwnd_top:
                    doc = getattr(w,"Document",None); folder=getattr(doc,"Folder",None)
                    self_obj=getattr(folder,"Self",None); path=getattr(self_obj,"Path",None)
                    if path and os.path.isdir(path): return path
            except Exception: pass
    except Exception:
        return None
    return None

# ---- 영상 수신 ----
class VideoClient(QThread):
    sig_status = Signal(float, int, bool)
    sig_frame  = Signal(QImage, int, int)
    def __init__(self, host: str, port: int):
        super().__init__()
        self.host=host; self.port=port
        self._stop=False; self._sock=None
        self._connected=False; self._conn_ts=None
        self._frame_count=0; self._last_ts=time.time()
    def run(self):
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(5.0); self._sock.connect((self.host,self.port))
            self._sock.settimeout(None); self._connected=True; self._conn_ts=time.time()
        except Exception:
            self.sig_status.emit(0.0,0,False); return
        try:
            while not self._stop:
                hdr = recv_exact(self._sock,12)
                if not hdr: break
                data_len, w, h = struct.unpack(">III", hdr)
                blob = recv_exact(self._sock, data_len)
                if not blob: break
                arr = np.frombuffer(blob, dtype=np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if img is not None:
                    self.sig_frame.emit(np_bgr_to_qimage(img), w, h)
                    self._frame_count += 1
                now=time.time()
                if now - self._last_ts >= 1.0:
                    fps=float(self._frame_count); self._frame_count=0; self._last_ts=now
                    elapsed=int(now - (self._conn_ts or now))
                    self.sig_status.emit(fps, elapsed, self._connected)
        finally:
            try:
                if self._sock: self._sock.close()
            except Exception: pass
            self._connected=False; self.sig_status.emit(0.0,0,False)
    def stop(self): self._stop=True

# ---- 제어 채널(양방향) ----
class ControlClient:
    def __init__(self, host:str, port:int):
        self.host=host; self.port=port
        self.sock=None; self._rx_thread=None; self.on_message=None
        self._lock = threading.Lock()
        self.connect()

    def connect(self):
        self.close()
        try:
            self.sock=socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(5.0); self.sock.connect((self.host,self.port)); self.sock.settimeout(None)
            self._rx_thread = threading.Thread(target=self._recv_loop, daemon=True); self._rx_thread.start()
        except Exception:
            self.sock=None

    def close(self):
        try:
            if self.sock:
                try: self.sock.shutdown(socket.SHUT_RDWR)
                except: pass
                self.sock.close()
        except: pass
        self.sock=None

    def _recv_loop(self):
        try:
            while True:
                hdr = recv_exact(self.sock, 4)
                if not hdr: break
                jlen = struct.unpack(">I", hdr)[0]
                body = recv_exact(self.sock, jlen)
                if not body: break
                msg = json.loads(body.decode("utf-8","ignore"))
                cb = self.on_message
                if cb: cb(msg)
        except Exception:
            pass
        finally:
            self.close()

    def send_json(self, obj:dict):
        if not self.sock:
            self.connect()
            if not self.sock: return
        try:
            raw=json.dumps(obj).encode("utf-8"); head=struct.pack(">I",len(raw))
            with self._lock:
                self.sock.sendall(head+raw)
        except Exception:
            self.connect()

    def send_key(self, vk:int, down:bool):
        if vk: self.send_json({"t":"key","vk":int(vk),"down":bool(down)})

# ---- 파일/폴더 클라이언트 ----
class FileClient:
    def __init__(self, host: str, port: int):
        self.host=host; self.port=port
    def _connect(self, timeout: float = 5.0):
        s=socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout); s.connect((self.host,self.port)); s.settimeout(None); return s
    def get_server_active_folder(self):
        s=self._connect(2.0)
        try:
            req=json.dumps({"cmd":"active_folder"}).encode("utf-8")
            s.sendall(struct.pack(">I",len(req))+req)
            jlen_b=recv_exact(s,4)
            if not jlen_b: return False,""
            jlen=struct.unpack(">I",jlen_b)[0]
            body=recv_exact(s,jlen)
            if not body: return False,""
            resp=json.loads(body.decode("utf-8","ignore"))
            return bool(resp.get("ok",False)), resp.get("path","")
        finally:
            s.close()
    def server_has_clip_files(self):
        try: s=self._connect(1.0)
        except Exception: return False, 0
        try:
            req=json.dumps({"cmd":"probe_clip"}).encode("utf-8")
            s.sendall(struct.pack(">I",len(req))+req)
            jlen_b = recv_exact(s, 4)
            if not jlen_b:
                return False, 0
            jlen = struct.unpack(">I", jlen_b)[0]
            body = recv_exact(s, jlen)
            if not body:
                return False, 0
            resp=json.loads(body.decode("utf-8","ignore"))
            return (resp.get("count",0) > 0), int(resp.get("count",0))
        except Exception:
            return False, 0
        finally:
            try: s.close()
            except: pass
    def upload_clipboard_files(self, target_dir:str|None):
        cb=QGuiApplication.clipboard(); md=cb.mimeData(); urls=md.urls() if md else []
        paths=[u.toLocalFile() for u in urls if u.isLocalFile()]
        if not paths: return False,"클립보드에 파일이 없습니다.",[]
        metas=[]
        for p in paths:
            if not os.path.isfile(p): continue
            metas.append({"name":os.path.basename(p),"size":int(os.path.getsize(p)),"path":p})
        if not metas: return False,"유효한 파일이 없습니다.",[]
        s=self._connect(10.0)
        try:
            head={"cmd":"upload","files":[{"name":m["name"],"size":m["size"]} for m in metas]}
            if target_dir: head["target_dir"]=target_dir
            raw=json.dumps(head).encode("utf-8")
            s.sendall(struct.pack(">I",len(raw))+raw)
            for m in metas:
                with open(m["path"],"rb") as f:
                    while True:
                        b=f.read(1024*256)
                        if not b: break
                        s.sendall(b)
            jlen_b = recv_exact(s, 4)
            if not jlen_b:
                return False, "서버 응답 없음", []
            jlen = struct.unpack(">I", jlen_b)[0]
            ack_raw = recv_exact(s, jlen)
            if not ack_raw:
                return False, "서버 응답 없음", []
            ack=json.loads(ack_raw.decode("utf-8","ignore"))
            return bool(ack.get("ok",False)), ack.get("saved_dir",""), ack.get("saved_paths",[])
        finally:
            s.close()
    def download_server_clipboard_files(self, target_dir:str|None):
        if not target_dir:
            base = os.path.expanduser("~/Downloads")
            target_dir = os.path.join(base, "RemoteClipMirror", time.strftime("%Y%m%d_%H%M%S"))
        os.makedirs(target_dir, exist_ok=True)
        s=self._connect(10.0)
        try:
            req=json.dumps({"cmd":"download_clip"}).encode("utf-8")
            s.sendall(struct.pack(">I",len(req))+req)
            jlen_b = recv_exact(s, 4)
            if not jlen_b:
                return False, "서버 응답 없음", []
            jlen = struct.unpack(">I", jlen_b)[0]
            head_raw = recv_exact(s, jlen)
            if not head_raw:
                return False, "서버 응답 없음", []
            head=json.loads(head_raw.decode("utf-8","ignore"))
            files=head.get("files",[])
            if not files: return False,"서버 클립보드에 파일이 없습니다.",[]
            saved=[]
            for m in files:
                name=os.path.basename(m.get("name","file")); size=int(m.get("size",0))
                dst=os.path.join(target_dir,name)
                with open(dst,"wb") as f:
                    remain=size
                    while remain>0:
                        chunk=s.recv(min(1024*256,remain))
                        if not chunk: raise ConnectionError("file stream interrupted")
                        f.write(chunk); remain-=len(chunk)
                saved.append(dst)
            return True, target_dir, saved
        finally:
            s.close()
    # 원격 클립보드 직접 설정(서버측)
    def set_remote_clip_text(self, text:str, meta:dict):
        s=self._connect(3.0)
        try:
            req=json.dumps({"cmd":"clip_text","text":text,"meta":meta}).encode("utf-8")
            s.sendall(struct.pack(">I",len(req))+req); _=recv_exact(s,4);  # ack skip
        finally:
            s.close()
    def set_remote_clip_image(self, png_bytes:bytes, meta:dict):
        s=self._connect(5.0)
        try:
            b64=base64.b64encode(png_bytes).decode("ascii")
            req=json.dumps({"cmd":"clip_image","png_b64":b64,"meta":meta}).encode("utf-8")
            s.sendall(struct.pack(">I",len(req))+req); _=recv_exact(s,4)
        finally:
            s.close()
    def set_remote_clip_files(self, paths:list[str], meta:dict):
        s=self._connect(5.0)
        try:
            req=json.dumps({"cmd":"set_clip_files","paths":paths,"meta":meta}).encode("utf-8")
            s.sendall(struct.pack(">I",len(req))+req); _=recv_exact(s,4)
        finally:
            s.close()

# ---- 상단 상태바 ----
class TopStatusBar(QFrame):
    def __init__(self):
        super().__init__()
        self.setFrameShape(QFrame.NoFrame); self.setFixedHeight(24)
        self.lbl_time=QLabel("경과 00:00:00"); self.lbl_fps=QLabel("FPS 0"); self.lbl_ip=QLabel("서버: -")
        lay=QHBoxLayout(); lay.setContentsMargins(8,0,8,0); lay.setSpacing(16)
        lay.addWidget(self.lbl_time); lay.addWidget(self.lbl_fps); lay.addWidget(self.lbl_ip); lay.addStretch(1)
        self.setLayout(lay)
    def update_time(self, seconds:int):
        h=seconds//3600; m=(seconds%3600)//60; s=seconds%60
        self.lbl_time.setText(f"경과 {h:02d}:{m:02d}:{s:02d}")
    def update_fps(self, fps:float): self.lbl_fps.setText(f"FPS {int(fps)}")
    def update_ip(self, ip_text:str): self.lbl_ip.setText(f"서버: {ip_text}")

# ---- 원격 뷰 라벨 ----
class ViewerLabel(QLabel):
    sig_mouse = Signal(dict)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True); self.setFocusPolicy(Qt.StrongFocus)
        self.keep_aspect=True; self.remote_size=(0,0)
    def set_keep_aspect(self, on:bool): self.keep_aspect=on
    def set_remote_size(self, w:int, h:int): self.remote_size=(w,h)
    def map_to_remote(self, p:QPoint) -> tuple[int,int]:
        rw,rh=self.remote_size
        if rw<=0 or rh<=0: return (0,0)
        lw,lh=self.width(),self.height()
        if self.keep_aspect:
            r=min(lw/rw, lh/rh); vw=int(rw*r); vh=int(rh*r)
            ox=(lw-vw)//2; oy=(lh-vh)//2
            x=p.x()-ox; y=p.y()-oy
            if vw>0 and vh>0:
                rx=int(max(0,min(x,vw))*rw/vw); ry=int(max(0,min(y,vh))*rh/vh)
            else: rx,ry=0,0
        else:
            rx=int(p.x()*rw/max(1,lw)); ry=int(p.y()*rh/max(1,lh))
        rx=max(0,min(rx,rw-1)); ry=max(0,min(ry,rh-1)); return (rx,ry)
    def mouseMoveEvent(self,e): self.sig_mouse.emit({"t":"move","x":e.position().x(),"y":e.position().y()})
    def mousePressEvent(self,e):
        btn="left" if e.button()==Qt.LeftButton else "right" if e.button()==Qt.RightButton else "middle"
        self.sig_mouse.emit({"t":"down","btn":btn,"x":e.position().x(),"y":e.position().y()})
    def mouseReleaseEvent(self,e):
        btn="left" if e.button()==Qt.LeftButton else "right" if e.button()==Qt.RightButton else "middle"
        self.sig_mouse.emit({"t":"up","btn":btn,"x":e.position().x(),"y":e.position().y()})
    def wheelEvent(self,e): self.sig_mouse.emit({"t":"wheel","delta":e.angleDelta().y()})

# ====== 로컬 클립보드 적용기 ======
class QClipboardMime:
    from PySide6.QtCore import QMimeData
    def __init__(self):
        from PySide6.QtCore import QMimeData
        self.qmime = QMimeData()
    def set_text(self, t:str): self.qmime.setText(t)
    def set_image(self, img: QImage): self.qmime.setImageData(img)
    def set_urls(self, urls:list): self.qmime.setUrls(urls)

def apply_meta(mime, meta: dict):
    try:
        raw = json.dumps(meta).encode("utf-8")
        mime.qmime.setData(META_MIME, QByteArray(raw))
    except Exception:
        pass

def apply_clip_text_local(text: str, meta: dict):
    m=QClipboardMime(); m.set_text(text); apply_meta(m, meta)
    QGuiApplication.clipboard().setMimeData(m.qmime)

def apply_clip_image_local(png_bytes: bytes, meta: dict):
    img = QImage.fromData(png_bytes, "PNG")
    if img.isNull(): return
    m=QClipboardMime(); m.set_image(img); apply_meta(m, meta)
    QGuiApplication.clipboard().setMimeData(m.qmime)

def apply_clip_files_local(paths: list[str], meta: dict):
    from PySide6.QtCore import QUrl
    urls=[QUrl.fromLocalFile(p) for p in paths if os.path.exists(p)]
    if not urls: return
    m=QClipboardMime(); m.set_urls(urls); apply_meta(m, meta)
    QGuiApplication.clipboard().setMimeData(m.qmime)

# ====== 클립보드 감시(클라이언트→서버 전송, 서버→클 수신 반영) ======
class ClipboardSync(QObject):
    def __init__(self, cc: ControlClient, fc: FileClient):
        super().__init__()
        self.cc=cc; self.fc=fc
        self.seq=0; self.last_hash=""
        self._debounce = QTimer(self); self._debounce.setSingleShot(True); self._debounce.setInterval(150)
        self._debounce.timeout.connect(self.on_debounced)
        QGuiApplication.clipboard().dataChanged.connect(self.on_changed)

    def on_changed(self): self._debounce.start()

    def on_debounced(self):
        cb = QGuiApplication.clipboard(); md = cb.mimeData()
        if not md: return

        # 내가 설정한(원격 기원 포함) 변경이면 재송신 금지
        try:
            if md.hasFormat(META_MIME):
                meta = json.loads(bytes(md.data(META_MIME)).decode("utf-8","ignore"))
                if meta.get("origin_id") == SELF_ID:  # 내가 보낸 것
                    return
        except Exception:
            pass

        # 텍스트
        if md.hasText():
            text = md.text(); payload = text.encode("utf-8"); h = sha256_bytes(payload)
            if h == self.last_hash: return
            self.last_hash=h; self.seq+=1
            meta={"origin_id":SELF_ID,"seq":self.seq,"sha256":h,"type":"text"}
            self.cc.send_json({"t":"clip_text","text":text,"meta":meta})
            return

        # 이미지
        img = cb.image()
        if not img.isNull():
            from PySide6.QtCore import QBuffer, QIODevice
            ba=QByteArray(); buf=QBuffer(ba); buf.open(QIODevice.WriteOnly); img.save(buf,"PNG"); png=bytes(ba)
            h=sha256_bytes(png)
            if h == self.last_hash: return
            self.last_hash=h; self.seq+=1
            meta={"origin_id":SELF_ID,"seq":self.seq,"sha256":h,"type":"image"}
            b64=base64.b64encode(png).decode("ascii")
            self.cc.send_json({"t":"clip_image","png_b64":b64,"meta":meta})
            return

        # 파일(CF_HDROP)
        urls = md.urls()
        paths = [u.toLocalFile() for u in urls if u.isLocalFile()]
        if paths:
            try:
                items=[]
                for p in paths:
                    sz = os.path.getsize(p) if os.path.isfile(p) else 0
                    items.append(f"{p}|{sz}")
                payload="\n".join(sorted(items)).encode("utf-8"); h=sha256_bytes(payload)
            except Exception:
                h=""
            if h and h == self.last_hash: return
            self.last_hash=h; self.seq+=1
            meta={"origin_id":SELF_ID,"seq":self.seq,"sha256":h,"type":"files"}

            # 서버로 업로드 → 서버 클립보드를 업로드된 경로로 설정
            ok_path, active_dir = self.fc.get_server_active_folder()
            ok_up, saved_dir, saved_paths = self.fc.upload_clipboard_files(active_dir if ok_path else None)
            if ok_up and saved_paths:
                self.cc.send_json({"t":"set_clip_files","paths":saved_paths,"meta":meta})
            return

# ---- 메인 윈도우 ----
class ClientWindow(QMainWindow):
    def __init__(self, server_ip: str):
        super().__init__()
        self.setWindowTitle("원격 뷰어 클라이언트"); self.resize(1100, 720)
        self.server_ip = server_ip

        # UI
        self.topbar = TopStatusBar(); self.topbar.update_ip(f"{self.server_ip}: V{VIDEO_PORT}/C{CONTROL_PORT}/F{FILE_PORT}")
        self.btn_full=QPushButton("전체크기"); self.btn_full.clicked.connect(self.on_fullscreen)
        self.btn_keep=QPushButton("원격해상도유지"); self.btn_keep.setCheckable(True); self.btn_keep.setChecked(True)
        self.ed_ip=QLineEdit(self.server_ip); self.ed_ip.setFixedWidth(160)
        self.btn_re=QPushButton("재연결"); self.btn_re.clicked.connect(self.on_reconnect)
        self.view = ViewerLabel("원격 화면 수신 대기")
        self.view.setAlignment(Qt.AlignCenter); self.view.setStyleSheet("background:#202020; color:#DDDDDD;")
        self.view.sig_mouse.connect(self.on_mouse_local)
        ctrl=QHBoxLayout(); ctrl.setContentsMargins(8,4,8,4); ctrl.setSpacing(8)
        ctrl.addWidget(self.btn_full); ctrl.addWidget(self.btn_keep); ctrl.addStretch(1)
        ctrl.addWidget(QLabel("서버 IP:")); ctrl.addWidget(self.ed_ip); ctrl.addWidget(self.btn_re)
        v=QVBoxLayout(); v.addWidget(self.topbar); v.addLayout(ctrl); v.addWidget(self.view,1)
        wrap=QWidget(); wrap.setLayout(v); self.setCentralWidget(wrap)

        # 네트워크
        self.vc=VideoClient(self.server_ip, VIDEO_PORT); self.vc.sig_status.connect(self.on_status)
        self.vc.sig_frame.connect(self.on_frame); self.vc.start()
        self.cc=ControlClient(self.server_ip, CONTROL_PORT)
        self.cc.on_message = self.on_ctrl_message
        self.fc=FileClient(self.server_ip, FILE_PORT)

        # 클립보드 동기화(클→서 푸시)
        self.clip_sync = ClipboardSync(self.cc, self.fc)

    # ---- 서버→클 메시지 처리 ----
    def on_ctrl_message(self, m: dict):
        t = m.get("t")
        if t=="clip_text":
            meta=m.get("meta") or {}
            apply_clip_text_local(m.get("text",""), meta)
            self.statusBar().showMessage("서버→클립보드(텍스트) 동기화", 1500)
            return
        if t=="clip_image":
            b64=m.get("png_b64") or ""; data=base64.b64decode(b64) if b64 else b""
            meta=m.get("meta") or {}
            apply_clip_image_local(data, meta)
            self.statusBar().showMessage("서버→클립보드(이미지) 동기화", 1500)
            return
        if t=="clip_files":
            # 서버 클립보드 파일 내려받아 로컬 클립보드에 경로 설정
            target_local = get_local_explorer_folder_under_cursor()
            ok, saved_dir, saved = self.fc.download_server_clipboard_files(target_local)
            if ok and saved:
                meta = m.get("meta") or {}
                apply_clip_files_local(saved, meta)
                self.statusBar().showMessage(f"서버→클립보드(파일 {len(saved)}개) 동기화: {saved_dir}", 3000)
            else:
                self.statusBar().showMessage(saved_dir or "서버 클립보드 파일 없음", 2000)
            return

        # 마우스/키(기존)
        if t=="mouse_move" or t=="mouse_down" or t=="mouse_up" or t=="mouse_wheel" or t=="key":
            # 서버는 현재 이 경로로는 안 보냅니다. (호환을 위해 남김)
            return

    # ---- 상태/프레임 ----
    def on_status(self,fps:float,elapsed:int,connected:bool):
        self.topbar.update_fps(fps); self.topbar.update_time( elapsed if connected else 0 )
        if not connected: self.view.setText("연결 끊김")
    def on_frame(self,qimg:QImage,w:int,h:int):
        self.view.set_remote_size(w,h); self.redraw(qimg)
    def redraw(self,qimg:QImage):
        pm=QPixmap.fromImage(qimg); mode_keep=self.btn_keep.isChecked()
        scaled=pm.scaled(self.view.size(), Qt.KeepAspectRatio if mode_keep else Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        self.view.setPixmap(scaled)
    def resizeEvent(self,e):
        if self.view.pixmap() and not self.view.pixmap().isNull():
            self.redraw(self.view.pixmap().toImage())
        super().resizeEvent(e)

    # ---- 마우스/키: 서버 제어 ----
    def on_mouse_local(self, ev:dict):
        cursor=QPoint(int(ev.get("x",0)), int(ev.get("y",0)))
        rx,ry=self.view.map_to_remote(cursor); t=ev.get("t")
        if t=="move": self.cc.send_json({"t":"mouse_move","x":rx,"y":ry})
        elif t=="down":
            self.cc.send_json({"t":"mouse_move","x":rx,"y":ry})
            self.cc.send_json({"t":"mouse_down","btn":ev.get("btn","left")})
        elif t=="up": self.cc.send_json({"t":"mouse_up","btn":ev.get("btn","left")})
        elif t=="wheel": self.cc.send_json({"t":"mouse_wheel","delta":int(ev.get("delta",0))})

    def keyPressEvent(self, e):
        if e.isAutoRepeat(): return

        # 기존: Ctrl+V 인터셉트(보조 수단)
        if (e.modifiers() & Qt.ControlModifier) and e.key() == Qt.Key_V:
            mods = e.modifiers()
            force_download = bool(mods & Qt.AltModifier)      # Ctrl+Alt+V : 서버→클 강제
            force_upload   = bool(mods & Qt.ShiftModifier)    # Ctrl+Shift+V : 클→서 강제

            cb = QGuiApplication.clipboard(); md = cb.mimeData()
            local_has_files = bool(md and md.urls() and any(u.isLocalFile() for u in md.urls()))

            has_server_files, _ = self.fc.server_has_clip_files()

            if force_download or (has_server_files and not force_upload):
                target_local = get_local_explorer_folder_under_cursor()
                ok_dl, saved_dir, saved = self.fc.download_server_clipboard_files(target_local)
                if ok_dl and saved:
                    apply_clip_files_local(saved, {"origin_id":"server_bypass"})
                    self.statusBar().showMessage(f"클라이언트 저장: {saved_dir} ({len(saved)}개)", 3000)
                else:
                    self.statusBar().showMessage(saved_dir, 3000)
                return

            if force_upload or local_has_files:
                ok_path, active_dir = self.fc.get_server_active_folder()
                ok_up, saved_dir, saved_paths = self.fc.upload_clipboard_files(active_dir if ok_path else None)
                if ok_up and saved_paths:
                    self.cc.send_json({"t":"set_clip_files","paths":saved_paths,"meta":{"origin_id":SELF_ID,"type":"files"}})
                    self.statusBar().showMessage(f"서버 저장: {active_dir if ok_path else saved_dir}", 3000)
                else:
                    self.statusBar().showMessage("업로드 실패", 3000)
                return

        # 일반 키 전송
        vk = qt_to_vk(e)
        if vk: self.cc.send_key(vk, True)

    def keyReleaseEvent(self, e):
        if e.isAutoRepeat(): return
        vk = qt_to_vk(e)
        if vk: self.cc.send_key(vk, False)

    # 기타
    def on_fullscreen(self):
        if self.isFullScreen(): self.showNormal()
        else: self.showFullScreen()
    def on_reconnect(self):
        ip=self.ed_ip.text().strip()
        if not ip:
            QMessageBox.warning(self,"알림","IP를 입력하세요."); return
        self.server_ip=ip
        self.topbar.update_ip(f"{self.server_ip}: V{VIDEO_PORT}/C{CONTROL_PORT}/F{FILE_PORT}")
        try: self.vc.stop(); self.vc.wait(1000)
        except Exception: pass
        self.vc=VideoClient(self.server_ip, VIDEO_PORT); self.vc.sig_status.connect(self.on_status)
        self.vc.sig_frame.connect(self.on_frame); self.vc.start()
        try: self.cc.close()
        except: pass
        self.cc=ControlClient(self.server_ip, CONTROL_PORT); self.cc.on_message=self.on_ctrl_message
        self.fc=FileClient(self.server_ip, FILE_PORT)
        self.clip_sync = ClipboardSync(self.cc, self.fc)
    def closeEvent(self,e):
        try: self.vc.stop(); self.vc.wait(1000)
        except Exception: pass
        try: self.cc.close()
        except Exception: pass
        super().closeEvent(e)

def main():
    server_ip = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    app = QApplication(sys.argv)
    w = ClientWindow(server_ip); w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
