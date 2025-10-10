# client_main.py
import sys, time, socket, struct, json, os, tempfile, zipfile
import numpy as np
import cv2
from datetime import datetime

from PySide6.QtCore import Qt, QThread, Signal, QPoint
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QHBoxLayout, QVBoxLayout,
    QPushButton, QFrame, QLineEdit, QMessageBox, QSplitter, QProgressBar,
    QTreeWidget, QTreeWidgetItem, QHeaderView, QStyle, QSizePolicy, QDialog
)

from common import VIDEO_PORT, CONTROL_PORT, FILE_PORT

# ===================== 공통 유틸 =====================
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

def send_json(sock: socket.socket, obj: dict):
    raw = json.dumps(obj).encode("utf-8")
    sock.sendall(struct.pack(">I", len(raw)) + raw)

def human_size(n: int) -> str:
    if n is None: return ""
    if n < 1024: return f"{n} B"
    units = ["KB","MB","GB","TB","PB"]
    x = float(n); i = -1
    while x >= 1024 and i < len(units)-1:
        x /= 1024.0; i += 1
    return f"{x:.1f} {units[i]}"

def fmt_mtime(epoch: float|int|None) -> str:
    if not epoch: return ""
    try:
        return datetime.fromtimestamp(float(epoch)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""

# ===================== Qt Key -> Windows VK =====================
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
    k = e.key(); mods = e.modifiers()
    if k == Qt.Key_Control: return VK["CTRL"]
    if k == Qt.Key_Shift:   return VK["SHIFT"]
    if k == Qt.Key_Alt:     return VK["ALT"]
    if k == Qt.Key_Meta:    return VK["WIN"]
    if k == Qt.Key_Space:   return VK["SPACE"]
    if k == Qt.Key_Tab:     return VK["TAB"]
    if k in (Qt.Key_Return, Qt.Key_Enter): return VK["ENTER"]
    if k == Qt.Key_Backspace: return VK["BACK"]
    if k == Qt.Key_Escape:    return VK["ESC"]
    if k == Qt.Key_Left: return VK["LEFT"]
    if k == Qt.Key_Right: return VK["RIGHT"]
    if k == Qt.Key_Up: return VK["UP"]
    if k == Qt.Key_Down: return VK["DOWN"]
    if k == Qt.Key_Insert: return VK["INSERT"]
    if k == Qt.Key_Delete: return VK["DELETE"]
    if k == Qt.Key_Home: return VK["HOME"]
    if k == Qt.Key_End: return VK["END"]
    if k == Qt.Key_PageUp: return VK["PGUP"]
    if k == Qt.Key_PageDown: return VK["PGDN"]
    if k == Qt.Key_CapsLock: return VK["CAPSLOCK"]
    if k == Qt.Key_NumLock:  return VK["NUMLOCK"]
    if k == Qt.Key_ScrollLock: return VK["SCROLLLOCK"]
    if k == Qt.Key_Print: return VK["PRINT"]
    if k == Qt.Key_Pause: return VK["PAUSE"]
    if k == Qt.Key_Menu:  return VK["APPS"]
    if k == Qt.Key_Hangul: return VK["HANGUL"]
    if k == Qt.Key_Hangul_Hanja: return VK["HANJA"]
    if Qt.Key_F1 <= k <= Qt.Key_F24: return VK["F"+str(k - Qt.Key_F1 + 1)]
    if (mods & Qt.KeypadModifier):
        if k == Qt.Key_0: return VK["NP0"]
        if k == Qt.Key_1: return VK["NP1"]
        if k == Qt.Key_2: return VK["NP2"]
        if k == Qt.Key_3: return VK["NP3"]
        if k == Qt.Key_4: return VK["NP4"]
        if k == Qt.Key_5: return VK["NP5"]
        if k == Qt.Key_6: return VK["NP6"]
        if k == Qt.Key_7: return VK["NP7"]
        if k == Qt.Key_8: return VK["NP8"]
        if k == Qt.Key_9: return VK["NP9"]
        if k == Qt.Key_Asterisk: return VK["NP_MUL"]
        if k == Qt.Key_Plus:     return VK["NP_ADD"]
        if k == Qt.Key_Minus:    return VK["NP_SUB"]
        if k == Qt.Key_Slash:    return VK["NP_DIV"]
        if k == Qt.Key_Period:   return VK["NP_DEC"]
    if Qt.Key_0 <= k <= Qt.Key_9: return ord(str(k - Qt.Key_0))
    if Qt.Key_A <= k <= Qt.Key_Z: return ord(chr(k))
    if k == Qt.Key_Semicolon: return VK["OEM_1"]
    if k == Qt.Key_Equal:     return VK["OEM_PLUS"]
    if k == Qt.Key_Comma:     return VK["OEM_COMMA"]
    if k == Qt.Key_Minus:     return VK["OEM_MINUS"]
    if k == Qt.Key_Period:    return VK["OEM_PERIOD"]
    if k == Qt.Key_Slash:     return VK["OEM_2"]
    if k == Qt.Key_QuoteLeft: return VK["OEM_3"]
    if k == Qt.Key_BracketLeft:  return VK["OEM_4"]
    if k == Qt.Key_Backslash:    return VK["OEM_5"]
    if k == Qt.Key_BracketRight: return VK["OEM_6"]
    if k == Qt.Key_Apostrophe:   return VK["OEM_7"]
    return 0

# ===================== 영상 스레드 =====================
class VideoClient(QThread):
    sig_status = Signal(float, int, bool, float)  # fps, elapsed, connected, mbps
    sig_frame  = Signal(QImage, int, int)

    def __init__(self, host: str, port: int):
        super().__init__()
        self.host = host; self.port = port
        self._stop = False; self._sock = None
        self._connected = False; self._conn_ts = None
        self._frame_count = 0; self._last_fps_ts = time.time()
        self._bytes_acc = 0

    def run(self):
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(5.0); self._sock.connect((self.host, self.port))
            self._sock.settimeout(None)
            self._connected = True; self._conn_ts = time.time()
        except Exception:
            self.sig_status.emit(0.0, 0, False, 0.0); return

        try:
            while not self._stop:
                hdr = recv_exact(self._sock, 12)
                if not hdr: break
                data_len, w, h = struct.unpack(">III", hdr)
                blob = recv_exact(self._sock, data_len)
                if not blob: break
                self._bytes_acc += 12 + len(blob)

                arr = np.frombuffer(blob, dtype=np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if img is not None:
                    self.sig_frame.emit(np_bgr_to_qimage(img), w, h)
                    self._frame_count += 1

                now = time.time()
                if now - self._last_fps_ts >= 1.0:
                    fps = float(self._frame_count); self._frame_count = 0
                    elapsed = int(now - (self._conn_ts or now))
                    mbps = (self._bytes_acc * 8.0) / 1_000_000.0
                    self._bytes_acc = 0
                    self._last_fps_ts = now
                    self.sig_status.emit(fps, elapsed, self._connected, mbps)
        finally:
            try:
                if self._sock: self._sock.close()
            except Exception: pass
            self._connected = False; self.sig_status.emit(0.0, 0, False, 0.0)

    def stop(self): self._stop = True

# ===================== 제어 송신 =====================
class ControlClient:
    def __init__(self, host:str, port:int):
        self.host=host; self.port=port; self.sock=None; self.connect()

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

    def send_json(self, obj:dict):
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

    def send_key(self, vk:int, down:bool):
        if vk: self.send_json({"t":"key","vk":int(vk),"down":bool(down)})

# ===================== 파일/디렉토리 클라이언트 =====================
class FileClient:
    def __init__(self, host: str, port: int):
        self.host = host; self.port = port

    def _connect(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5.0); s.connect((self.host, self.port)); s.settimeout(None)
        return s

    def list_dir_server(self, path:str|None=None):
        s = self._connect()
        try:
            send_json(s, {"cmd":"ls","path": path or ""})
            jlen = struct.unpack(">I", recv_exact(s,4))[0]
            resp = json.loads(recv_exact(s, jlen).decode("utf-8","ignore"))
            return resp
        finally:
            s.close()

    def upload_to_dir(self, target_dir:str, local_paths:list[str], progress=None):
        metas = []
        for p in local_paths:
            if os.path.isfile(p):
                metas.append({"name": os.path.basename(p), "size": int(os.path.getsize(p)), "path": p})
        if not metas: return (False, "no valid files")
        total = sum(m["size"] for m in metas); done = 0
        s = self._connect()
        try:
            head = {"cmd":"upload_to","target_dir":target_dir,"files":[{"name":m["name"],"size":m["size"]} for m in metas]}
            send_json(s, head)
            for m in metas:
                with open(m["path"], "rb") as f:
                    while True:
                        buf = f.read(1024*256)
                        if not buf: break
                        s.sendall(buf)
                        done += len(buf)
                        if progress: progress(done, total)
            jlen = struct.unpack(">I", recv_exact(s,4))[0]
            ack = json.loads(recv_exact(s, jlen).decode("utf-8","ignore"))
            return (bool(ack.get("ok")), "OK" if ack.get("ok") else ack.get("error",""))
        finally:
            s.close()

    def upload_tree_to(self, target_dir:str, local_paths:list[str], progress=None):
        entries = []
        for p in local_paths:
            p = os.path.abspath(p)
            if os.path.isfile(p):
                entries.append({"rel": os.path.basename(p), "size": int(os.path.getsize(p)), "src": p})
            elif os.path.isdir(p):
                base = os.path.basename(p.rstrip("\\/")) or p
                for root, _dirs, fnames in os.walk(p):
                    for fn in fnames:
                        fp = os.path.join(root, fn)
                        rel_sub = os.path.relpath(fp, p)
                        rel = os.path.join(base, rel_sub)
                        entries.append({"rel": rel, "size": int(os.path.getsize(fp)), "src": fp})
        if not entries: return (False, "no files")
        total = sum(e["size"] for e in entries); done = 0
        s = self._connect()
        try:
            head = {"cmd":"upload_tree_to","target_dir":target_dir,"files":[{"rel":e["rel"],"size":e["size"]} for e in entries]}
            send_json(s, head)
            for e in entries:
                with open(e["src"], "rb") as f:
                    while True:
                        buf = f.read(1024*256)
                        if not buf: break
                        s.sendall(buf)
                        done += len(buf)
                        if progress: progress(done, total)
            jlen = struct.unpack(">I", recv_exact(s,4))[0]
            ack = json.loads(recv_exact(s, jlen).decode("utf-8","ignore"))
            return (bool(ack.get("ok")), "OK" if ack.get("ok") else ack.get("error",""))
        finally:
            s.close()

    def download_paths(self, server_paths:list[str], local_target_dir:str, progress=None):
        os.makedirs(local_target_dir, exist_ok=True)
        s = self._connect()
        try:
            send_json(s, {"cmd":"download_paths","paths": server_paths})
            jlen = struct.unpack(">I", recv_exact(s,4))[0]
            head = json.loads(recv_exact(s, jlen).decode("utf-8","ignore"))
            if not head.get("ok"): return (False, head.get("error",""))
            files = head.get("files", [])
            total = sum(int(m["size"]) for m in files); done = 0
            for m in files:
                name = os.path.basename(m["name"])
                size = int(m["size"])
                dst = os.path.join(local_target_dir, name)
                with open(dst, "wb") as f:
                    remain = size
                    while remain > 0:
                        chunk = s.recv(min(1024*256, remain))
                        if not chunk: raise ConnectionError("file stream interrupted")
                        f.write(chunk)
                        remain -= len(chunk); done += len(chunk)
                        if progress: progress(done, total)
            return (True, "OK")
        finally:
            s.close()

    def download_tree_paths(self, server_paths:list[str], local_target_dir:str, progress=None):
        os.makedirs(local_target_dir, exist_ok=True)
        s = self._connect()
        try:
            send_json(s, {"cmd":"download_tree_paths","paths": server_paths})
            jlen = struct.unpack(">I", recv_exact(s,4))[0]
            head = json.loads(recv_exact(s, jlen).decode("utf-8","ignore"))
            if not head.get("ok"): return (False, head.get("error",""))
            files = head.get("files", [])
            total = sum(int(m["size"]) for m in files); done = 0
            for m in files:
                rel = m["rel"]; size = int(m["size"])
                dst = os.path.join(local_target_dir, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst, "wb") as f:
                    remain = size
                    while remain > 0:
                        chunk = s.recv(min(1024*256, remain))
                        if not chunk: raise ConnectionError("file stream interrupted")
                        f.write(chunk)
                        remain -= len(chunk); done += len(chunk)
                        if progress: progress(done, total)
            return (True, "OK")
        finally:
            s.close()

    def download_paths_as_zip(self, server_paths:list[str], local_target_dir:str, zip_name:str|None=None, progress=None):
        os.makedirs(local_target_dir, exist_ok=True)
        s = self._connect()
        try:
            send_json(s, {"cmd":"download_paths_as_zip","paths": server_paths, "zip_name": zip_name or ""})
            jlen = struct.unpack(">I", recv_exact(s,4))[0]
            head = json.loads(recv_exact(s, jlen).decode("utf-8","ignore"))
            if not head.get("ok"): return (False, head.get("error",""))
            name = head.get("zip_name") or "bundle.zip"
            size = int(head.get("size", 0))
            dst = os.path.join(local_target_dir, name)
            done = 0
            with open(dst, "wb") as f:
                remain = size
                while remain > 0:
                    chunk = s.recv(min(1024*256, remain))
                    if not chunk: raise ConnectionError("zip stream interrupted")
                    f.write(chunk)
                    remain -= len(chunk); done += len(chunk)
                    if progress: progress(done, size)
            return (True, "OK")
        finally:
            s.close()

    def upload_zip_of_local(self, target_dir:str, src_paths:list[str], zip_name:str|None=None, progress=None):
        if not src_paths: return (False, "no source")
        tmp_dir = tempfile.gettempdir()
        if not zip_name:
            base = os.path.basename(os.path.abspath(src_paths[0])).rstrip("\\/")
            zip_name = f"{base}_{int(time.now())}.zip"
        zpath = os.path.join(tmp_dir, zip_name)
        try:
            with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
                for p in src_paths:
                    p = os.path.abspath(p)
                    if os.path.isfile(p):
                        zf.write(p, arcname=os.path.basename(p))
                    elif os.path.isdir(p):
                        base = os.path.basename(p.rstrip("\\/")) or p
                        for root, _dirs, fnames in os.walk(p):
                            for fn in fnames:
                                fp = os.path.join(root, fn)
                                rel_sub = os.path.relpath(fp, p)
                                arc = os.path.join(base, rel_sub)
                                zf.write(fp, arcname=arc)
            return self.upload_to_dir(target_dir, [zpath], progress=progress)
        finally:
            try:
                if os.path.exists(zpath):
                    os.remove(zpath)
            except Exception:
                pass

# ===================== 상단 헤더바/배지 =====================
class Badge(QLabel):
    def __init__(self, text=""):
        super().__init__(text)
        self.setObjectName("Badge")
        self.setMinimumHeight(28)
        self.setAlignment(Qt.AlignCenter)
        self.setContentsMargins(10, 3, 10, 3)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)

class TopHeader(QFrame):
    def __init__(self, on_fullscreen, on_toggle_transfer, on_reconnect, on_exit):
        super().__init__()
        self.setObjectName("TopHeader")
        self.setFixedHeight(56)

        # 좌측 타이틀
        icon_lbl = QLabel()
        icon_lbl.setPixmap(self.style().standardIcon(QStyle.SP_ComputerIcon).pixmap(20,20))
        title = QLabel("원격 제어"); title.setObjectName("Title")
        left = QHBoxLayout(); left.setContentsMargins(12,0,0,0); left.setSpacing(8)
        left.addWidget(icon_lbl); left.addWidget(title)
        left_wrap = QWidget(); left_wrap.setLayout(left)

        # 중앙 배지
        self.badge_time = Badge("⏱ 00:00:00")
        self.badge_bw   = Badge("⇅ 0 Mbps")
        self.badge_ip   = Badge("서버 IP: -")
        center = QHBoxLayout(); center.setContentsMargins(0,0,0,0); center.setSpacing(8)
        center.addStretch(1); center.addWidget(self.badge_time); center.addWidget(self.badge_bw); center.addWidget(self.badge_ip); center.addStretch(1)
        center_wrap = QWidget(); center_wrap.setLayout(center)

        # 우측 버튼
        self.btn_full = QPushButton("전체 화면"); self.btn_full.clicked.connect(on_fullscreen)
        self.btn_transfer = QPushButton("파일 전달"); self.btn_transfer.setCheckable(True); self.btn_transfer.clicked.connect(on_toggle_transfer)
        self.btn_re = QPushButton("재연결"); self.btn_re.clicked.connect(on_reconnect)
        self.btn_exit = QPushButton("원격 종료"); self.btn_exit.setObjectName("btnExit"); self.btn_exit.clicked.connect(on_exit)
        right = QHBoxLayout(); right.setContentsMargins(0,0,12,0); right.setSpacing(8)
        for b in [self.btn_full, self.btn_transfer, self.btn_re, self.btn_exit]:
            right.addWidget(b)
        right_wrap = QWidget(); right_wrap.setLayout(right)

        lay = QHBoxLayout(self); lay.setContentsMargins(0,0,0,0); lay.setSpacing(0)
        lay.addWidget(left_wrap, 0)
        lay.addWidget(center_wrap, 1)
        lay.addWidget(right_wrap, 0)

    def update_time(self, seconds:int):
        h=seconds//3600; m=(seconds%3600)//60; s=seconds%60
        self.badge_time.setText(f"⏱ {h:02d}:{m:02d}:{s:02d}")
    def update_bw(self, mbps:float):
        self.badge_bw.setText(f"⇅ {mbps:.0f} Mbps")
    def update_ip(self, ip_text:str):
        self.badge_ip.setText(f"서버 IP: {ip_text}")

# ===================== 원격 화면 라벨 =====================
class ViewerLabel(QLabel):
    sig_mouse = Signal(dict)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ViewerLabel")
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.remote_size = (0,0)
        self.setAlignment(Qt.AlignCenter)
        self.setText("원격 화면\n\n원격 PC 화면이 여기에 표시됩니다.")
    def set_remote_size(self, w:int, h:int): self.remote_size = (w,h)
    def map_to_remote(self, p: QPoint) -> tuple[int,int]:
        rw, rh = self.remote_size
        if rw<=0 or rh<=0: return (0,0)
        lw, lh = self.width(), self.height()
        r = min(lw / rw, lh / rh)
        vw = int(rw * r); vh = int(rh * r)
        ox = (lw - vw)//2; oy = (lh - vh)//2
        x = (p.x() - ox); y = (p.y() - oy)
        if vw>0 and vh>0:
            rx = int(max(0, min(x, vw)) * rw / vw)
            ry = int(max(0, min(y, vh)) * rh / vh)
        else:
            rx, ry = 0, 0
        return max(0,min(rx,rw-1)), max(0,min(ry,rh-1))
    def mouseMoveEvent(self, e):  self.sig_mouse.emit({"t":"move","x":e.position().x(),"y":e.position().y()})
    def mousePressEvent(self, e):
        btn = "left" if e.button()==Qt.LeftButton else "right" if e.button()==Qt.RightButton else "middle"
        self.sig_mouse.emit({"t":"down","btn":btn,"x":e.position().x(),"y":e.position().y()})
    def mouseReleaseEvent(self, e):
        btn = "left" if e.button()==Qt.LeftButton else "right" if e.button()==Qt.RightButton else "middle"
        self.sig_mouse.emit({"t":"up","btn":btn,"x":e.position().x(),"y":e.position().y()})
    def wheelEvent(self, e): self.sig_mouse.emit({"t":"wheel","delta":e.angleDelta().y()})

# ===================== 파일 테이블 =====================
class FileTable(QTreeWidget):
    sig_copy = Signal()
    sig_paste = Signal()
    def __init__(self):
        super().__init__()
        self.setColumnCount(4)
        self.setHeaderLabels(["이름", "수정 날짜", "유형", "크기"])
        self.header().setStretchLastSection(False)
        self.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.header().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.setSortingEnabled(True)
        self.setRootIsDecorated(False)
        self.setAlternatingRowColors(True)
        self.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.dir_icon = self.style().standardIcon(QStyle.SP_DirIcon)
        self.file_icon = self.style().standardIcon(QStyle.SP_FileIcon)
        self.setUniformRowHeights(True)
    def keyPressEvent(self, e):
        if (e.modifiers() & Qt.ControlModifier) and e.key()==Qt.Key_C:
            self.sig_copy.emit(); return
        if (e.modifiers() & Qt.ControlModifier) and e.key()==Qt.Key_V:
            self.sig_paste.emit(); return
        super().keyPressEvent(e)
    def add_entry(self, name:str, is_dir:bool, full_path:str, size:int|None, mtime:float|None):
        ftype = "폴더" if is_dir else (f"{os.path.splitext(name)[1][1:].upper()} 파일" if os.path.splitext(name)[1] else "파일")
        size_str = "" if is_dir else human_size(size or 0)
        mtime_str = fmt_mtime(mtime)
        it = QTreeWidgetItem([name, mtime_str, ftype, size_str])
        it.setData(0, Qt.UserRole, {"name": name, "is_dir": is_dir, "path": full_path})
        it.setIcon(0, self.dir_icon if is_dir else self.file_icon)
        it.setData(1, Qt.UserRole+1, mtime or 0.0)
        it.setData(3, Qt.UserRole+1, size or (0 if is_dir else 0))
        it.setTextAlignment(3, Qt.AlignRight | Qt.AlignVCenter)
        self.addTopLevelItem(it)
        return it

# ===================== 전송 스레드 =====================
class TransferThread(QThread):
    prog = Signal(int, int)     # done, total
    done = Signal(bool, str)    # ok, msg
    def __init__(self, op_callable):
        super().__init__()
        self._op = op_callable
    def run(self):
        def cb(done, total):
            self.prog.emit(int(done), int(total))
        try:
            ok, msg = self._op(cb)
        except Exception as ex:
            ok, msg = False, str(ex)
        self.done.emit(bool(ok), str(msg))

# ===================== 파일 전달 페이지 =====================
class FileTransferPage(QWidget):
    def __init__(self, fc: 'FileClient', parent=None):
        super().__init__(parent)
        self.fc = fc
        self.clip = None
        self._xfer_thread = None

        # 좌(서버)
        self.lbl_left = QLabel("서버 경로:")
        self.ed_left = QLineEdit(); self.ed_left.setReadOnly(True)
        self.btn_left_send = QPushButton("전달"); self.btn_left_zip = QPushButton("ZIP으로 전달")
        self.btn_left_send.setEnabled(False); self.btn_left_zip.setEnabled(False)
        self.left_table = FileTable()
        self.left_table.sig_copy.connect(self.copy_from_server)
        self.left_table.sig_paste.connect(self.paste_to_server)
        self.left_table.itemSelectionChanged.connect(self.update_buttons)

        # 우(로컬)
        self.lbl_right = QLabel("클라이언트 경로:")
        self.ed_right = QLineEdit(); self.ed_right.setReadOnly(True)
        self.btn_right_send = QPushButton("전달"); self.btn_right_zip = QPushButton("ZIP으로 전달")
        self.btn_right_send.setEnabled(False); self.btn_right_zip.setEnabled(False)
        self.right_table = FileTable()
        self.right_table.sig_copy.connect(self.copy_from_local)
        self.right_table.sig_paste.connect(self.paste_to_local)
        self.right_table.itemSelectionChanged.connect(self.update_buttons)

        # 버튼 동작 연결
        self.btn_left_send.clicked.connect(self.on_left_send)
        self.btn_left_zip.clicked.connect(self.on_left_zip)
        self.btn_right_send.clicked.connect(self.on_right_send)
        self.btn_right_zip.clicked.connect(self.on_right_zip)

        # 레이아웃
        header_l = QHBoxLayout(); header_l.addWidget(self.lbl_left); header_l.addWidget(self.ed_left, 1); header_l.addWidget(self.btn_left_send); header_l.addWidget(self.btn_left_zip)
        header_r = QHBoxLayout(); header_r.addWidget(self.lbl_right); header_r.addWidget(self.ed_right, 1); header_r.addWidget(self.btn_right_send); header_r.addWidget(self.btn_right_zip)
        left_wrap = QVBoxLayout(); left_wrap.addLayout(header_l); left_wrap.addWidget(self.left_table, 1)
        right_wrap = QVBoxLayout(); right_wrap.addLayout(header_r); right_wrap.addWidget(self.right_table, 1)
        left_w = QWidget(); left_w.setLayout(left_wrap)
        right_w = QWidget(); right_w.setLayout(right_wrap)
        spl = QSplitter(); spl.addWidget(left_w); spl.addWidget(right_w); spl.setSizes([600, 600])

        # 진행률
        self.prog = QProgressBar(); self.prog.setRange(0,100); self.prog.setValue(0)
        self.lbl_prog = QLabel("")
        prog_lay = QHBoxLayout(); prog_lay.addWidget(QLabel("전송 진행:")); prog_lay.addWidget(self.prog, 1); prog_lay.addWidget(self.lbl_prog)

        root = QVBoxLayout(); root.setContentsMargins(8,8,8,8); root.addWidget(spl, 1); root.addLayout(prog_lay)
        self.setLayout(root)

        # 초기 경로
        self.server_cwd = None
        self.local_cwd  = os.path.expanduser("~")
        self.refresh_server(self.server_cwd)
        self.refresh_local(self.local_cwd)

        # 더블클릭 탐색
        self.left_table.itemDoubleClicked.connect(self.on_double_left)
        self.right_table.itemDoubleClicked.connect(self.on_double_right)

    # 상태 질의/대기
    def has_running_transfer(self) -> bool:
        return (self._xfer_thread is not None) and self._xfer_thread.isRunning()
    def wait_transfer_finish(self, timeout_ms: int | None = None):
        if self._xfer_thread is not None:
            if timeout_ms is None: self._xfer_thread.wait()
            else: self._xfer_thread.wait(timeout_ms)

    # 목록 갱신
    def refresh_server(self, path: str|None):
        resp = self.fc.list_dir_server(path)
        self.left_table.clear()
        if not resp.get("ok"):
            self.ed_left.setText(resp.get("error","에러")); return
        self.server_cwd = resp["path"]; self.ed_left.setText(self.server_cwd)
        up = os.path.dirname(self.server_cwd)
        if up and up != self.server_cwd:
            it = self.left_table.add_entry("..", True, up, None, None)
            it.setForeground(0, self.palette().brush(self.foregroundRole()))
        items = resp.get("items", [])
        items.sort(key=lambda x:(not x.get("is_dir",False), x.get("name","").lower()))
        for m in items:
            full = os.path.join(self.server_cwd, m["name"])
            self.left_table.add_entry(m["name"], bool(m["is_dir"]), full, int(m.get("size",0)), float(m.get("mtime",0)))
        self.left_table.sortItems(0, Qt.AscendingOrder)

    def refresh_local(self, path: str):
        path = os.path.abspath(path)
        self.local_cwd = path; self.ed_right.setText(self.local_cwd)
        self.right_table.clear()
        up = os.path.dirname(self.local_cwd)
        if up and up != self.local_cwd:
            it = self.right_table.add_entry("..", True, up, None, None)
            it.setForeground(0, self.palette().brush(self.foregroundRole()))
        try:
            with os.scandir(self.local_cwd) as iters:
                entries = []
                for e in iters:
                    try:
                        st = e.stat()
                        entries.append({"name": e.name,"is_dir": e.is_dir(),"size": int(st.st_size),"mtime": float(st.st_mtime)})
                    except Exception:
                        pass
            entries.sort(key=lambda x:(not x["is_dir"], x["name"].lower()))
            for m in entries:
                full = os.path.join(self.local_cwd, m["name"])
                self.right_table.add_entry(m["name"], bool(m["is_dir"]), full, int(m.get("size",0)), float(m.get("mtime",0)))
            self.right_table.sortItems(0, Qt.AscendingOrder)
        except Exception as ex:
            self.right_table.addTopLevelItem(QTreeWidgetItem([f"[ERROR] {ex!s}","","",""]))

    # 더블클릭 이동
    def on_double_left(self, item: QTreeWidgetItem):
        meta = item.data(0, Qt.UserRole)
        if meta and meta.get("is_dir"):
            self.refresh_server(meta["path"])
    def on_double_right(self, item: QTreeWidgetItem):
        meta = item.data(0, Qt.UserRole)
        if meta and meta.get("is_dir"):
            self.refresh_local(meta["path"])

    # 버튼 활성화
    def update_buttons(self):
        def has_valid(items):
            for it in items:
                meta = it.data(0, Qt.UserRole)
                if meta and meta.get("name") != "..":
                    return True
            return False
        self.btn_left_send.setEnabled(has_valid(self.left_table.selectedItems()))
        self.btn_left_zip.setEnabled(has_valid(self.left_table.selectedItems()))
        self.btn_right_send.setEnabled(has_valid(self.right_table.selectedItems()))
        self.btn_right_zip.setEnabled(has_valid(self.right_table.selectedItems()))

    # 복사/붙여넣기 (파일만)
    def copy_from_server(self):
        paths = []
        for it in self.left_table.selectedItems():
            meta = it.data(0, Qt.UserRole)
            if meta and meta.get("name")!=".." and not meta.get("is_dir"):
                paths.append(meta["path"])
        if not paths:
            self.window().statusBar().showMessage("서버: 파일을 선택하세요(폴더 제외).", 3000); return
        self.clip = {"type":"server", "paths": paths}
        self.window().statusBar().showMessage(f"서버에서 {len(paths)}개 복사됨.", 3000)

    def paste_to_server(self):
        if not self.clip or self.clip.get("type")!="local":
            self.window().statusBar().showMessage("로컬에서 복사(Ctrl+C) 후 서버 창에 붙여넣기(Ctrl+V).", 3000); return
        self.run_transfer(lambda cb: self.fc.upload_to_dir(self.server_cwd, self.clip["paths"], progress=cb),
                          after=lambda ok: self.refresh_server(self.server_cwd))

    def copy_from_local(self):
        paths = []
        for it in self.right_table.selectedItems():
            meta = it.data(0, Qt.UserRole)
            if meta and meta.get("name")!=".." and not meta.get("is_dir"):
                paths.append(meta["path"])
        if not paths:
            self.window().statusBar().showMessage("클라이언트: 파일을 선택하세요(폴더 제외).", 3000); return
        self.clip = {"type":"local", "paths": paths}
        self.window().statusBar().showMessage(f"클라이언트에서 {len(paths)}개 복사됨.", 3000)

    def paste_to_local(self):
        if not self.clip or self.clip.get("type")!="server":
            self.window().statusBar().showMessage("서버에서 복사(Ctrl+C) 후 클라이언트 창에 붙여넣기(Ctrl+V).", 3000); return
        self.run_transfer(lambda cb: self.fc.download_paths(self.clip["paths"], self.local_cwd, progress=cb),
                          after=lambda ok: self.refresh_local(self.local_cwd))

    # 전달 버튼(폴더 지원)
    def _selected_paths(self, table: FileTable):
        return [it.data(0, Qt.UserRole)["path"] for it in table.selectedItems()
                if it.data(0, Qt.UserRole) and it.data(0, Qt.UserRole)["name"]!=".."]

    def on_left_send(self):
        sel = self._selected_paths(self.left_table)
        if not sel: return
        self.run_transfer(lambda cb: self.fc.download_tree_paths(sel, self.local_cwd, progress=cb),
                          after=lambda ok: self.refresh_local(self.local_cwd))

    def on_left_zip(self):
        sel = self._selected_paths(self.left_table)
        if not sel: return
        self.run_transfer(lambda cb: self.fc.download_paths_as_zip(sel, self.local_cwd, zip_name=None, progress=cb),
                          after=lambda ok: self.refresh_local(self.local_cwd))

    def on_right_send(self):
        sel = self._selected_paths(self.right_table)
        if not sel: return
        self.run_transfer(lambda cb: self.fc.upload_tree_to(self.server_cwd, sel, progress=cb),
                          after=lambda ok: self.refresh_server(self.server_cwd))

    def on_right_zip(self):
        sel = self._selected_paths(self.right_table)
        if not sel: return
        self.run_transfer(lambda cb: self.fc.upload_zip_of_local(self.server_cwd, sel, zip_name=None, progress=cb),
                          after=lambda ok: self.refresh_server(self.server_cwd))

    # 전송 실행/진행률 UI
    def run_transfer(self, op_callable, after=None):
        if self.has_running_transfer():
            self.window().statusBar().showMessage("이미 전송 작업이 실행 중입니다.", 3000)
            return
        self.setEnabled_controls(False)
        self.prog.setValue(0); self.lbl_prog.setText("남은 100%")

        th = TransferThread(op_callable)
        th.setParent(self)
        self._xfer_thread = th

        th.prog.connect(self.on_progress)

        def on_done(ok, msg):
            try:
                self.on_finish(ok, msg)
                if after: after(ok)
            finally:
                self.setEnabled_controls(True)
                th.deleteLater()
                self._xfer_thread = None

        th.done.connect(on_done)
        th.start()

    def setEnabled_controls(self, enabled: bool):
        for w in [self.left_table, self.right_table, self.btn_left_send, self.btn_left_zip, self.btn_right_send, self.btn_right_zip]:
            w.setEnabled(enabled)

    def on_progress(self, done:int, total:int):
        pct = int((done * 100 / total)) if total>0 else 0
        self.prog.setValue(pct)
        self.lbl_prog.setText(f"남은 {max(0, 100 - pct)}%")

    def on_finish(self, ok:bool, msg:str):
        if ok:
            self.lbl_prog.setText("전송 완료")
            self.window().statusBar().showMessage("전송 완료", 3000)
        else:
            self.lbl_prog.setText("전송 실패")
            self.window().statusBar().showMessage("전송 실패: " + msg, 5000)

# ===================== 간단 스택 위젯 =====================
class QStackedWidgetSafe(QWidget):
    def __init__(self):
        super().__init__()
        self._lay = QVBoxLayout(self); self._lay.setContentsMargins(0,0,0,0)
        self._stack = []; self._idx = 0
    def addWidget(self, w: QWidget):
        if self._stack:
            w.setVisible(False)
        self._stack.append(w); self._lay.addWidget(w)
    def setCurrentIndex(self, i: int):
        if i<0 or i>=len(self._stack): return
        self._stack[self._idx].setVisible(False)
        self._idx = i
        self._stack[self._idx].setVisible(True)
    def currentIndex(self): return self._idx
    def widget(self, i:int): return self._stack[i]

# ===================== 메인 윈도우 =====================
class ClientWindow(QMainWindow):
    def __init__(self, server_ip: str):
        super().__init__()
        self.setWindowTitle("원격 뷰어 클라이언트")
        self.resize(1180, 760)
        self.server_ip = server_ip

        # 상단 헤더
        self.header = TopHeader(self.on_fullscreen, self.toggle_transfer_page, self.on_reconnect, self.on_exit)
        self.header.update_ip(f"{self.server_ip}: V{VIDEO_PORT} / C{CONTROL_PORT} / F{FILE_PORT}")

        # 페이지 1: 원격 뷰어
        self.view = ViewerLabel()
        viewer_layout = QVBoxLayout(); viewer_layout.setContentsMargins(12,12,12,12); viewer_layout.addWidget(self.view, 1)
        self.page_viewer = QWidget(); self.page_viewer.setLayout(viewer_layout)

        # 페이지 2: 파일 전달
        self.fc = FileClient(self.server_ip, FILE_PORT)
        self.page_transfer = FileTransferPage(self.fc)

        # 스택
        self.stack = QStackedWidgetSafe()
        self.stack.addWidget(self.page_viewer)   # index 0
        self.stack.addWidget(self.page_transfer) # index 1

        root = QVBoxLayout(); root.setContentsMargins(0,0,0,0)
        root.addWidget(self.header)
        root.addWidget(self.stack, 1)
        wrap = QWidget(); wrap.setLayout(root); self.setCentralWidget(wrap)

        # 네트워크
        self.vc = VideoClient(self.server_ip, VIDEO_PORT)
        self.vc.sig_status.connect(self.on_status)
        self.vc.sig_frame.connect(self.on_frame)
        self.vc.start()

        self.cc = ControlClient(self.server_ip, CONTROL_PORT)
        self.view.setFocusPolicy(Qt.StrongFocus)

    def toggle_transfer_page(self):
        checked = self.header.btn_transfer.isChecked()
        self.stack.setCurrentIndex(1 if checked else 0)
        if checked:
            self.statusBar().showMessage("파일 전달 모드: 좌(서버) / 우(클라이언트). Ctrl+C / Ctrl+V 또는 상단 버튼 사용", 5000)
        else:
            self.statusBar().clearMessage()

    def on_status(self, fps:float, elapsed:int, connected:bool, mbps:float):
        self.header.update_time(elapsed if connected else 0)
        self.header.update_bw(mbps)
        if not connected and self.stack.currentIndex()==0:
            self.view.setText("연결 끊김")

    def on_frame(self, qimg:QImage, w:int, h:int):
        if self.stack.currentIndex()==0:
            self.view.set_remote_size(w,h)
            self.redraw(qimg)

    def redraw(self, qimg:QImage):
        pm = QPixmap.fromImage(qimg)
        scaled = pm.scaled(self.view.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.view.setPixmap(scaled)

    def resizeEvent(self, e):
        if self.stack.currentIndex()==0 and self.view.pixmap() and not self.view.pixmap().isNull():
            self.redraw(self.view.pixmap().toImage())
        super().resizeEvent(e)

    def on_mouse_local(self, ev:dict):
        if self.stack.currentIndex()!=0: return
        cursor = QPoint(int(ev.get("x",0)), int(ev.get("y",0)))
        rx, ry = self.view.map_to_remote(cursor)
        t = ev.get("t")
        if t == "move":
            self.cc.send_json({"t":"mouse_move","x":rx,"y":ry})
        elif t == "down":
            self.cc.send_json({"t":"mouse_move","x":rx,"y":ry})
            self.cc.send_json({"t":"mouse_down","btn": ev.get("btn","left")})
        elif t == "up":
            self.cc.send_json({"t":"mouse_up","btn": ev.get("btn","left")})
        elif t == "wheel":
            self.cc.send_json({"t":"mouse_wheel","delta": int(ev.get("delta",0))})

    def keyPressEvent(self, e):
        if self.stack.currentIndex()==0:
            if e.isAutoRepeat(): return
            vk = qt_to_vk(e)
            if vk: self.cc.send_key(vk, True)
        else:
            super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if self.stack.currentIndex()==0:
            if e.isAutoRepeat(): return
            vk = qt_to_vk(e)
            if vk: self.cc.send_key(vk, False)
        else:
            super().keyReleaseEvent(e)

    def on_fullscreen(self):
        if self.isFullScreen(): self.showNormal()
        else: self.showFullScreen()

    def on_reconnect(self):
        self.header.update_ip(f"{self.server_ip}: V{VIDEO_PORT} / C{CONTROL_PORT} / F{FILE_PORT}")
        try:
            self.vc.stop(); self.vc.wait(1000)
        except Exception:
            pass
        self.vc = VideoClient(self.server_ip, VIDEO_PORT); self.vc.sig_status.connect(self.on_status); self.vc.sig_frame.connect(self.on_frame); self.vc.start()
        self.cc = ControlClient(self.server_ip, CONTROL_PORT)
        self.fc = FileClient(self.server_ip, FILE_PORT)
        self.page_transfer.fc = self.fc
        self.page_transfer.refresh_server(None)

    def on_exit(self):
        self.close()

    def closeEvent(self, e):
        try:
            if hasattr(self, "page_transfer") and self.page_transfer and \
               hasattr(self.page_transfer, "has_running_transfer") and \
               self.page_transfer.has_running_transfer():
                self.statusBar().showMessage("파일 전송 마무리 중입니다. 잠시만 기다려주세요...", 3000)
                self.page_transfer.wait_transfer_finish(15000)
                if self.page_transfer.has_running_transfer():
                    QMessageBox.warning(self, "알림", "파일 전송이 진행 중입니다. 전송 완료 후 종료해 주세요.")
                    e.ignore()
                    return
        except Exception:
            pass
        try:
            self.vc.stop(); self.vc.wait(1000)
        except Exception:
            pass
        super().closeEvent(e)

# ===================== 연결 다이얼로그(200×200) =====================
class ConnectDialog(QDialog):
    def __init__(self, default_ip: str | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("ConnectDialog")
        self.setWindowTitle("원격 연결")
        self.setFixedSize(250, 220)

        lbl_title = QLabel("서버 IP"); ed = QLineEdit()
        if default_ip: ed.setText(default_ip)
        ed.setPlaceholderText("예: 192.168.1.100")
        btn = QPushButton("연결")
        self.lbl_err = QLabel(""); self.lbl_err.setObjectName("ConnectError")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14,14,14,14); lay.setSpacing(10)
        lay.addWidget(lbl_title)
        lay.addWidget(ed)
        lay.addStretch(1)
        lay.addWidget(btn)
        lay.addWidget(self.lbl_err)

        self.ed_ip = ed
        btn.clicked.connect(self.try_connect)
        ed.returnPressed.connect(self.try_connect)

    def try_connect(self):
        ip = self.ed_ip.text().strip()
        if not ip:
            self.lbl_err.setText("연결 실패: IP를 입력하세요.")
            return
        ok = self._probe(ip, CONTROL_PORT, timeout=2.0)   # 제어 포트로 빠르게 점검
        if not ok:
            # 영상/파일 포트도 한 번 시도(환경에 따라 열려있는 포트가 다를 수 있음)
            ok = self._probe(ip, VIDEO_PORT, timeout=2.0) or self._probe(ip, FILE_PORT, timeout=2.0)
        if ok:
            self.accept()
        else:
            self.lbl_err.setText("연결 실패: 서버에 연결할 수 없습니다.")

    @staticmethod
    def _probe(ip: str, port: int, timeout: float = 2.0) -> bool:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect((ip, port))
            s.close()
            return True
        except Exception:
            return False

# ===================== 진입점 =====================
def main():
    app = QApplication(sys.argv)

    # 외부 QSS 로드
    css_path = os.path.join(os.path.dirname(__file__), "client_main_css.qss")
    try:
        with open(css_path, "r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())
    except Exception:
        pass  # 스타일 파일이 없어도 실행되도록

    # 연결 다이얼로그 표시
    default_ip = sys.argv[1] if len(sys.argv) > 1 else None
    dlg = ConnectDialog(default_ip=default_ip)
    if dlg.exec() != QDialog.Accepted:
        sys.exit(0)

    server_ip = dlg.ed_ip.text().strip()
    w = ClientWindow(server_ip)
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
