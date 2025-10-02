# server_main.py
import sys, time, socket, select, threading, struct, json, os, ctypes
import numpy as np
import cv2
from mss import mss

from PySide6.QtCore import Qt, QTimer, QThread, Signal
from PySide6.QtGui import QAction, QIcon, QGuiApplication
from PySide6.QtWidgets import QApplication, QMainWindow, QLabel, QWidget, QVBoxLayout, QSystemTrayIcon, QMenu

# --- COM/가상파일용 ---
import struct as pystruct
import pythoncom
import win32clipboard
import win32con
from win32com.server.policy import DesignatedWrapPolicy

from common import (
    DEFAULT_HOST, VIDEO_PORT, CONTROL_PORT, FILE_PORT,
    FRAME_FPS, JPEG_QUALITY, get_local_ip
)

# ============ Windows 입력 주입 ============
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

# ============ 영상 서버 ============
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

# ============ 가상 파일 COM 구현 ============
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
    hdr += b"\x00"*16  # clsid
    hdr += b"\x00"*8   # sizel
    hdr += b"\x00"*8   # pointl
    hdr += pystruct.pack("<I", 0)        # attrs
    hdr += pystruct.pack("<II", 0, 0)    # ctime
    hdr += pystruct.pack("<II", 0, 0)    # atime
    hdr += pystruct.pack("<II", 0, 0)    # mtime
    hdr += pystruct.pack("<I", 0)        # high
    hdr += pystruct.pack("<I", size)     # low
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
    def __init__(self, files: list[dict]):
        self._wrap_(self)
        self.files = files
        self.cf_filedesc = win32clipboard.RegisterClipboardFormat(CFSTR_FILEDESCRIPTORW)
        self.cf_filecont = win32clipboard.RegisterClipboardFormat(CFSTR_FILECONTENTS)
    def GetData(self, formatetc):
        cfFormat, tymed, lindex, *_ = formatetc
        if cfFormat == self.cf_filedesc:
            payload = pystruct.pack("<I", len(self.files))
            for f in self.files:
                payload += _build_filedescriptorw(f["name"], len(f["data"]))
            st = pythoncom.CreateStreamOnHGlobal()
            st.Write(payload); st.Seek(0,0)
            return (pythoncom.TYMED_ISTREAM, st)
        if cfFormat == self.cf_filecont:
            idx = int(lindex) if lindex is not None else 0
            if 0 <= idx < len(self.files):
                return (pythoncom.TYMED_ISTREAM, _MemIStream(self.files[idx]["data"]))
        raise pythoncom.com_error(hresult=win32con.DV_E_FORMATETC, desc="Unsupported", scode=0, argerr=0, helpfile=None)
    def QueryGetData(self, formatetc): return win32con.S_OK
    def GetDataHere(self, *a, **k): raise pythoncom.com_error(win32con.DV_E_TYMED, None, None, None)
    def GetCanonicalFormatEtc(self, *a, **k): return (None, win32con.DATA_S_SAMEFORMATETC)
    def SetData(self, *a, **k): return win32con.S_OK
    def EnumFormatEtc(self, *a, **k): raise pythoncom.com_error(win32con.E_NOTIMPL, None, None, None)
    def DAdvise(self, *a, **k): return win32con.OLE_E_ADVISENOTSUPPORTED
    def DUnadvise(self, *a, **k): return win32con.OLE_E_ADVISENOTSUPPORTED
    def EnumDAdvise(self, *a, **k): return win32con.OLE_E_ADVISENOTSUPPORTED

def set_virtual_files_to_clipboard(files: list[dict]):
    pythoncom.OleInitialize()
    obj = VirtualFileDataObject(files)
    pythoncom.OleSetClipboard(obj)

