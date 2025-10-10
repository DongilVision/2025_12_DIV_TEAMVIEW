# client/ui.py
import os
from PySide6.QtCore import Qt, QPoint, Signal
from PySide6.QtGui import QImage, QPixmap, QIcon
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QStyle, QDialog, QLineEdit, QTreeWidget, QTreeWidgetItem,
    QHeaderView, QSplitter, QProgressBar, QMessageBox, QSizePolicy
)
from utils import qt_to_vk, human_size, fmt_mtime
from net import VideoClient, ControlClient, FileClient

# ---------- 포트 상수: 외부(common.py) 우선, 실패 시 기본값 ----------

from common import VIDEO_PORT, CONTROL_PORT, FILE_PORT  # 프로젝트 루트 공유 파일


# ===================== 연결 다이얼로그 =====================
class ConnectDialog(QDialog):
    def __init__(self, default_ip: str | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("ConnectDialog")
        self.setWindowTitle("원격 연결")
        self.setFixedSize(220, 180)

        lbl_title = QLabel("서버 IP")
        ed = QLineEdit()
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

    # CONTROL → VIDEO → FILE 순으로 빠르게 연결성 점검
    def try_connect(self):
        import socket
        ip = self.ed_ip.text().strip()
        if not ip:
            self.lbl_err.setText("연결 실패: IP를 입력하세요.")
            return

        def probe(port: int, timeout=2.0) -> bool:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(timeout); s.connect((ip, port)); s.close(); return True
            except Exception: return False

        ok = probe(CONTROL_PORT) or probe(VIDEO_PORT) or probe(FILE_PORT)
        if ok: self.accept()
        else:  self.lbl_err.setText("연결 실패: 서버에 연결할 수 없습니다.")

# ===================== 공통 뷰 스택(간단) =====================
class QStackedWidgetSafe(QWidget):
    def __init__(self):
        super().__init__()
        self._lay = QVBoxLayout(self); self._lay.setContentsMargins(0,0,0,0)
        self._stack = []; self._idx = 0
    def addWidget(self, w: QWidget):
        if self._stack: w.setVisible(False)
        self._stack.append(w); self._lay.addWidget(w)
    def setCurrentIndex(self, i: int):
        if not (0 <= i < len(self._stack)): return
        self._stack[self._idx].setVisible(False)
        self._idx = i
        self._stack[self._idx].setVisible(True)
    def currentIndex(self): return self._idx

# ===================== 상단 헤더/배지 =====================
class Badge(QLabel):
    def __init__(self, text=""):
        super().__init__(text)
        self.setObjectName("Badge")
        self.setMinimumHeight(28)
        self.setAlignment(Qt.AlignCenter)
        self.setContentsMargins(10,3,10,3)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)

class TopHeader(QFrame):
    def __init__(self, on_fullscreen, on_toggle_transfer, on_reconnect, on_exit):
        super().__init__()
        self.setObjectName("TopHeader")
        self.setFixedHeight(56)

        # 좌: 아이콘+타이틀
        icon_lbl = QLabel()
        icon_lbl.setPixmap(self.style().standardIcon(QStyle.SP_ComputerIcon).pixmap(20,20))
        title = QLabel("원격 제어"); title.setObjectName("Title")
        left = QHBoxLayout(); left.setContentsMargins(12,0,0,0); left.setSpacing(8)
        left.addWidget(icon_lbl); left.addWidget(title)
        left_wrap = QWidget(); left_wrap.setLayout(left)

        # 중: 배지
        self.badge_time = Badge("⏱ 00:00:00")
        self.badge_bw   = Badge("⇅ 0 Mbps")
        self.badge_ip   = Badge("서버 IP: -")
        center = QHBoxLayout(); center.setContentsMargins(0,0,0,0); center.setSpacing(8)
        center.addStretch(1); center.addWidget(self.badge_time); center.addWidget(self.badge_bw); center.addWidget(self.badge_ip); center.addStretch(1)
        center_wrap = QWidget(); center_wrap.setLayout(center)

        # 우: 버튼
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

    def update_time(self, sec:int):
        h=sec//3600; m=(sec%3600)//60; s=sec%60
        self.badge_time.setText(f"⏱ {h:02d}:{m:02d}:{s:02d}")
    def update_bw(self, mbps:float):
        self.badge_bw.setText(f"⇅ {mbps:.0f} Mbps")
    def update_ip(self, s:str):
        self.badge_ip.setText(f"서버 IP: {s}")

