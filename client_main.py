# client_main.py
import sys, time, socket, struct, json, os, tempfile
import numpy as np
import cv2

from PySide6.QtCore import Qt, QThread, Signal, QPoint
from PySide6.QtGui import QImage, QPixmap, QGuiApplication
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget, QLabel, QHBoxLayout, QVBoxLayout, QPushButton, QFrame, QLineEdit, QMessageBox

from common import VIDEO_PORT, CONTROL_PORT, FILE_PORT

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
    if k == Qt.Key_Left:   return VK["LEFT"]
    if k == Qt.Key_Right:  return VK["RIGHT"]
    if k == Qt.Key_Up:     return VK["UP"]
    if k == Qt.Key_Down:   return VK["DOWN"]
    if k == Qt.Key_Insert: return VK["INSERT"]
    if k == Qt.Key_Delete: return VK["DELETE"]
    if k == Qt.Key_Home:   return VK["HOME"]
    if k == Qt.Key_End:    return VK["END"]
    if k == Qt.Key_PageUp:   return VK["PGUP"]
    if k == Qt.Key_PageDown: return VK["PGDN"]
    if k == Qt.Key_CapsLock:  return VK["CAPSLOCK"]
    if k == Qt.Key_NumLock:   return VK["NUMLOCK"]
    if k == Qt.Key_ScrollLock:return VK["SCROLLLOCK"]
    if k == Qt.Key_Print:     return VK["PRINT"]
    if k == Qt.Key_Pause:     return VK["PAUSE"]
    if k == Qt.Key_Menu:      return VK["APPS"]
    if k == Qt.Key_Hangul:    return VK["HANGUL"]
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
        if k in (Qt.Key_Return, Qt.Key_Enter): return VK["ENTER"]
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

# ---- 영상 수신 ----
class VideoClient(QThread):
    sig_status = Signal(float, int, bool)
    sig_frame  = Signal(QImage, int, int)
    def __init__(self, host: str, port: int):
        super().__init__()
        self.host = host; self.port = port
        self._stop=False; self._sock=None; self._connected=False
        self._conn_ts=None; self._frame_count=0; self._last_ts=time.time()
    def run(self):
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(5.0); self._sock.connect((self.host, self.port))
            self._sock.settimeout(None); self._connected=True; self._conn_ts=time.time()
        except Exception:
            self.sig_status.emit(0.0, 0, False); return
        try:
            while not self._stop:
                hdr = recv_exact(self._sock, 12)
                if not hdr: break
                n,w,h = struct.unpack(">III", hdr)
                blob = recv_exact(self._sock, n)
                if not blob: break
                img = cv2.imdecode(np.frombuffer(blob, np.uint8), cv2.IMREAD_COLOR)
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

# ---- 제어 송신 ----
class ControlClient:
    def __init__(self, host:str, port:int):
        self.host=host; self.port=port; self.sock=None
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
    def send_json(self, obj:dict):
        if not self.sock:
            self.connect()
            if not self.sock: return
        try:
            raw = json.dumps(obj).encode("utf-8")
            self.sock.sendall(struct.pack(">I", len(raw)) + raw)
        except Exception:
            try: self.sock.close()
            except: pass
            self.sock=None
    def send_key(self, vk:int, down:bool):
        if vk: self.send_json({"t":"key","vk":int(vk),"down":bool(down)})