# ============ 제어 서버 ============
class ControlServer(QThread):
    sig_ctrl_conn = Signal(bool)
    def __init__(self, host: str, port: int, mem_sessions: dict):
        super().__init__()
        self.host = host; self.port = port
        self._stop = threading.Event()
        self.mem_sessions = mem_sessions

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

        # 마우스
        if t == "mouse_move":
            SetCursorPos(int(m.get("x",0)), int(m.get("y",0))); return
        if t == "mouse_down":
            btn = m.get("btn","left")
            if btn=="left":  mouse_event(MOUSEEVENTF_LEFTDOWN,0,0,0,0)
            elif btn=="right": mouse_event(MOUSEEVENTF_RIGHTDOWN,0,0,0,0)
            elif btn=="middle":mouse_event(MOUSEEVENTF_MIDDLEDOWN,0,0,0,0)
            return
        if t == "mouse_up":
            btn = m.get("btn","left")
            if btn=="left":  mouse_event(MOUSEEVENTF_LEFTUP,0,0,0,0)
            elif btn=="right": mouse_event(MOUSEEVENTF_RIGHTUP,0,0,0,0)
            elif btn=="middle":mouse_event(MOUSEEVENTF_MIDDLEUP,0,0,0,0)
            return
        if t == "mouse_wheel":
            mouse_event(MOUSEEVENTF_WHEEL,0,0,int(m.get("delta",0)),0); return

        # 키보드
        if t == "key":
            vk = int(m.get("vk",0)); down = bool(m.get("down",True))
            if vk:
                keybd_event(vk, 0, 0 if down else KEYEVENTF_KEYUP, 0); return
            name = m.get("key","")
            if not name: return
            if name == " ": name = "SPACE"
            up = name.upper()
            if len(up)==1 and ("A"<=up<="Z" or "0"<=up<="9"):
                keybd_event(ord(up),0,0 if down else KEYEVENTF_KEYUP,0); return
            vk2 = VK_FALLBACK.get(up,0)
            if vk2: keybd_event(vk2,0,0 if down else KEYEVENTF_KEYUP,0)
            return

        # 클라이언트→서버 붙여넣기: 메모리 업로드를 가상파일로 설정
        if t == "set_virtual_clip":
            sess = m.get("session","")
            and_paste = bool(m.get("and_paste", False))
            files = self.mem_sessions.pop(sess, None)
            if files:
                set_virtual_files_to_clipboard(files)
                if and_paste:
                    keybd_event(0x11,0,0,0)               # CTRL down
                    keybd_event(0x56,0,0,0)               # 'V'
                    keybd_event(0x56,0,KEYEVENTF_KEYUP,0)
                    keybd_event(0x11,0,KEYEVENTF_KEYUP,0)
            return

# ============ 파일 서버 ============
class FileServer(QThread):
    """
    업로드(클→서, 메모리): {"cmd":"upload_mem","session":"<id>","files":[{"name":..., "size":...}, ...]} + body
      -> self.mem_sessions[session] = [{"name","data(bytes)"}]
    서버 클립보드 다운로드(서→클): {"cmd":"download_clip"} 요청 시
      - 서버 클립보드가 CF_HDROP(경로)면 각 파일을 읽어 스트리밍 전송
      응답 헤더: {"cmd":"download_clip","files":[{"name":..., "size":...}, ...]}
      이후 파일 바디를 순차 전송
    """
    def __init__(self, host: str, port: int, mem_sessions: dict):
        super().__init__()
        self.host = host; self.port = port
        self._stop = threading.Event()
        self.mem_sessions = mem_sessions

    def run(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port)); srv.listen(2); srv.settimeout(0.5)
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

            if cmd == "upload_mem":
                self._handle_upload_mem(sock, req)
            elif cmd == "download_clip":
                self._handle_download_clip(sock)
        except Exception:
            pass
        finally:
            try: sock.close()
            except: pass

    def _handle_upload_mem(self, sock, req):
        session = req.get("session","")
        files_meta = req.get("files", [])
        buf_list = []
        for meta in files_meta:
            name = os.path.basename(meta.get("name","file"))
            size = int(meta.get("size", 0))
            remain = size; chunks = []
            while remain > 0:
                chunk = sock.recv(min(1024*256, remain))
                if not chunk: raise ConnectionError("file stream interrupted")
                chunks.append(chunk); remain -= len(chunk)
            data = b"".join(chunks)
            buf_list.append({"name": name, "data": data})
        if session:
            self.mem_sessions[session] = buf_list
        ack = json.dumps({"ok": True}).encode("utf-8")
        sock.sendall(struct.pack(">I", len(ack)) + ack)

    def _handle_download_clip(self, sock):
        # 서버 클립보드에서 파일 경로 읽기(CF_HDROP)
        from PySide6.QtGui import QClipboard
        cb = QGuiApplication.clipboard()
        md = cb.mimeData(mode=QClipboard.Clipboard)
        urls = md.urls() if md else []
        paths = [u.toLocalFile() for u in urls if u.isLocalFile()]

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

# ============ 서버 윈도우 ============
class ServerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("원격화면 서버")
        self.setFixedSize(360, 200)
        self.start_ts = time.time()
        self.ip = get_local_ip()

        self.mem_sessions = {}

        self.video = VideoServer(DEFAULT_HOST, VIDEO_PORT)
        self.ctrl  = ControlServer(DEFAULT_HOST, CONTROL_PORT, self.mem_sessions)
        self.files = FileServer(DEFAULT_HOST, FILE_PORT, self.mem_sessions)
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
            parts = self.lbl_conns.text().split("|"); left = parts[0].strip()
            self.lbl_conns.setText(f"{left} | 제어 연결: {'OK' if ctrl_ok else '-'}")
        elif ctrl_ok is None:
            parts = self.lbl_conns.text().split("|"); right = parts[1].strip() if len(parts)>1 else "제어 연결: -"
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