# ===================== 원격 화면라벨 =====================
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
    def set_remote_size(self, w:int, h:int): self.remote_size=(w,h)
    def map_to_remote(self, p: QPoint) -> tuple[int,int]:
        rw, rh = self.remote_size
        if rw<=0 or rh<=0: return (0,0)
        lw, lh = self.width(), self.height()
        r = min(lw/rw, lh/rh)
        vw, vh = int(rw*r), int(rh*r)
        ox, oy = (lw-vw)//2, (lh-vh)//2
        x, y = p.x()-ox, p.y()-oy
        if vw>0 and vh>0:
            rx = int(max(0, min(x, vw)) * rw / vw)
            ry = int(max(0, min(y, vh)) * rh / vh)
        else: rx, ry = 0, 0
        return max(0,min(rx,rw-1)), max(0,min(ry,rh-1))
    def mouseMoveEvent(self, e):  self.sig_mouse.emit({"t":"move","x":e.position().x(),"y":e.position().y()})
    def mousePressEvent(self, e):
        btn = "left" if e.button()==Qt.LeftButton else "right" if e.button()==Qt.RightButton else "middle"
        self.sig_mouse.emit({"t":"down","btn":btn,"x":e.position().x(),"y":e.position().y()})
    def mouseReleaseEvent(self, e):
        btn = "left" if e.button()==Qt.LeftButton else "right" if e.button()==Qt.RightButton else "middle"
        self.sig_mouse.emit({"t":"up","btn":btn,"x":e.position().x(),"y":e.position().y()})
    def wheelEvent(self, e): self.sig_mouse.emit({"t":"wheel","delta": int(e.angleDelta().y())})

# ===================== 파일 테이블/전달 페이지 =====================
class FileTable(QTreeWidget):
    sig_copy = Signal(); sig_paste = Signal()
    def __init__(self):
        super().__init__()
        self.setColumnCount(4)
        self.setHeaderLabels(["이름","수정 날짜","유형","크기"])
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
        if (e.modifiers() & Qt.ControlModifier) and e.key()==Qt.Key_C: self.sig_copy.emit(); return
        if (e.modifiers() & Qt.ControlModifier) and e.key()==Qt.Key_V: self.sig_paste.emit(); return
        super().keyPressEvent(e)
    def add_entry(self, name:str, is_dir:bool, full_path:str, size:int|None, mtime:float|None):
        ext = os.path.splitext(name)[1][1:].upper()
        ftype = "폴더" if is_dir else (f"{ext} 파일" if ext else "파일")
        it = QTreeWidgetItem([name, fmt_mtime(mtime), ftype, "" if is_dir else human_size(size or 0)])
        it.setData(0, Qt.UserRole, {"name":name,"is_dir":is_dir,"path":full_path})
        it.setIcon(0, self.dir_icon if is_dir else self.file_icon)
        it.setTextAlignment(3, Qt.AlignRight | Qt.AlignVCenter)
        self.addTopLevelItem(it)
        return it

from PySide6.QtCore import QThread

class TransferThread(QThread):
    prog = Signal(int,int); done = Signal(bool,str)
    def __init__(self, op_callable): super().__init__(); self._op=op_callable
    def run(self):
        def cb(done,total): self.prog.emit(int(done), int(total))
        try: ok,msg = self._op(cb)
        except Exception as ex: ok,msg = False,str(ex)
        self.done.emit(bool(ok), str(msg))