# ---- 파일/활성폴더 클라이언트 ----
class FileClient:
    def __init__(self, host: str, port: int):
        self.host = host; self.port = port
    def _connect(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5.0); s.connect((self.host, self.port)); s.settimeout(None)
        return s

    # 서버 활성 폴더
    def get_server_active_folder(self):
        s = self._connect()
        try:
            req = json.dumps({"cmd":"active_folder"}).encode("utf-8")
            s.sendall(struct.pack(">I", len(req)) + req)
            jlen_b = recv_exact(s, 4); 
            if not jlen_b: return False, ""
            jlen = struct.unpack(">I", jlen_b)[0]
            body = recv_exact(s, jlen)
            if not body: return False, ""
            resp = json.loads(body.decode("utf-8","ignore"))
            return bool(resp.get("ok")), resp.get("path","")
        finally:
            s.close()

    # 서버 클립 메타(파일 존재 체크)
    def get_server_clip_meta(self):
        s = self._connect()
        try:
            req = json.dumps({"cmd":"clip_meta"}).encode("utf-8")
            s.sendall(struct.pack(">I", len(req)) + req)
            jlen_b = recv_exact(s, 4)
            if not jlen_b: return False, []
            jlen = struct.unpack(">I", jlen_b)[0]
            body = recv_exact(s, jlen)
            if not body: return False, []
            resp = json.loads(body.decode("utf-8", "ignore"))
            return bool(resp.get("ok", False)), resp.get("files", [])
        finally:
            s.close()

    # 서버 클립보드 → 로컬 저장
    def download_from_server_clip(self, target_dir: str | None):
        def _writable_dir(d: str) -> bool:
            try:
                os.makedirs(d, exist_ok=True)
                fd, tmp = tempfile.mkstemp(dir=d)
                os.close(fd); os.remove(tmp)
                return True
            except Exception:
                return False

        # 1) 저장 경로 결정(쓰기 가능 검사), 아니면 폴백
        if target_dir and os.path.isdir(target_dir) and _writable_dir(target_dir):
            base_dir = target_dir
        else:
            base_default = os.path.join(os.path.expanduser("~"), "Downloads")
            base_dir = os.path.join(base_default, "RemoteFromServer")
            os.makedirs(base_dir, exist_ok=True)

        # 2) 서버 접속 후 헤더 수신
        s = self._connect()
        try:
            req = json.dumps({"cmd":"download_clip"}).encode("utf-8")
            s.sendall(struct.pack(">I", len(req)) + req)

            jlen_b = recv_exact(s, 4)
            if not jlen_b: return False, "서버 응답 없음", []
            jlen = struct.unpack(">I", jlen_b)[0]
            head = json.loads(recv_exact(s, jlen).decode("utf-8","ignore"))
            files = head.get("files", [])
            if not files:
                return False, "서버 클립보드에 파일이 없습니다.", []

            saved_paths = []
            # 3) 각 파일 본문 수신/저장 (개별 실패 시 폴백 디렉터리로 재시도)
            for m in files:
                name = os.path.basename(m.get("name","file"))
                size = int(m.get("size",0))
                remain = size

                # 기본 저장 경로
                dst_dir = base_dir
                dst = os.path.join(dst_dir, name)

                # 열기 실패 시 폴백 디렉터리 강제
                try:
                    f = open(dst, "wb")
                except Exception:
                    fb_root = os.path.join(os.path.expanduser("~"), "Downloads", "RemoteFromServer")
                    os.makedirs(fb_root, exist_ok=True)
                    dst_dir = fb_root
                    dst = os.path.join(dst_dir, name)
                    f = open(dst, "wb")

                # 스트림은 끝까지 소비(프로토콜 정합성)
                try:
                    with f:
                        while remain > 0:
                            chunk = s.recv(min(1024*256, remain))
                            if not chunk:
                                raise ConnectionError("file stream interrupted")
                            f.write(chunk)
                            remain -= len(chunk)
                    saved_paths.append(dst)
                except Exception:
                    while remain > 0:
                        chunk = s.recv(min(1024*256, remain))
                        if not chunk:
                            break
                        remain -= len(chunk)
                    # 이 파일은 스킵

            return (len(saved_paths) > 0), (base_dir if saved_paths else ""), saved_paths
        finally:
            s.close()

    # 로컬 클립보드 → 서버 저장
    def upload_local_clip_to_server(self, target_dir: str | None):
        cb = QGuiApplication.clipboard(); md = cb.mimeData()
        urls = md.urls() if md else []
        paths = [u.toLocalFile() for u in urls if u.isLocalFile()]
        if not paths: return False, "클립보드에 파일이 없습니다.", []
        metas=[]
        for p in paths:
            if os.path.isfile(p):
                metas.append({"name": os.path.basename(p), "size": int(os.path.getsize(p)), "path": p})
        if not metas: return False, "유효한 파일이 없습니다.", []
        s = self._connect()
        try:
            head = {"cmd":"upload","files":[{"name":m["name"], "size":m["size"]} for m in metas]}
            if target_dir: head["target_dir"] = target_dir
            raw = json.dumps(head).encode("utf-8")
            s.sendall(struct.pack(">I", len(raw)) + raw)
            for m in metas:
                with open(m["path"], "rb") as f:
                    while True:
                        buf = f.read(1024*256)
                        if not buf: break
                        s.sendall(buf)
            jlen_b = recv_exact(s, 4)
            if not jlen_b: return False, "서버 응답 없음", []
            jlen = struct.unpack(">I", jlen_b)[0]
            ack = json.loads(recv_exact(s, jlen).decode("utf-8","ignore"))
            return bool(ack.get("ok")), ack.get("saved_dir",""), ack.get("saved_paths",[])
        finally:
            s.close()

# ---- 로컬(클라이언트) 활성 탐색기 폴더 조회 ----
def get_local_active_explorer_folder() -> str | None:
    try:
        import win32com.client, win32gui
    except Exception:
        return None
    try:
        hwnd = win32gui.GetForegroundWindow()
        shell = win32com.client.Dispatch("Shell.Application")
        for w in shell.Windows():
            try:
                if int(w.HWND) == hwnd and getattr(w, "Document", None):
                    folder = w.Document.Folder
                    if folder and folder.Self and os.path.isdir(folder.Self.Path):
                        return folder.Self.Path
            except Exception:
                continue
    except Exception:
        return None
    return None

