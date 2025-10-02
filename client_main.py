# client_main.py
import sys, time, socket, struct, json, os, ctypes
import numpy as np
import cv2

from PySide6.QtCore import Qt, QThread, Signal, QPoint
from PySide6.QtGui import QImage, QPixmap, QGuiApplication
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget, QLabel, QHBoxLayout, QVBoxLayout, QPushButton, QFrame, QLineEdit, QMessageBox

from common import VIDEO_PORT, CONTROL_PORT, FILE_PORT

# --- COM/가상파일(로컬 붙여넣기용) ---
import struct as pystruct
import pythoncom
import win32clipboard
import win32con
from win32com.server.policy import DesignatedWrapPolicy

# ============ Windows 키 주입(로컬 Ctrl+V 주입용) ============
user32 = ctypes.windll.user32
keybd_event = user32.keybd_event
KEYEVENTF_KEYUP = 0x0002

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

# ---- 영상 수신 ----
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
                    qimg = np_bgr_to_qimage(img)
                    self.sig_frame.emit(qimg, w, h)
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
            self._connected = False
            self.sig_status.emit(0.0, 0, False)

    def stop(self): self._stop = True

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
            body = json.dumps(obj).encode("utf-8")
            head = struct.pack(">I", len(body))
            self.sock.sendall(head+body)
        except Exception:
            try: self.sock.close()
            except: pass
            self.sock = None

    def send_key(self, vk:int, down:bool):
        if vk:
            self.send_json({"t":"key","vk":int(vk),"down":bool(down)})

# ---- 파일 클라이언트(업/다운) ----
class FileClient:
    def __init__(self, host: str, port: int):
        self.host = host; self.port = port

    def _connect(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5.0); s.connect((self.host, self.port)); s.settimeout(None)
        return s

    def upload_mem(self, session_id: str, paths: list[str]) -> bool:
        metas=[]
        for p in paths:
            if os.path.isfile(p):
                metas.append({"name": os.path.basename(p), "size": int(os.path.getsize(p)), "path": p})
        if not metas: return False
        s = self._connect()
        try:
            head = {"cmd":"upload_mem","session":session_id,"files":[{"name":m["name"],"size":m["size"]} for m in metas]}
            raw = json.dumps(head).encode("utf-8")
            s.sendall(struct.pack(">I", len(raw)) + raw)
            for m in metas:
                with open(m["path"], "rb") as f:
                    while True:
                        buf = f.read(1024*256)
                        if not buf: break
                        s.sendall(buf)
            # ack
            _ = recv_exact(s, 4)
            if not _: return False
            jlen = struct.unpack(">I", _)[0]
            ack = recv_exact(s, jlen)
            return bool(ack)
        finally:
            s.close()

    def pull_server_clip_files(self):
        """서버 클립보드(CF_HDROP) 파일을 당겨와 [(name, bytes), ...] 로 반환"""
        s = self._connect()
        files = []
        try:
            head = {"cmd":"download_clip"}
            raw = json.dumps(head).encode("utf-8")
            s.sendall(struct.pack(">I", len(raw)) + raw)

            jlen_b = recv_exact(s, 4)
            if not jlen_b: return []
            jlen = struct.unpack(">I", jlen_b)[0]
            hdr = recv_exact(s, jlen)
            if not hdr: return []
            info = json.loads(hdr.decode("utf-8","ignore"))
            metas = info.get("files", [])
            for m in metas:
                name = m.get("name","file")
                size = int(m.get("size",0))
                remain = size; chunks=[]
                while remain>0:
                    chunk = s.recv(min(1024*256, remain))
                    if not chunk: break
                    chunks.append(chunk); remain -= len(chunk)
                data = b"".join(chunks)
                if len(data)==size:
                    files.append((name, data))
        finally:
            s.close()
        return files

# ---- 로컬 가상 파일 클립보드 (붙여넣기용) ----
CFSTR_FILEDESCRIPTORW = "FileGroupDescriptorW"
CFSTR_FILECONTENTS     = "FileContents"
MAX_PATH = 260
FD_FILESIZE   = 0x00004000
FD_WRITESTIME = 0x00000020