class FileTransferPage(QWidget):
    def __init__(self, fc: FileClient, parent=None):
        super().__init__(parent)
        self.fc = fc
        self.clip = None
        self._th = None

        # 좌: 서버
        self.ed_left = QLineEdit(); self.ed_left.setReadOnly(True)
        self.btn_left_send = QPushButton("전달"); self.btn_left_zip = QPushButton("ZIP으로 전달")
        for b in (self.btn_left_send, self.btn_left_zip): b.setEnabled(False)

        # ★ 여기 추가
        self.btn_left_send.clicked.connect(self.on_left_send)
        self.btn_left_zip.clicked.connect(self.on_left_zip)

        self.left_table = FileTable()
        self.left_table.sig_copy.connect(self.copy_from_server)
        self.left_table.sig_paste.connect(self.paste_to_server)
        self.left_table.itemSelectionChanged.connect(self.update_buttons)

        # 우: 로컬
        self.ed_right = QLineEdit(); self.ed_right.setReadOnly(True)
        self.btn_right_send = QPushButton("전달"); self.btn_right_zip = QPushButton("ZIP으로 전달")
        for b in (self.btn_right_send, self.btn_right_zip): b.setEnabled(False)

        # ★ 여기 추가
        self.btn_right_send.clicked.connect(self.on_right_send)
        self.btn_right_zip.clicked.connect(self.on_right_zip)

        self.right_table = FileTable()
        self.right_table.sig_copy.connect(self.copy_from_local)
        self.right_table.sig_paste.connect(self.paste_to_local)
        self.right_table.itemSelectionChanged.connect(self.update_buttons)

        # 상단바
        header_l = QHBoxLayout(); header_l.addWidget(QLabel("서버 경로:")); header_l.addWidget(self.ed_left,1); header_l.addWidget(self.btn_left_send); header_l.addWidget(self.btn_left_zip)
        header_r = QHBoxLayout(); header_r.addWidget(QLabel("클라이언트 경로:")); header_r.addWidget(self.ed_right,1); header_r.addWidget(self.btn_right_send); header_r.addWidget(self.btn_right_zip)

        # 본문
        left_wrap = QVBoxLayout(); left_wrap.addLayout(header_l); left_wrap.addWidget(self.left_table,1)
        right_wrap = QVBoxLayout(); right_wrap.addLayout(header_r); right_wrap.addWidget(self.right_table,1)
        w_left = QWidget(); w_left.setLayout(left_wrap)
        w_right = QWidget(); w_right.setLayout(right_wrap)
        spl = QSplitter(); spl.addWidget(w_left); spl.addWidget(w_right); spl.setSizes([600,600])

        # 진행률
        self.prog = QProgressBar(); self.prog.setRange(0,100); self.prog.setValue(0)
        self.lbl_prog = QLabel("")
        prog_lay = QHBoxLayout(); prog_lay.addWidget(QLabel("전송 진행:")); prog_lay.addWidget(self.prog,1); prog_lay.addWidget(self.lbl_prog)

        root = QVBoxLayout(self); root.setContentsMargins(8,8,8,8)
        root.addWidget(spl,1); root.addLayout(prog_lay)

        # 초기 경로
        self.server_cwd=None
        self.local_cwd = os.path.expanduser("~")
        self.refresh_server(self.server_cwd)
        self.refresh_local(self.local_cwd)

        # 더블클릭 이동
        self.left_table.itemDoubleClicked.connect(self.on_double_left)
        self.right_table.itemDoubleClicked.connect(self.on_double_right)

    # 유틸
    def has_running_transfer(self): return (self._th is not None) and self._th.isRunning()
    def wait_transfer_finish(self, timeout_ms=None):
        if self._th is not None: self._th.wait(timeout_ms or -1)

    # 갱신
    def refresh_server(self, path):
        resp = self.fc.list_dir_server(path)
        self.left_table.clear()
        if not resp.get("ok"):
            self.ed_left.setText(resp.get("error","에러")); return
        self.server_cwd = resp["path"]; self.ed_left.setText(self.server_cwd)
        up = os.path.dirname(self.server_cwd)
        if up and up != self.server_cwd:
            self.left_table.add_entry("..", True, up, None, None)
        for m in sorted(resp.get("items",[]), key=lambda x:(not x.get("is_dir",False), x.get("name","").lower())):
            full = os.path.join(self.server_cwd, m["name"])
            self.left_table.add_entry(m["name"], bool(m["is_dir"]), full, int(m.get("size",0)), float(m.get("mtime",0)))
        self.left_table.sortItems(0, Qt.AscendingOrder)

    def refresh_local(self, path):
        path = os.path.abspath(path)
        self.local_cwd = path; self.ed_right.setText(self.local_cwd)
        self.right_table.clear()
        up = os.path.dirname(self.local_cwd)
        if up and up != self.local_cwd:
            self.right_table.add_entry("..", True, up, None, None)
        try:
            with os.scandir(self.local_cwd) as iters:
                entries=[]
                for e in iters:
                    try:
                        st=e.stat()
                        entries.append({"name":e.name,"is_dir":e.is_dir(),"size":int(st.st_size),"mtime":float(st.st_mtime)})
                    except Exception: pass
            for m in sorted(entries, key=lambda x:(not x["is_dir"], x["name"].lower())):
                full = os.path.join(self.local_cwd, m["name"])
                self.right_table.add_entry(m["name"], bool(m["is_dir"]), full, int(m.get("size",0)), float(m.get("mtime",0)))
            self.right_table.sortItems(0, Qt.AscendingOrder)
        except Exception as ex:
            self.right_table.addTopLevelItem(QTreeWidgetItem([f"[ERROR] {ex!s}","","",""]))

    # 이동
    def on_double_left(self, it):
        meta = it.data(0, Qt.UserRole)
        if meta and meta.get("is_dir"): self.refresh_server(meta["path"])
    def on_double_right(self, it):
        meta = it.data(0, Qt.UserRole)
        if meta and meta.get("is_dir"): self.refresh_local(meta["path"])

    # 버튼 활성화
    def update_buttons(self):
        def has_valid(items):
            for it in items:
                m = it.data(0, Qt.UserRole)
                if m and m.get("name")!="..": return True
            return False
        self.btn_left_send.setEnabled(has_valid(self.left_table.selectedItems()))
        self.btn_left_zip.setEnabled(has_valid(self.left_table.selectedItems()))
        self.btn_right_send.setEnabled(has_valid(self.right_table.selectedItems()))
        self.btn_right_zip.setEnabled(has_valid(self.right_table.selectedItems()))

    # 복사/붙여넣기(파일만)
    def copy_from_server(self):
        paths=[]
        for it in self.left_table.selectedItems():
            m=it.data(0,Qt.UserRole)
            if m and m.get("name")!=".." and not m.get("is_dir"): paths.append(m["path"])
        if not paths: self.window().statusBar().showMessage("서버: 파일을 선택하세요(폴더 제외).",3000); return
        self.clip={"type":"server","paths":paths}; self.window().statusBar().showMessage(f"서버에서 {len(paths)}개 복사됨.",3000)
    def paste_to_server(self):
        if not self.clip or self.clip.get("type")!="local":
            self.window().statusBar().showMessage("로컬에서 Ctrl+C 후 서버 창에 Ctrl+V.",3000); return
        self.run_transfer(lambda cb: self.fc.upload_to_dir(self.server_cwd, self.clip["paths"], progress=cb),
                          after=lambda ok: self.refresh_server(self.server_cwd))

    def copy_from_local(self):
        paths=[]
        for it in self.right_table.selectedItems():
            m=it.data(0,Qt.UserRole)
            if m and m.get("name")!=".." and not m.get("is_dir"): paths.append(m["path"])
        if not paths: self.window().statusBar().showMessage("클라이언트: 파일을 선택하세요(폴더 제외).",3000); return
        self.clip={"type":"local","paths":paths}; self.window().statusBar().showMessage(f"클라이언트에서 {len(paths)}개 복사됨.",3000)
    def paste_to_local(self):
        if not self.clip or self.clip.get("type")!="server":
            self.window().statusBar().showMessage("서버에서 Ctrl+C 후 클라이언트 창에 Ctrl+V.",3000); return
        self.run_transfer(lambda cb: self.fc.download_paths(self.clip["paths"], self.local_cwd, progress=cb),
                          after=lambda ok: self.refresh_local(self.local_cwd))

    # 전달 버튼(폴더 지원 포함)
    def _sel(self, table):  # 선택 경로
        return [it.data(0,Qt.UserRole)["path"] for it in table.selectedItems()
                if it.data(0,Qt.UserRole) and it.data(0,Qt.UserRole)["name"]!=".."]
    def on_left_send(self):
        sel=self._sel(self.left_table); 
        if not sel: return
        self.run_transfer(lambda cb: self.fc.download_tree_paths(sel, self.local_cwd, progress=cb),
                          after=lambda ok: self.refresh_local(self.local_cwd))
    def on_left_zip(self):
        sel=self._sel(self.left_table);
        if not sel: return
        self.run_transfer(lambda cb: self.fc.download_paths_as_zip(sel, self.local_cwd, None, progress=cb),
                          after=lambda ok: self.refresh_local(self.local_cwd))
    def on_right_send(self):
        sel=self._sel(self.right_table);
        if not sel: return
        self.run_transfer(lambda cb: self.fc.upload_tree_to(self.server_cwd, sel, progress=cb),
                          after=lambda ok: self.refresh_server(self.server_cwd))
    def on_right_zip(self):
        sel=self._sel(self.right_table);
        if not sel: return
        self.run_transfer(lambda cb: self.fc.upload_zip_of_local(self.server_cwd, sel, None, progress=cb),
                          after=lambda ok: self.refresh_server(self.server_cwd))

    # 전송 실행/진행률
    def run_transfer(self, op, after=None):
        if self.has_running_transfer():
            self.window().statusBar().showMessage("이미 전송 작업이 실행 중입니다.",3000); return
        self._enable_controls(False); self.prog.setValue(0); self.lbl_prog.setText("남은 100%")
        th = TransferThread(op); th.setParent(self); self._th = th
        th.prog.connect(self._on_progress)
        def _done(ok,msg):
            try:
                if ok: self.lbl_prog.setText("전송 완료"); self.window().statusBar().showMessage("전송 완료",3000)
                else:  self.lbl_prog.setText("전송 실패"); self.window().statusBar().showMessage("전송 실패: "+msg,5000)
                if after: after(ok)
            finally:
                self._enable_controls(True); th.deleteLater(); self._th=None
        th.done.connect(_done); th.start()
    def _enable_controls(self, en:bool):
        for w in [self.left_table, self.right_table, self.btn_left_send, self.btn_left_zip, self.btn_right_send, self.btn_right_zip]:
            w.setEnabled(en)
    def _on_progress(self, done:int, total:int):
        pct = int(done*100/total) if total>0 else 0
        self.prog.setValue(pct); self.lbl_prog.setText(f"남은 {max(0,100-pct)}%")

