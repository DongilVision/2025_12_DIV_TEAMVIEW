# client_main.py
import sys, time, socket, struct, json, os
import numpy as np
import cv2

from PySide6.QtCore import Qt, QThread, Signal, QPoint, QStandardPaths
from PySide6.QtGui import QImage, QPixmap, QGuiApplication
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QHBoxLayout, QVBoxLayout,
    QPushButton, QFrame, QLineEdit, QMessageBox, QListWidget, QListWidgetItem,
    QSplitter
)

from common import VIDEO_PORT, CONTROL_PORT, FILE_PORT

# ===== 공통 유틸 =====
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

# ===== Qt Key -> Windows VK 매핑(요지) =====
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
        if k == Qt.Key_Plus: return VK["NP_ADD"]
        if k == Qt.Key_Minus: return VK["NP_SUB"]
        if k == Qt.Key_Slash: return VK["NP_DIV"]
        if k == Qt.Key_Period: return VK["NP_DEC"]
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

# ===== 네트워크 스레드(영상) =====
class VideoClient(QThread):
    sig_status = Signal(float, int, bool)
    sig_frame  = Signal(QImage, int, int)

    def __init__(self, host: str, port: int):
        super().__init__()
        self.host = host; self.port = port
        self._stop = False; self._sock = None
        self._connected = False; self._conn_ts = None
        self._frame_count = 0; self._last_fps_ts = time.time()

    def run(self):
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(5.0); self._sock.connect((self.host, self.port))
            self._sock.settimeout(None)
            self._connected = True; self._conn_ts = time.time()
        except Exception:
            self.sig_status.emit(0.0, 0, False); return

        try:
            while not self._stop:
                hdr = recv_exact(self._sock, 12)
                if not hdr: break
                data_len, w, h = struct.unpack(">III", hdr)
                blob = recv_exact(self._sock, data_len)
                if not blob: break
                arr = np.frombuffer(blob, dtype=np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if img is not None:
                    self.sig_frame.emit(np_bgr_to_qimage(img), w, h)
                    self._frame_count += 1

                now = time.time()
                if now - self._last_fps_ts >= 1.0:
                    fps = float(self._frame_count); self._frame_count = 0; self._last_fps_ts = now
                    elapsed = int(now - (self._conn_ts or now))
                    self.sig_status.emit(fps, elapsed, self._connected)
        finally:
            try:
                if self._sock: self._sock.close()
            except Exception: pass
            self._connected = False; self.sig_status.emit(0.0, 0, False)

    def stop(self): self._stop = True

# ===== 제어/키보드 송신 =====
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

# ===== 파일/디렉토리 클라이언트 =====
class FileClient:
    def __init__(self, host: str, port: int):
        self.host = host; self.port = port

    def _connect(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5.0); s.connect((self.host, self.port)); s.settimeout(None)
        return s

    # --- 서버 디렉토리 목록 ---
    def list_dir_server(self, path:str|None=None):
        s = self._connect()
        try:
            send_json(s, {"cmd":"ls","path": path or ""})
            jlen = struct.unpack(">I", recv_exact(s,4))[0]
            resp = json.loads(recv_exact(s, jlen).decode("utf-8","ignore"))
            return resp
        finally:
            s.close()

    # --- 로컬→서버 업로드 (대상 디렉토리 지정) ---
    def upload_to_dir(self, target_dir:str, local_paths:list[str]):
        metas = []
        for p in local_paths:
            if os.path.isfile(p):
                metas.append({"name": os.path.basename(p), "size": int(os.path.getsize(p)), "path": p})
        if not metas: return {"ok": False, "error":"no valid files"}
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
            jlen = struct.unpack(">I", recv_exact(s,4))[0]
            return json.loads(recv_exact(s, jlen).decode("utf-8","ignore"))
        finally:
            s.close()

    # --- 서버→로컬 다운로드 (경로 배열) ---
    def download_paths(self, server_paths:list[str], local_target_dir:str):
        os.makedirs(local_target_dir, exist_ok=True)
        s = self._connect()
        try:
            send_json(s, {"cmd":"download_paths","paths": server_paths})
            jlen = struct.unpack(">I", recv_exact(s,4))[0]
            head = json.loads(recv_exact(s, jlen).decode("utf-8","ignore"))
            if not head.get("ok"): return head
            files = head.get("files", [])
            saved = []
            for m in files:
                name = os.path.basename(m["name"])
                size = int(m["size"])
                dst = os.path.join(local_target_dir, name)
                with open(dst, "wb") as f:
                    remain = size
                    while remain > 0:
                        chunk = s.recv(min(1024*256, remain))
                        if not chunk: raise ConnectionError("file stream interrupted")
                        f.write(chunk); remain -= len(chunk)
                saved.append(dst)
            return {"ok": True, "saved": saved}
        finally:
            s.close()

# ===== 상단 상태바 =====
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

# ===== 원격 화면 라벨 =====
class ViewerLabel(QLabel):
    sig_mouse = Signal(dict)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.keep_aspect = True; self.remote_size = (0,0)
    def set_keep_aspect(self, on:bool): self.keep_aspect = on
    def set_remote_size(self, w:int, h:int): self.remote_size = (w,h)
    def map_to_remote(self, p: QPoint) -> tuple[int,int]:
        rw, rh = self.remote_size
        if rw<=0 or rh<=0: return (0,0)
        lw, lh = self.width(), self.height()
        if self.keep_aspect:
            r = min(lw / rw, lh / rh)
            vw = int(rw * r); vh = int(rh * r)
            ox = (lw - vw)//2; oy = (lh - vh)//2
            x = (p.x() - ox); y = (p.y() - oy)
            if vw>0 and vh>0:
                rx = int(max(0, min(x, vw)) * rw / vw)
                ry = int(max(0, min(y, vh)) * rh / vh)
            else:
                rx, ry = 0, 0
        else:
            rx = int(p.x() * rw / max(1,lw))
            ry = int(p.y() * rh / max(1,lh))
        return max(0,min(rx,rw-1)), max(0,min(ry,rh-1))
    def mouseMoveEvent(self, e):  self.sig_mouse.emit({"t":"move","x":e.position().x(),"y":e.position().y()})
    def mousePressEvent(self, e):
        btn = "left" if e.button()==Qt.LeftButton else "right" if e.button()==Qt.RightButton else "middle"
        self.sig_mouse.emit({"t":"down","btn":btn,"x":e.position().x(),"y":e.position().y()})
    def mouseReleaseEvent(self, e):
        btn = "left" if e.button()==Qt.LeftButton else "right" if e.button()==Qt.RightButton else "middle"
        self.sig_mouse.emit({"t":"up","btn":btn,"x":e.position().x(),"y":e.position().y()})
    def wheelEvent(self, e): self.sig_mouse.emit({"t":"wheel","delta":e.angleDelta().y()})

# ===== 파일 목록 위젯(공통 베이스) =====
class FileList(QListWidget):
    sig_copy = Signal()
    sig_paste = Signal()
    def __init__(self):
        super().__init__()
        self.setSelectionMode(QListWidget.ExtendedSelection)
        self.setUniformItemSizes(True)
        self.setAlternatingRowColors(True)
        self.setStyleSheet("QListWidget{font-size:12px;}")

    def keyPressEvent(self, e):
        if (e.modifiers() & Qt.ControlModifier) and e.key()==Qt.Key_C:
            self.sig_copy.emit(); return
        if (e.modifiers() & Qt.ControlModifier) and e.key()==Qt.Key_V:
            self.sig_paste.emit(); return
        super().keyPressEvent(e)

# ===== 파일 전달 페이지 =====
class FileTransferPage(QWidget):
    def __init__(self, fc: FileClient, parent=None):
        super().__init__(parent)
        self.fc = fc
        # 내부 전송 클립보드: {"type":"local"|"server", "paths":[...]}
        self.clip = None

        # 좌(서버)
        self.lbl_left = QLabel("서버 경로:")
        self.ed_left = QLineEdit(); self.ed_left.setReadOnly(True)
        self.left_list = FileList()
        self.left_list.sig_copy.connect(self.copy_from_server)
        self.left_list.sig_paste.connect(self.paste_to_server)
        # 우(로컬)
        self.lbl_right = QLabel("클라이언트 경로:")
        self.ed_right = QLineEdit(); self.ed_right.setReadOnly(True)
        self.right_list = FileList()
        self.right_list.sig_copy.connect(self.copy_from_local)
        self.right_list.sig_paste.connect(self.paste_to_local)

        # 레이아웃
        header_l = QHBoxLayout(); header_l.addWidget(self.lbl_left); header_l.addWidget(self.ed_left)
        header_r = QHBoxLayout(); header_r.addWidget(self.lbl_right); header_r.addWidget(self.ed_right)
        left_wrap = QVBoxLayout(); left_wrap.addLayout(header_l); left_wrap.addWidget(self.left_list, 1)
        right_wrap = QVBoxLayout(); right_wrap.addLayout(header_r); right_wrap.addWidget(self.right_list, 1)

        left_w = QWidget(); left_w.setLayout(left_wrap)
        right_w = QWidget(); right_w.setLayout(right_wrap)
        spl = QSplitter(); spl.addWidget(left_w); spl.addWidget(right_w); spl.setSizes([600, 600])

        root = QHBoxLayout(); root.setContentsMargins(8,8,8,8); root.addWidget(spl, 1)
        self.setLayout(root)

        # 초기 경로
        self.server_cwd = None
        self.local_cwd  = os.path.expanduser("~")
        self.refresh_server(self.server_cwd)
        self.refresh_local(self.local_cwd)

        # 더블클릭 탐색
        self.left_list.itemDoubleClicked.connect(self.on_double_left)
        self.right_list.itemDoubleClicked.connect(self.on_double_right)

    # ---- 서버 목록 ----
    def refresh_server(self, path: str|None):
        resp = self.fc.list_dir_server(path)
        if not resp.get("ok"):
            self.left_list.clear(); self.ed_left.setText(resp.get("error","에러"))
            return
        self.server_cwd = resp["path"]
        self.ed_left.setText(self.server_cwd)
        self.left_list.clear()
        # 상위로 이동 항목
        up = os.path.dirname(self.server_cwd)
        if up and up != self.server_cwd:
            it = QListWidgetItem(".."); it.setData(Qt.UserRole, {"name":"..","is_dir":True,"path": up})
            self.left_list.addItem(it)
        for m in sorted(resp["items"], key=lambda x:(not x["is_dir"], x["name"].lower())):
            it = QListWidgetItem(("[D] " if m["is_dir"] else "[F] ")+m["name"])
            it.setData(Qt.UserRole, {"name":m["name"],"is_dir":m["is_dir"],"path": os.path.join(self.server_cwd, m["name"])})
            self.left_list.addItem(it)

    # ---- 로컬 목록 ----
    def refresh_local(self, path: str):
        path = os.path.abspath(path)
        self.local_cwd = path
        self.ed_right.setText(self.local_cwd)
        self.right_list.clear()
        up = os.path.dirname(self.local_cwd)
        if up and up != self.local_cwd:
            it = QListWidgetItem(".."); it.setData(Qt.UserRole, {"name":"..","is_dir":True,"path": up})
            self.right_list.addItem(it)
        try:
            with os.scandir(self.local_cwd) as iters:
                for e in sorted(iters, key=lambda x:(not x.is_dir(), x.name.lower())):
                    meta = {"name":e.name,"is_dir":e.is_dir(),"path": os.path.join(self.local_cwd, e.name)}
                    item = QListWidgetItem(("[D] " if e.is_dir() else "[F] ")+e.name)
                    item.setData(Qt.UserRole, meta)
                    self.right_list.addItem(item)
        except Exception as ex:
            self.right_list.addItem(QListWidgetItem(f"[ERROR] {ex!s}"))

    # ---- 더블클릭 이동 ----
    def on_double_left(self, item: QListWidgetItem):
        meta = item.data(Qt.UserRole)
        if meta and meta.get("is_dir"):
            self.refresh_server(meta["path"])

    def on_double_right(self, item: QListWidgetItem):
        meta = item.data(Qt.UserRole)
        if meta and meta.get("is_dir"):
            self.refresh_local(meta["path"])

    # ---- 복사/붙여넣기 동작 ----
    def copy_from_server(self):
        paths = []
        for it in self.left_list.selectedItems():
            meta = it.data(Qt.UserRole); 
            if meta and not meta.get("is_dir"): paths.append(meta["path"])
        if not paths:
            self.parent().statusBar().showMessage("서버: 파일을 선택하세요(폴더 제외).", 3000); return
        self.clip = {"type":"server", "paths": paths}
        self.parent().statusBar().showMessage(f"서버에서 {len(paths)}개 복사됨.", 3000)

    def paste_to_server(self):
        # 로컬 클립보드(내부) → 서버 현재 폴더로 업로드
        if not self.clip or self.clip.get("type")!="local":
            self.parent().statusBar().showMessage("로컬에서 복사(Ctrl+C)한 뒤 서버 창에 붙여넣기(Ctrl+V) 하세요.", 3000); return
        res = self.fc.upload_to_dir(self.server_cwd, self.clip["paths"])
        if res.get("ok"):
            self.refresh_server(self.server_cwd)
            self.parent().statusBar().showMessage(f"업로드 완료: {len(res.get('saved',[]))}개", 3000)
        else:
            self.parent().statusBar().showMessage("업로드 실패: "+res.get("error",""), 5000)

    def copy_from_local(self):
        paths = []
        for it in self.right_list.selectedItems():
            meta = it.data(Qt.UserRole); 
            if meta and not meta.get("is_dir"): paths.append(meta["path"])
        if not paths:
            self.parent().statusBar().showMessage("클라이언트: 파일을 선택하세요(폴더 제외).", 3000); return
        self.clip = {"type":"local", "paths": paths}
        self.parent().statusBar().showMessage(f"클라이언트에서 {len(paths)}개 복사됨.", 3000)

    def paste_to_local(self):
        # 서버 클립보드(내부) → 로컬 현재 폴더로 다운로드
        if not self.clip or self.clip.get("type")!="server":
            self.parent().statusBar().showMessage("서버에서 복사(Ctrl+C)한 뒤 클라이언트 창에 붙여넣기(Ctrl+V) 하세요.", 3000); return
        res = self.fc.download_paths(self.clip["paths"], self.local_cwd)
        if res.get("ok"):
            self.refresh_local(self.local_cwd)
            self.parent().statusBar().showMessage(f"다운로드 완료: {len(res.get('saved',[]))}개", 3000)
        else:
            self.parent().statusBar().showMessage("다운로드 실패: "+res.get("error",""), 5000)

# ===== 메인 윈도우 =====
class ClientWindow(QMainWindow):
    def __init__(self, server_ip: str):
        super().__init__()
        self.setWindowTitle("원격 뷰어 클라이언트")
        self.resize(1180, 760)
        self.server_ip = server_ip

        # 상단 바 + 컨트롤
        self.topbar = TopStatusBar(); self.topbar.update_ip(f"{self.server_ip}: V{VIDEO_PORT}/C{CONTROL_PORT}/F{FILE_PORT}")
        self.btn_full = QPushButton("전체크기"); self.btn_full.clicked.connect(self.on_fullscreen)
        self.btn_keep = QPushButton("원격해상도유지"); self.btn_keep.setCheckable(True); self.btn_keep.setChecked(True)
        self.btn_transfer = QPushButton("파일 전달"); self.btn_transfer.setCheckable(True); self.btn_transfer.clicked.connect(self.toggle_transfer_page)

        self.ed_ip = QLineEdit(self.server_ip); self.ed_ip.setFixedWidth(160)
        self.btn_re = QPushButton("재연결"); self.btn_re.clicked.connect(self.on_reconnect)

        ctrl = QHBoxLayout(); ctrl.setContentsMargins(8,4,8,4); ctrl.setSpacing(8)
        ctrl.addWidget(self.btn_full); ctrl.addWidget(self.btn_keep); ctrl.addWidget(self.btn_transfer)
        ctrl.addStretch(1)
        ctrl.addWidget(QLabel("서버 IP:")); ctrl.addWidget(self.ed_ip); ctrl.addWidget(self.btn_re)

        # 페이지 1: 원격 뷰어
        self.view = ViewerLabel("원격 화면 수신 대기")
        self.view.setAlignment(Qt.AlignCenter); self.view.setStyleSheet("background:#202020; color:#DDDDDD;")
        self.view.sig_mouse.connect(self.on_mouse_local)
        viewer_layout = QVBoxLayout(); viewer_layout.addWidget(self.view, 1)
        self.page_viewer = QWidget(); self.page_viewer.setLayout(viewer_layout)

        # 페이지 2: 파일 전달
        self.fc = FileClient(self.server_ip, FILE_PORT)
        self.page_transfer = FileTransferPage(self.fc)

        # 스택: 토글 표시
        self.stack = QStackedWidgetSafe()
        self.stack.addWidget(self.page_viewer)   # index 0
        self.stack.addWidget(self.page_transfer) # index 1

        root = QVBoxLayout(); root.addWidget(self.topbar); root.addLayout(ctrl); root.addWidget(self.stack, 1)
        wrap = QWidget(); wrap.setLayout(root); self.setCentralWidget(wrap)

        # 네트워크
        self.vc = VideoClient(self.server_ip, VIDEO_PORT); self.vc.sig_status.connect(self.on_status); self.vc.sig_frame.connect(self.on_frame); self.vc.start()
        self.cc = ControlClient(self.server_ip, CONTROL_PORT)

        self.view.setFocusPolicy(Qt.StrongFocus)
        self.btn_keep.clicked.connect(self.on_keep_toggle)

    # ----- 스택 위젯의 안전한 포커스 전환 -----
class QStackedWidgetSafe(QWidget):
    def __init__(self):
        super().__init__()
        self._lay = QVBoxLayout(self); self._lay.setContentsMargins(0,0,0,0)
        self._stack = []
        self._idx = 0

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

    # 편의
    def widget(self, i:int): return self._stack[i]

# ===== ClientWindow 계속 =====
    def toggle_transfer_page(self, checked: bool):
        self.stack.setCurrentIndex(1 if checked else 0)
        if checked:
            self.statusBar().showMessage("파일 전달 모드: 좌(서버) / 우(클라이언트). Ctrl+C / Ctrl+V 사용", 5000)
        else:
            self.statusBar().clearMessage()

    # 상태/프레임
    def on_status(self, fps:float, elapsed:int, connected:bool):
        self.topbar.update_fps(fps); self.topbar.update_time(elapsed if connected else 0)
        if not connected and self.stack.currentIndex()==0:
            self.view.setText("연결 끊김")

    def on_frame(self, qimg:QImage, w:int, h:int):
        if self.stack.currentIndex()==0:  # 뷰어 페이지일 때만 갱신 표시
            self.view.set_remote_size(w,h)
            self.redraw(qimg)

    def redraw(self, qimg:QImage):
        pm = QPixmap.fromImage(qimg)
        mode_keep = self.btn_keep.isChecked()
        self.view.set_keep_aspect(mode_keep)
        scaled = pm.scaled(self.view.size(), Qt.KeepAspectRatio if mode_keep else Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        self.view.setPixmap(scaled)

    def resizeEvent(self, e):
        if self.stack.currentIndex()==0 and self.view.pixmap() and not self.view.pixmap().isNull():
            self.redraw(self.view.pixmap().toImage())
        super().resizeEvent(e)

    # 마우스/키 → 제어
    def on_mouse_local(self, ev:dict):
        if self.stack.currentIndex()!=0:
            return  # 파일 전달 모드에서는 원격 마우스 주입 비활성화
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
        # 파일 전달 페이지일 때는 본문으로 넘겨서 Ctrl+C/V 등이 리스트에 들어가도록 함
        if self.stack.currentIndex()==0:
            if e.isAutoRepeat(): return
            vk = qt_to_vk(e)
            if vk:
                self.cc.send_key(vk, True)
        else:
            super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if self.stack.currentIndex()==0:
            if e.isAutoRepeat(): return
            vk = qt_to_vk(e)
            if vk:
                self.cc.send_key(vk, False)
        else:
            super().keyReleaseEvent(e)

    # 기타
    def on_keep_toggle(self, checked:bool):
        if self.stack.currentIndex()==0 and self.view.pixmap():
            self.redraw(self.view.pixmap().toImage())

    def on_fullscreen(self):
        if self.isFullScreen(): self.showNormal()
        else: self.showFullScreen()

    def on_reconnect(self):
        ip = self.ed_ip.text().strip()
        if not ip:
            QMessageBox.warning(self,"알림","IP를 입력하세요."); return
        self.server_ip = ip
        self.topbar.update_ip(f"{self.server_ip}: V{VIDEO_PORT}/C{CONTROL_PORT}/F{FILE_PORT}")
        try: self.vc.stop(); self.vc.wait(1000)
        except Exception: pass
        self.vc = VideoClient(self.server_ip, VIDEO_PORT); self.vc.sig_status.connect(self.on_status); self.vc.sig_frame.connect(self.on_frame); self.vc.start()
        self.cc = ControlClient(self.server_ip, CONTROL_PORT)
        self.fc = FileClient(self.server_ip, FILE_PORT)
        # 파일 전달 페이지의 서버 호스트는 self.fc 내부 사용(새 인스턴스 적용)
        self.page_transfer.fc = self.fc
        self.page_transfer.refresh_server(None)

    def closeEvent(self, e):
        try: self.vc.stop(); self.vc.wait(1000)
        except Exception: pass
        super().closeEvent(e)

def main():
    server_ip = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    app = QApplication(sys.argv)
    w = ClientWindow(server_ip); w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