# ---- UI 컴포넌트 ----
class TopStatusBar(QFrame):
    def __init__(self):
        super().__init__()
        self.setFrameShape(QFrame.NoFrame); self.setFixedHeight(24)
        self.lbl_time = QLabel("경과 00:00:00"); self.lbl_fps=QLabel("FPS 0"); self.lbl_ip=QLabel("서버: -")
        lay = QHBoxLayout(); lay.setContentsMargins(8,0,8,0); lay.setSpacing(16)
        lay.addWidget(self.lbl_time); lay.addWidget(self.lbl_fps); lay.addWidget(self.lbl_ip); lay.addStretch(1)
        self.setLayout(lay)
    def update_time(self, s:int):
        h=s//3600; m=(s%3600)//60; sec=s%60
        self.lbl_time.setText(f"경과 {h:02d}:{m:02d}:{sec:02d}")
    def update_fps(self, fps:float): self.lbl_fps.setText(f"FPS {int(fps)}")
    def update_ip(self, txt:str): self.lbl_ip.setText(f"서버: {txt}")

class ViewerLabel(QLabel):
    sig_mouse = Signal(dict)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True); self.setFocusPolicy(Qt.StrongFocus)
        self.keep_aspect=True; self.remote_size=(0,0)
    def set_keep_aspect(self, on:bool): self.keep_aspect = on
    def set_remote_size(self, w:int, h:int): self.remote_size=(w,h)
    def map_to_remote(self, p: QPoint) -> tuple[int,int]:
        rw, rh = self.remote_size
        if rw<=0 or rh<=0: return (0,0)
        lw, lh = self.width(), self.height()
        if self.keep_aspect:
            r = min(lw / rw, lh / rh); vw=int(rw*r); vh=int(rh*r)
            ox=(lw-vw)//2; oy=(lh-vh)//2
            x=max(0,min(p.x()-ox, vw)); y=max(0,min(p.y()-oy, vh))
            rx=int(x*rw/max(1,vw)); ry=int(y*rh/max(1,vh))
        else:
            rx=int(p.x()*rw/max(1,lw)); ry=int(p.y()*rh/max(1,lh))
        return (max(0,min(rx,rw-1)), max(0,min(ry,rh-1)))
    def mouseMoveEvent(self, e):  self.sig_mouse.emit({"t":"move","x":e.position().x(),"y":e.position().y()})
    def mousePressEvent(self, e):
        btn="left" if e.button()==Qt.LeftButton else "right" if e.button()==Qt.RightButton else "middle"
        self.sig_mouse.emit({"t":"down","btn":btn,"x":e.position().x(),"y":e.position().y()})
    def mouseReleaseEvent(self, e):
        btn="left" if e.button()==Qt.LeftButton else "right" if e.button()==Qt.RightButton else "middle"
        self.sig_mouse.emit({"t":"up","btn":btn,"x":e.position().x(),"y":e.position().y()})
    def wheelEvent(self, e): self.sig_mouse.emit({"t":"wheel","delta": e.angleDelta().y()})