def _build_filedescriptorw(filename:str, size:int):
    name_utf16 = filename.encode("utf-16le")
    name_utf16 = (name_utf16 + b"\x00\x00")[:MAX_PATH*2]
    name_utf16 = name_utf16 + b"\x00\x00"*(MAX_PATH - len(name_utf16)//2)
    hdr  = pystruct.pack("<I", FD_FILESIZE | FD_WRITESTIME)
    hdr += b"\x00"*16; hdr += b"\x00"*8; hdr += b"\x00"*8
    hdr += pystruct.pack("<I", 0)
    hdr += pystruct.pack("<II", 0, 0)
    hdr += pystruct.pack("<II", 0, 0)
    hdr += pystruct.pack("<II", 0, 0)
    hdr += pystruct.pack("<I", 0)
    hdr += pystruct.pack("<I", size)
    hdr += name_utf16
    return hdr

class _MemIStream:
    _com_interfaces_ = [pythoncom.IID_IStream]
    def __init__(self, data: bytes):
        self._data = data; self._pos = 0
    def Read(self, cb):
        if self._pos >= len(self._data): return b""
        chunk = self._data[self._pos:self._pos+cb]
        self._pos += len(chunk); return bytes(chunk)
    def Seek(self, dlibMove, dwOrigin):
        if dwOrigin == 0: self._pos = dlibMove
        elif dwOrigin == 1: self._pos += dlibMove
        elif dwOrigin == 2: self._pos = len(self._data)+dlibMove
        return self._pos
    def Stat(self, flags): return (None,)

class VirtualFileDataObject(DesignatedWrapPolicy):
    _com_interfaces_ = [pythoncom.IID_IDataObject]
    _public_methods_ = ['GetData','GetDataHere','QueryGetData','GetCanonicalFormatEtc',
                        'SetData','EnumFormatEtc','DAdvise','DUnadvise','EnumDAdvise']
    def __init__(self, files: list[tuple[str, bytes]]):
        self._wrap_(self)
        self.files = files
        self.cf_filedesc = win32clipboard.RegisterClipboardFormat(CFSTR_FILEDESCRIPTORW)
        self.cf_filecont = win32clipboard.RegisterClipboardFormat(CFSTR_FILECONTENTS)
    def GetData(self, formatetc):
        cfFormat, tymed, lindex, *_ = formatetc
        if cfFormat == self.cf_filedesc:
            payload = pystruct.pack("<I", len(self.files))
            for name, data in self.files:
                payload += _build_filedescriptorw(name, len(data))
            st = pythoncom.CreateStreamOnHGlobal()
            st.Write(payload); st.Seek(0,0)
            return (pythoncom.TYMED_ISTREAM, st)
        if cfFormat == self.cf_filecont:
            idx = int(lindex) if lindex is not None else 0
            if 0 <= idx < len(self.files):
                return (pythoncom.TYMED_ISTREAM, _MemIStream(self.files[idx][1]))
        raise pythoncom.com_error(hresult=win32con.DV_E_FORMATETC, desc="Unsupported", scode=0, argerr=0, helpfile=None)

def set_local_virtual_files(files: list[tuple[str, bytes]]):
    pythoncom.OleInitialize()
    obj = VirtualFileDataObject(files)
    pythoncom.OleSetClipboard(obj)

def inject_local_ctrl_v():
    keybd_event(0x11,0,0,0)               # CTRL down
    keybd_event(0x56,0,0,0)               # 'V'
    keybd_event(0x56,0,KEYEVENTF_KEYUP,0)
    keybd_event(0x11,0,KEYEVENTF_KEYUP,0)

# ---- 상태바 ----
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

# ---- 원격 뷰 라벨 ----
class ViewerLabel(QLabel):
    sig_mouse = Signal(dict)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.keep_aspect = True
        self.remote_size = (0,0)

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
        rx = max(0, min(rx, rw-1)); ry = max(0, min(ry, rh-1))
        return (rx, ry)

    def mouseMoveEvent(self, e):  self.sig_mouse.emit({"t":"move","x":e.position().x(),"y":e.position().y()})
    def mousePressEvent(self, e):
        btn = "left" if e.button()==Qt.LeftButton else "right" if e.button()==Qt.RightButton else "middle"
        self.sig_mouse.emit({"t":"down","btn":btn,"x":e.position().x(),"y":e.position().y()})
    def mouseReleaseEvent(self, e):
        btn = "left" if e.button()==Qt.LeftButton else "right" if e.button()==Qt.RightButton else "middle"
        self.sig_mouse.emit({"t":"up","btn":btn,"x":e.position().x(),"y":e.position().y()})
    def wheelEvent(self, e):
        delta = e.angleDelta().y()
        self.sig_mouse.emit({"t":"wheel","delta":delta})

# ---- 메인 윈도우 ----
class ClientWindow(QMainWindow):
    def __init__(self, server_ip: str):
        super().__init__()
        self.setWindowTitle("원격 뷰어 클라이언트")
        self.resize(1100, 720)
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
        ctrl.addWidget(self.btn_full); ctrl.addWidget(self.btn_keep)
        ctrl.addStretch(1)
        ctrl.addWidget(QLabel("서버 IP:")); ctrl.addWidget(self.ed_ip); ctrl.addWidget(self.btn_re)

        v = QVBoxLayout(); v.addWidget(self.topbar); v.addLayout(ctrl); v.addWidget(self.view, 1)
        wrap = QWidget(); wrap.setLayout(v); self.setCentralWidget(wrap)

        self.vc = VideoClient(self.server_ip, VIDEO_PORT); self.vc.sig_status.connect(self.on_status); self.vc.sig_frame.connect(self.on_frame); self.vc.start()
        self.cc = ControlClient(self.server_ip, CONTROL_PORT)
        self.fc = FileClient(self.server_ip, FILE_PORT)

    # 상태/프레임
    def on_status(self, fps:float, elapsed:int, connected:bool):
        self.topbar.update_fps(fps); self.topbar.update_time(elapsed if connected else 0)
        if not connected: self.view.setText("연결 끊김")

    def on_frame(self, qimg:QImage, w:int, h:int):
        pm = QPixmap.fromImage(qimg)
        scaled = pm.scaled(self.view.size(), Qt.KeepAspectRatio if self.btn_keep.isChecked() else Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        self.view.setPixmap(scaled)
        self.view.set_remote_size(w,h)

    def resizeEvent(self, e):
        if self.view.pixmap() and not self.view.pixmap().isNull():
            self.on_frame(self.view.pixmap().toImage(), *self.view.remote_size)
        super().resizeEvent(e)

    # 마우스/키 → 서버 전송
    def on_mouse_local(self, ev:dict):
        cursor = QPoint(int(ev.get("x",0)), int(ev.get("y",0)))
        rx, ry = self.view.map_to_remote(cursor)
        t = ev.get("t")
        if t == "move":
            self.cc.send_json({"t":"mouse_move", "x":rx, "y":ry})
        elif t == "down":
            self.cc.send_json({"t":"mouse_move", "x":rx, "y":ry})
            self.cc.send_json({"t":"mouse_down", "btn": ev.get("btn","left")})
        elif t == "up":
            self.cc.send_json({"t":"mouse_up", "btn": ev.get("btn","left")})
        elif t == "wheel":
            self.cc.send_json({"t":"mouse_wheel", "delta": int(ev.get("delta",0))})

    # Qt Key -> VK (간단 맵)
    def _qt_to_vk(self, e):
        k = e.key()
        if k == Qt.Key_Control: return 0x11
        if k == Qt.Key_Shift:   return 0x10
        if k == Qt.Key_Alt:     return 0x12
        if k == Qt.Key_Meta:    return 0x5B
        if k == Qt.Key_Space:   return 0x20
        if k == Qt.Key_Tab:     return 0x09
        if k in (Qt.Key_Return, Qt.Key_Enter): return 0x0D
        if k == Qt.Key_Backspace: return 0x08
        if Qt.Key_F1 <= k <= Qt.Key_F24: return 0x70 + (k - Qt.Key_F1)
        if Qt.Key_0 <= k <= Qt.Key_9:    return ord(str(k - Qt.Key_0))
        if Qt.Key_A <= k <= Qt.Key_Z:    return ord(chr(k))
        if k == Qt.Key_Left:  return 0x25
        if k == Qt.Key_Right: return 0x27
        if k == Qt.Key_Up:    return 0x26
        if k == Qt.Key_Down:  return 0x28
        if k == Qt.Key_Delete:return 0x2E
        if k == Qt.Key_Home:  return 0x24
        if k == Qt.Key_End:   return 0x23
        if k == Qt.Key_PageUp:   return 0x21
        if k == Qt.Key_PageDown: return 0x22
        return 0

    def keyPressEvent(self, e):
        if e.isAutoRepeat(): return

        # Ctrl+V: 양방향 분기
        if (e.modifiers() & Qt.ControlModifier) and e.key() == Qt.Key_V:
            # 1) 로컬 클립보드에 파일이 있으면 -> 서버로 업로드 후 서버 붙여넣기
            cb = QGuiApplication.clipboard()
            md = cb.mimeData()
            urls = md.urls() if md else []
            local_paths = [u.toLocalFile() for u in urls if u.isLocalFile()]
            if local_paths:
                import uuid
                sess = str(uuid.uuid4())[:8]
                if self.fc.upload_mem(sess, local_paths):
                    self.cc.send_json({"t":"set_virtual_clip","session":sess,"and_paste":True})
                    self.statusBar().showMessage("서버로 전송 후 원격 붙여넣기 완료", 3000)
                return  # 이벤트 소비

            # 2) 로컬에 파일이 없으면 -> 서버 클립보드에서 당겨와 로컬 붙여넣기
            files = self.fc.pull_server_clip_files()  # [(name, bytes), ...]
            if files:
                set_local_virtual_files(files)
                inject_local_ctrl_v()
                self.statusBar().showMessage("서버 파일을 로컬에 붙여넣었습니다.", 3000)
            else:
                # 서버에 붙여넣을 것도, 가져올 것도 없음 → 원격으로 그냥 Ctrl+V 전달
                self.cc.send_key(0x11, True)  # CTRL down
                self.cc.send_key(0x56, True); self.cc.send_key(0x56, False)
                self.cc.send_key(0x11, False)
            return

        # 일반 키 전달
        vk = self._qt_to_vk(e)
        if vk:
            self.cc.send_key(vk, True)

    def keyReleaseEvent(self, e):
        if e.isAutoRepeat(): return
        vk = self._qt_to_vk(e)
        if vk:
            self.cc.send_key(vk, False)

    # 기타
    def on_fullscreen(self):
        if self.isFullScreen(): self.showNormal()
        else: self.showFullScreen()

    def on_reconnect(self):
        ip = self.ed_ip.text().strip()
        if not ip:
            QMessageBox.warning(self,"알림","IP를 입력하세요."); return
        self.server_ip = ip
        self.topbar.update_ip(f"{self.server_ip}: V{VIDEO_PORT}/C{CONTROL_PORT}/F{FILE_PORT}")
        try:
            self.vc.stop(); self.vc.wait(1000)
        except Exception:
            pass
        self.vc = VideoClient(self.server_ip, VIDEO_PORT); self.vc.sig_status.connect(self.on_status); self.vc.sig_frame.connect(self.on_frame); self.vc.start()
        self.cc = ControlClient(self.server_ip, CONTROL_PORT)
        self.fc = FileClient(self.server_ip, FILE_PORT)

def main():
    server_ip = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    app = QApplication(sys.argv)
    w = ClientWindow(server_ip); w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