# ===================== 메인 윈도우 =====================
class ClientWindow(QMainWindow):
    def __init__(self, server_ip:str):
        super().__init__()
        self.setWindowTitle("원격 뷰어 클라이언트")
        self.resize(1180, 760)
        self.server_ip = server_ip

        # 헤더
        self.header = TopHeader(self.on_fullscreen, self.toggle_transfer_page, self.on_reconnect, self.on_exit)
        self.header.update_ip(f"{self.server_ip}: V{VIDEO_PORT} / C{CONTROL_PORT} / F{FILE_PORT}")

        # 페이지: 뷰어
        self.view = ViewerLabel()
        vlay = QVBoxLayout(); vlay.setContentsMargins(12,12,12,12); vlay.addWidget(self.view,1)
        self.page_viewer = QWidget(); self.page_viewer.setLayout(vlay)
        self.view.sig_mouse.connect(self.on_mouse_local)

        # 페이지: 파일 전달
        self.fc = FileClient(self.server_ip, FILE_PORT)
        self.page_transfer = FileTransferPage(self.fc)

        # 스택
        self.stack = QStackedWidgetSafe()
        self.stack.addWidget(self.page_viewer)   # 0
        self.stack.addWidget(self.page_transfer) # 1

        # 루트 레이아웃
        root = QVBoxLayout(); root.setContentsMargins(0,0,0,0)
        root.addWidget(self.header); root.addWidget(self.stack,1)
        wrap = QWidget(); wrap.setLayout(root); self.setCentralWidget(wrap)

        # 네트워크
        self.vc = VideoClient(self.server_ip, VIDEO_PORT)
        self.vc.sig_status.connect(self.on_status)
        self.vc.sig_frame.connect(self.on_frame)
        self.vc.start()

        self.cc = ControlClient(self.server_ip, CONTROL_PORT)

    # 헤더 토글
    def toggle_transfer_page(self):
        chk = self.header.btn_transfer.isChecked()
        self.stack.setCurrentIndex(1 if chk else 0)
        if chk: self.statusBar().showMessage("파일 전달 모드입니다. Ctrl+C/Ctrl+V 사용 가능.",5000)
        else:   self.statusBar().clearMessage()

    # 상태/프레임
    def on_status(self, fps:float, elapsed:int, connected:bool, mbps:float):
        self.header.update_time(elapsed if connected else 0)
        self.header.update_bw(mbps)
        if not connected and self.stack.currentIndex()==0:
            self.view.setText("연결 끊김")

    def on_frame(self, qimg:QImage, w:int, h:int):
        if self.stack.currentIndex()==0:
            self.view.set_remote_size(w,h)
            pm = QPixmap.fromImage(qimg)
            scaled = pm.scaled(self.view.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.view.setPixmap(scaled)

    def resizeEvent(self, e):
        if self.stack.currentIndex()==0 and self.view.pixmap() and not self.view.pixmap().isNull():
            pm = self.view.pixmap(); 
            self.view.setPixmap(pm.scaled(self.view.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        super().resizeEvent(e)

    # 입력 → 서버 제어
    def on_mouse_local(self, ev:dict):
        if self.stack.currentIndex()!=0: return
        cursor = QPoint(int(ev.get("x",0)), int(ev.get("y",0)))
        rx, ry = self.view.map_to_remote(cursor)
        t = ev.get("t")
        if t=="move":
            self.cc.send_json({"t":"mouse_move","x":rx,"y":ry})
        elif t=="down":
            self.cc.send_json({"t":"mouse_move","x":rx,"y":ry})
            self.cc.send_json({"t":"mouse_down","btn": ev.get("btn","left")})
        elif t=="up":
            self.cc.send_json({"t":"mouse_up","btn": ev.get("btn","left")})
        elif t=="wheel":
            self.cc.send_json({"t":"mouse_wheel","delta": int(ev.get("delta",0))})

    def keyPressEvent(self, e):
        if self.stack.currentIndex()==0 and not e.isAutoRepeat():
            vk = qt_to_vk(e)
            if vk: self.cc.send_json({"t":"key","vk":int(vk),"down":True})
        else:
            super().keyPressEvent(e)
    def keyReleaseEvent(self, e):
        if self.stack.currentIndex()==0 and not e.isAutoRepeat():
            vk = qt_to_vk(e)
            if vk: self.cc.send_json({"t":"key","vk":int(vk),"down":False})
        else:
            super().keyReleaseEvent(e)

    # 버튼 핸들러
    def on_fullscreen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def on_reconnect(self):
        self.header.update_ip(f"{self.server_ip}: V{VIDEO_PORT} / C{CONTROL_PORT} / F{FILE_PORT}")
        try:
            self.vc.stop(); self.vc.wait(1000)
        except Exception: pass
        self.vc = VideoClient(self.server_ip, VIDEO_PORT)
        self.vc.sig_status.connect(self.on_status)
        self.vc.sig_frame.connect(self.on_frame)
        self.vc.start()

        self.cc = ControlClient(self.server_ip, CONTROL_PORT)
        self.fc = FileClient(self.server_ip, FILE_PORT)
        self.page_transfer.fc = self.fc
        self.page_transfer.refresh_server(None)

    def on_exit(self): self.close()

    def closeEvent(self, e):
        # 파일 전송 중이면 종료 보류
        try:
            if self.page_transfer.has_running_transfer():
                self.statusBar().showMessage("파일 전송 마무리 중…", 3000)
                self.page_transfer.wait_transfer_finish(15000)
                if self.page_transfer.has_running_transfer():
                    QMessageBox.warning(self,"알림","파일 전송이 진행 중입니다. 완료 후 종료해 주세요.")
                    e.ignore(); return
        except Exception: pass
        try:
            self.vc.stop(); self.vc.wait(1000)
        except Exception: pass
        super().closeEvent(e)