# ---- 메인 윈도우 ----
class ClientWindow(QMainWindow):
    def __init__(self, server_ip: str):
        super().__init__()
        self.setWindowTitle("원격 뷰어 클라이언트"); self.resize(1100, 720)
        self.server_ip = server_ip

        self.topbar = TopStatusBar(); self.topbar.update_ip(f"{self.server_ip}: V{VIDEO_PORT}/C{CONTROL_PORT}/F{FILE_PORT}")
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

        self.vc = VideoClient(self.server_ip, VIDEO_PORT); self.vc.sig_status.connect(self.on_status); self.vc.sig_frame.connect(self.on_frame); self.vc.start()
        self.cc = ControlClient(self.server_ip, CONTROL_PORT)
        self.fc = FileClient(self.server_ip, FILE_PORT)

        self.view.setFocusPolicy(Qt.StrongFocus)
        self.btn_keep.clicked.connect(self.on_keep_toggle)

    # 상태/프레임
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

    # 마우스/휠
    def on_mouse_local(self, ev:dict):
        cursor = QPoint(int(ev.get("x",0)), int(ev.get("y",0)))
        rx, ry = self.view.map_to_remote(cursor); t = ev.get("t")
        if t == "move":
            self.cc.send_json({"t":"mouse_move","x":rx,"y":ry})
        elif t == "down":
            self.cc.send_json({"t":"mouse_move","x":rx,"y":ry})
            self.cc.send_json({"t":"mouse_down","btn":ev.get("btn","left")})
        elif t == "up":
            self.cc.send_json({"t":"mouse_up","btn":ev.get("btn","left")})
        elif t == "wheel":
            self.cc.send_json({"t":"mouse_wheel","delta":int(ev.get("delta",0))})

    # ---- 키 처리: 양방향 파일 붙여넣기 ----
    def keyPressEvent(self, e):
        if e.isAutoRepeat(): return

        is_ctrl_v  = (e.modifiers() & Qt.ControlModifier) and e.key() == Qt.Key_V
        force_dl   = (e.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier)) == (Qt.ControlModifier | Qt.ShiftModifier) and e.key() == Qt.Key_V  # Ctrl+Shift+V (서버→클라이언트 강제)
        force_ul   = (e.modifiers() & (Qt.ControlModifier | Qt.AltModifier))   == (Qt.ControlModifier | Qt.AltModifier)   and e.key() == Qt.Key_V  # Ctrl+Alt+V (클라이언트→서버 강제)

        if is_ctrl_v:
            if force_dl:
                # 서버 → 클라이언트 강제
                local_dir = get_local_active_explorer_folder()
                ok_down, saved_dir, saved_paths = self.fc.download_from_server_clip(local_dir or None)
                if ok_down and saved_paths:
                    self.statusBar().showMessage(f"서버 → 로컬 저장 완료: {saved_dir} ({len(saved_paths)}개)", 5000)
                    return
            elif force_ul:
                # 클라이언트 → 서버 강제
                ok_path, srv_dir = self.fc.get_server_active_folder()
                ok_up, saved_dir, saved_paths = self.fc.upload_local_clip_to_server(srv_dir if ok_path else None)
                if ok_up and saved_paths:
                    self.cc.send_key(VK["F5"], True); self.cc.send_key(VK["F5"], False)
                    self.statusBar().showMessage(f"서버에 저장됨: {saved_dir}", 4000)
                    return
            else:
                # 1) 서버 클립에 파일이 있으면 서버 → 클라이언트 우선
                has_srv, files_meta = self.fc.get_server_clip_meta()
                if has_srv and files_meta:
                    local_dir = get_local_active_explorer_folder()
                    ok_down, saved_dir, saved_paths = self.fc.download_from_server_clip(local_dir or None)
                    if ok_down and saved_paths:
                        self.statusBar().showMessage(f"서버 → 로컬 저장 완료: {saved_dir} ({len(saved_paths)}개)", 5000)
                        return
                # 2) 서버에 없으면 로컬 클립보드 파일을 서버로 업로드
                if self._local_clip_has_files():
                    ok_path, srv_dir = self.fc.get_server_active_folder()
                    ok_up, saved_dir, saved_paths = self.fc.upload_local_clip_to_server(srv_dir if ok_path else None)
                    if ok_up and saved_paths:
                        self.cc.send_key(VK["F5"], True); self.cc.send_key(VK["F5"], False)
                        self.statusBar().showMessage(f"서버에 저장됨: {saved_dir}", 4000)
                        return

            # 3) 여기까지 못 옮겼다면 일반 Ctrl+V를 서버로 전달
            self.cc.send_key(VK["CTRL"], True); self.cc.send_key(ord('V'), True)
            self.cc.send_key(ord('V'), False);  self.cc.send_key(VK["CTRL"], False)
            return

        # 일반 키 처리
        vk = qt_to_vk(e)
        if vk: self.cc.send_key(vk, True)

    def keyReleaseEvent(self, e):
        if e.isAutoRepeat(): return
        vk = qt_to_vk(e)
        if vk: self.cc.send_key(vk, False)

    def _local_clip_has_files(self) -> bool:
        md = QGuiApplication.clipboard().mimeData()
        return bool(md and any(u.isLocalFile() for u in md.urls()))

    # 기타
    def on_keep_toggle(self, _):
        if self.view.pixmap(): self.redraw(self.view.pixmap().toImage())
    def on_fullscreen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()
    def on_reconnect(self):
        ip = self.ed_ip.text().strip()
        if not ip:
            QMessageBox.warning(self,"알림","IP를 입력하세요."); return
        self.server_ip = ip
        self.topbar.update_ip(f"{self.server_ip}: V{VIDEO_PORT}/C{CONTROL_PORT}/F{FILE_PORT}")
        try:
            self.vc.stop(); self.vc.wait(1000)
        except Exception: pass
        self.vc = VideoClient(self.server_ip, VIDEO_PORT); self.vc.sig_status.connect(self.on_status); self.vc.sig_frame.connect(self.on_frame); self.vc.start()
        self.cc = ControlClient(self.server_ip, CONTROL_PORT)
        self.fc = FileClient(self.server_ip, FILE_PORT)
    def closeEvent(self, e):
        try:
            self.vc.stop(); self.vc.wait(1000)
        except Exception: pass
        super().closeEvent(e)

def main():
    server_ip = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    app = QApplication(sys.argv)
    w = ClientWindow(server_ip); w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
