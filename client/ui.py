# client/ui.py
import os
from PySide6.QtCore import Qt, QPoint, Signal, QEvent, QTimer, QSize
from PySide6.QtGui import QImage, QPixmap, QIcon, QAction, QCursor, QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QStyle, QDialog, QLineEdit, QTreeWidget, QTreeWidgetItem,
    QHeaderView, QSplitter, QProgressBar, QMessageBox, QSizePolicy,
    QListWidget, QListWidgetItem, QCheckBox, QDialogButtonBox, QAbstractItemView, QMenu, QApplication, QGraphicsDropShadowEffect
)
from utils import qt_to_vk, human_size, fmt_mtime
from net import VideoClient, ControlClient, FileClient

# ---------- 포트 상수: 외부(common.py) 우선, 실패 시 기본값 ----------

from common import VIDEO_PORT, CONTROL_PORT, FILE_PORT  # 프로젝트 루트 공유 파일

class IpEditDialog(QDialog):
    def __init__(self, parent=None, *, title="IP 추가", ok_text="추가", alias="", ip=""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setFixedSize(300, 200)

        lbl_alias = QLabel("별칭")
        self.ed_alias = QLineEdit(); self.ed_alias.setPlaceholderText("예: test pc"); self.ed_alias.setText(alias)

        lbl_ip = QLabel("IP 주소")
        self.ed_ip = QLineEdit(); self.ed_ip.setPlaceholderText("예: 192.168.2.111"); self.ed_ip.setText(ip)

        btns = QDialogButtonBox()
        btn_ok = btns.addButton(ok_text, QDialogButtonBox.AcceptRole)
        btn_cancel = btns.addButton("취소", QDialogButtonBox.RejectRole)

        lay = QVBoxLayout(self); lay.setContentsMargins(14,14,14,14); lay.setSpacing(8)
        lay.addWidget(lbl_alias); lay.addWidget(self.ed_alias)
        lay.addWidget(lbl_ip); lay.addWidget(self.ed_ip)
        lay.addWidget(btns)

        btn_ok.clicked.connect(self._on_accept)
        btn_cancel.clicked.connect(self.reject)

    def _on_accept(self):
        alias = self.ed_alias.text().strip()
        ip = self.ed_ip.text().strip()
        if not alias or not ip:
            QMessageBox.warning(self, "확인", "별칭과 IP를 모두 입력하세요.")
            return
        # IPv4 형식 간단 검증
        import socket
        try:
            socket.inet_aton(ip)
        except OSError:
            QMessageBox.warning(self, "확인", "유효한 IPv4 주소가 아닙니다.")
            return
        self.alias = alias
        self.ip = ip
        self.accept()

class AddIpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("IP 추가")
        self.setModal(True)
        self.setFixedSize(300, 200)

        lbl_alias = QLabel("별칭")
        self.ed_alias = QLineEdit(); self.ed_alias.setPlaceholderText("예: test pc")
        lbl_ip = QLabel("IP 주소")
        self.ed_ip = QLineEdit(); self.ed_ip.setPlaceholderText("예: 192.168.2.111")

        btns = QDialogButtonBox()
        btn_add = btns.addButton("추가", QDialogButtonBox.AcceptRole)
        btn_cancel = btns.addButton("취소", QDialogButtonBox.RejectRole)

        lay = QVBoxLayout(self); lay.setContentsMargins(14,14,14,14); lay.setSpacing(8)
        lay.addWidget(lbl_alias); lay.addWidget(self.ed_alias)
        lay.addWidget(lbl_ip); lay.addWidget(self.ed_ip)
        lay.addWidget(btns)

        btn_add.clicked.connect(self._on_accept)
        btn_cancel.clicked.connect(self.reject)

    def _on_accept(self):
        alias = self.ed_alias.text().strip()
        ip = self.ed_ip.text().strip()
        if not alias or not ip:
            QMessageBox.warning(self, "확인", "별칭과 IP를 모두 입력하세요.")
            return
        # 간단한 IP 형식 검증
        import socket
        try:
            socket.inet_aton(ip)
        except OSError:
            QMessageBox.warning(self, "확인", "유효한 IPv4 주소가 아닙니다.")
            return
        self.alias = alias
        self.ip = ip
        self.accept()

# ===================== 연결 다이얼로그 =====================
class ConnectDialog(QDialog):
    def __init__(self, default_ip: str | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("ConnectDialog")
        self.setWindowTitle("원격 연결")

        # 리스트 UI가 들어가므로 조금 키웁니다.
        self.setFixedSize(500, 400)

        # --- 위젯들 ---
        # 토글: 직접 입력
        self.cb_manual = QCheckBox("직접 입력")
        self.cb_manual.setChecked(True)  # 기본은 직접 입력

        # 직접 입력란
        self.ed_ip = QLineEdit()
        if default_ip:
            self.ed_ip.setText(default_ip)
        self.ed_ip.setPlaceholderText("예: 192.168.2.130")

        # 리스트/추가 버튼
        self.list_ips = QListWidget()
        self.list_ips.setSelectionMode(QAbstractItemView.SingleSelection)

        # ★ 컨텍스트 메뉴 활성화
        self.list_ips.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_ips.customContextMenuRequested.connect(self._on_list_menu)

        self.btn_add_ip = QPushButton("IP 추가")

        # 하단 버튼/에러
        self.btn_connect = QPushButton("연결")
        self.lbl_err = QLabel("")
        self.lbl_err.setObjectName("ConnectError")

        # --- 레이아웃 ---
        lay = QVBoxLayout(self); lay.setContentsMargins(14,14,14,14); lay.setSpacing(8)
        lay.addWidget(self.cb_manual)
        lay.addWidget(QLabel("서버 IP"))
        lay.addWidget(self.ed_ip)

        lay.addWidget(QLabel("IP 리스트"))
        row = QHBoxLayout(); row.addWidget(self.list_ips, 1); row.addWidget(self.btn_add_ip, 0)
        lay.addLayout(row)

        lay.addStretch(1)
        lay.addWidget(self.btn_connect)
        lay.addWidget(self.lbl_err)

        # --- 시그널 ---
        self.cb_manual.toggled.connect(self._update_mode)
        self.btn_add_ip.clicked.connect(self._on_add_ip)
        self.btn_connect.clicked.connect(self.try_connect)
        self.ed_ip.returnPressed.connect(self.try_connect)
        self.list_ips.itemDoubleClicked.connect(lambda *_: self.try_connect())

        # --- 데이터 로드 & 초기 모드 ---
        self._ip_list_path = os.path.join(os.path.dirname(__file__), "ip_list.json")
        self._load_ip_list()

        # default_ip가 리스트에 있으면 리스트 모드로 전환
        if default_ip and any(item.get("ip")==default_ip for item in self._ip_list):
            self.cb_manual.setChecked(False)
            # 해당 항목 선택
            for i in range(self.list_ips.count()):
                it = self.list_ips.item(i)
                data = it.data(Qt.UserRole)
                if data and data.get("ip")==default_ip:
                    self.list_ips.setCurrentItem(it)
                    break
        self._update_mode()  # 위젯 활성화/비활성 적용

    # --- IP 리스트 저장/로딩 ---
    def _load_ip_list(self):
        import json
        self._ip_list = []
        try:
            if os.path.exists(self._ip_list_path):
                with open(self._ip_list_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        # [{alias, ip}, ...] 기대
                        self._ip_list = [x for x in data if isinstance(x, dict) and "ip" in x]
        except Exception:
            pass
        self._refresh_list_widget()

    def _save_ip_list(self):
        import json, tempfile, shutil
        os.makedirs(os.path.dirname(self._ip_list_path), exist_ok=True)
        tmp = self._ip_list_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._ip_list, f, ensure_ascii=False, indent=2)
        # 원자적 교체
        shutil.move(tmp, self._ip_list_path)

    def _refresh_list_widget(self):
        self.list_ips.clear()
        for item in self._ip_list:
            alias = str(item.get("alias","")).strip() or "(무제)"
            ip = item.get("ip","")
            disp = f"{alias}({ip})"   # 예: test pc(192.168.2.111)
            lw = QListWidgetItem(disp)
            lw.setData(Qt.UserRole, {"alias": alias, "ip": ip})
            self.list_ips.addItem(lw)

    # --- 모드 토글 ---
    def _update_mode(self):
        manual = self.cb_manual.isChecked()
        self.ed_ip.setEnabled(manual)
        self.list_ips.setEnabled(not manual)
        self.btn_add_ip.setEnabled(not manual)
        self.lbl_err.setText("")

    # --- IP 추가 ---
    def _on_add_ip(self):
        dlg = IpEditDialog(self, title="IP 추가", ok_text="추가")
        if dlg.exec() == QDialog.Accepted:
            # 중복 IP면 별칭만 갱신
            for x in self._ip_list:
                if x.get("ip") == dlg.ip:
                    x["alias"] = dlg.alias
                    break
            else:
                self._ip_list.append({"alias": dlg.alias, "ip": dlg.ip})
            self._save_ip_list()
            self._refresh_list_widget()
            # 방금 추가/갱신한 항목 선택
            self._select_ip_in_list(dlg.ip)

    def _on_list_menu(self, pos):
        item = self.list_ips.itemAt(pos)
        if not item:
            return
        menu = QMenu(self)
        act_edit = QAction("편집", self)
        act_del  = QAction("삭제", self)
        act_edit.triggered.connect(lambda: self._edit_ip_item(item))
        act_del.triggered.connect(lambda: self._delete_ip_item(item))
        menu.addAction(act_edit)
        menu.addAction(act_del)
        menu.exec(self.list_ips.viewport().mapToGlobal(pos))

    def _edit_ip_item(self, item: QListWidgetItem):
        data = item.data(Qt.UserRole) or {}
        cur_alias = data.get("alias", "")
        cur_ip = data.get("ip", "")
        if not cur_ip:
            return
        dlg = IpEditDialog(self, title="IP 편집", ok_text="저장", alias=cur_alias, ip=cur_ip)
        if dlg.exec() == QDialog.Accepted:
            # 다른 항목과 IP 중복 체크
            for x in self._ip_list:
                if x.get("ip") == dlg.ip and x is not data:
                    QMessageBox.warning(self, "확인", "이미 존재하는 IP입니다.")
                    return
            # 기존 항목 갱신: IP가 바뀌면 키를 바꿔야 하므로 교체
            # self._ip_list는 dict 리스트이므로 대상 dict를 찾아 교체
            for i, x in enumerate(self._ip_list):
                if x.get("ip") == cur_ip and x.get("alias") == cur_alias:
                    self._ip_list[i] = {"alias": dlg.alias, "ip": dlg.ip}
                    break
            self._save_ip_list()
            self._refresh_list_widget()
            self._select_ip_in_list(dlg.ip)

    def _delete_ip_item(self, item: QListWidgetItem):
        data = item.data(Qt.UserRole) or {}
        ip = data.get("ip", "")
        alias = data.get("alias", "")
        if not ip:
            return
        ret = QMessageBox.question(self, "삭제 확인", f"선택한 항목을 삭제하시겠습니까?\n\n{alias} ({ip})")
        if ret != QMessageBox.Yes:
            return
        self._ip_list = [x for x in self._ip_list if x.get("ip") != ip]
        self._save_ip_list()
        self._refresh_list_widget()

    def _select_ip_in_list(self, ip: str):
        for i in range(self.list_ips.count()):
            it = self.list_ips.item(i)
            data = it.data(Qt.UserRole)
            if data and data.get("ip") == ip:
                self.list_ips.setCurrentItem(it)
                break


    # --- 연결 시도 ---
    def try_connect(self):
        import socket
        manual = self.cb_manual.isChecked()
        if manual:
            ip = self.ed_ip.text().strip()
            if not ip:
                self.lbl_err.setText("연결 실패: IP를 입력하세요.")
                return
        else:
            item = self.list_ips.currentItem()
            if not item:
                self.lbl_err.setText("연결 실패: IP 리스트에서 선택하세요.")
                return
            data = item.data(Qt.UserRole) or {}
            ip = data.get("ip","").strip()
            if not ip:
                self.lbl_err.setText("연결 실패: 선택한 항목에 IP가 없습니다.")
                return

        # probe: CONTROL → VIDEO → FILE 순
        def probe(port: int, timeout=2.0) -> bool:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(timeout); s.connect((ip, port)); s.close(); return True
            except Exception: return False

        ok = probe(CONTROL_PORT) or probe(VIDEO_PORT) or probe(FILE_PORT)
        if ok:
            # main.py가 ed_ip를 사용하므로 최종 IP를 주입
            self.ed_ip.setText(ip)
            self.accept()
        else:
            self.lbl_err.setText("연결 실패: 서버에 연결할 수 없습니다.")


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

# 필요한 모듈 임포트 (파일 상단에 이미 있다면 중복 제거 가능)
from PySide6.QtCore import Qt, QPoint, QTimer, QEvent
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QMessageBox, QApplication
)

from common import VIDEO_PORT, CONTROL_PORT, FILE_PORT
from net import VideoClient, ControlClient, FileClient
from utils import qt_to_vk

# ----------------------------------------------------------------------
# ClientWindow: 메인 클라이언트 창
#  - 상단 헤더(배지/버튼), 원격 화면 뷰, 파일전달 페이지(Stack)
#  - 몰입형 전체화면(프레임리스) + 상단 접근 시 "중앙 X 버튼" 노출
# ----------------------------------------------------------------------
class ClientWindow(QMainWindow):
    def __init__(self, server_ip: str):
        super().__init__()
        self.setWindowTitle("원격 뷰어 클라이언트")
        self.resize(1180, 760)
        self.server_ip = server_ip

        # --- 헤더/배지/버튼 ---
        self.header = TopHeader(self.on_fullscreen, self.toggle_transfer_page, self.on_reconnect, self.on_exit)
        self.header.update_ip(f"{self.server_ip}")

        # --- 페이지: 원격 뷰어 ---
        self.view = ViewerLabel()
        vlay = QVBoxLayout()
        vlay.setContentsMargins(12, 12, 12, 12)
        vlay.addWidget(self.view, 1)
        self.page_viewer = QWidget()
        self.page_viewer.setLayout(vlay)
        self.view.sig_mouse.connect(self.on_mouse_local)

        # 몰입형 전체화면 시 여백/상태 저장용
        self._viewer_layout = vlay
        self._viewer_margin_norm = (12, 12, 12, 12)
        self._immersive = False
        self._filter_installed = False  # 전역 이벤트 필터 설치 여부

        # --- 페이지: 파일 전달 ---
        self.fc = FileClient(self.server_ip, FILE_PORT)
        self.page_transfer = FileTransferPage(self.fc)

        # --- 스택 구성 ---
        self.stack = QStackedWidgetSafe()
        self.stack.addWidget(self.page_viewer)    # index 0
        self.stack.addWidget(self.page_transfer)  # index 1

        # --- 루트 레이아웃 ---
        root = QVBoxLayout()
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.header)
        root.addWidget(self.stack, 1)
        wrap = QWidget()
        wrap.setLayout(root)
        self.setCentralWidget(wrap)

        # 상태바 초기화
        self.statusBar()

        # --- 네트워크(영상/제어) ---
        self.vc = VideoClient(self.server_ip, VIDEO_PORT)
        self.vc.sig_status.connect(self.on_status)
        self.vc.sig_frame.connect(self.on_frame)
        self.vc.start()

        self.cc = ControlClient(self.server_ip, CONTROL_PORT)

        # --- 몰입형 전체화면: 중앙 X 버튼 오버레이 초기화 ---
        self._init_immersive_close_button()

    # ===================== 파일전달/페이지 토글 =====================
    def toggle_transfer_page(self):
        chk = self.header.btn_transfer.isChecked()
        # 파일전달 페이지 진입 시, 몰입형 전체화면이면 해제
        if chk and self._immersive:
            self.on_fullscreen()
        self.stack.setCurrentIndex(1 if chk else 0)
        if chk:
            self.statusBar().showMessage("파일 전달 모드입니다. Ctrl+C/Ctrl+V 사용 가능.", 5000)
        else:
            self.statusBar().clearMessage()

    # ===================== 상태/프레임 수신 =====================
    def on_status(self, fps: float, elapsed: int, connected: bool, mbps: float):
        self.header.update_time(elapsed if connected else 0)
        self.header.update_bw(mbps)
        if not connected and self.stack.currentIndex() == 0:
            self.view.setText("연결 끊김")

    def on_frame(self, qimg, w: int, h: int):
        if self.stack.currentIndex() == 0:
            self.view.set_remote_size(w, h)
            pm = QPixmap.fromImage(qimg)
            scaled = pm.scaled(self.view.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.view.setPixmap(scaled)

    def resizeEvent(self, e):
        # 원격 화면 리스케일
        if self.stack.currentIndex() == 0 and self.view.pixmap() and not self.view.pixmap().isNull():
            pm = self.view.pixmap()
            self.view.setPixmap(pm.scaled(self.view.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        # 중앙 X 버튼 위치 동기화
        self._layout_immersive_close()
        super().resizeEvent(e)

    def moveEvent(self, e):
        self._layout_immersive_close()
        super().moveEvent(e)

    def showEvent(self, e):
        self._layout_immersive_close()
        super().showEvent(e)

    # ===================== 입력 전달(마우스/키보드) =====================
    def on_mouse_local(self, ev: dict):
        if self.stack.currentIndex() != 0:
            return
        cursor = QPoint(int(ev.get("x", 0)), int(ev.get("y", 0)))
        rx, ry = self.view.map_to_remote(cursor)
        t = ev.get("t")
        if t == "move":
            self.cc.send_json({"t": "mouse_move", "x": rx, "y": ry})
        elif t == "down":
            self.cc.send_json({"t": "mouse_move", "x": rx, "y": ry})
            self.cc.send_json({"t": "mouse_down", "btn": ev.get("btn", "left")})
        elif t == "up":
            self.cc.send_json({"t": "mouse_up", "btn": ev.get("btn", "left")})
        elif t == "wheel":
            self.cc.send_json({"t": "mouse_wheel", "delta": int(ev.get("delta", 0))})

    def keyPressEvent(self, e):
        if self.stack.currentIndex() == 0 and not e.isAutoRepeat():
            vk = qt_to_vk(e)
            if vk:
                self.cc.send_json({"t": "key", "vk": int(vk), "down": True})
        else:
            super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if self.stack.currentIndex() == 0 and not e.isAutoRepeat():
            vk = qt_to_vk(e)
            if vk:
                self.cc.send_json({"t": "key", "vk": int(vk), "down": False})
        else:
            super().keyReleaseEvent(e)

    # ===================== 재연결/종료 =====================
    def on_reconnect(self):
        self.header.update_ip(f"{self.server_ip}")
        try:
            self.vc.stop()
            self.vc.wait(1000)
        except Exception:
            pass
        self.vc = VideoClient(self.server_ip, VIDEO_PORT)
        self.vc.sig_status.connect(self.on_status)
        self.vc.sig_frame.connect(self.on_frame)
        self.vc.start()

        self.cc = ControlClient(self.server_ip, CONTROL_PORT)
        self.fc = FileClient(self.server_ip, FILE_PORT)
        self.page_transfer.fc = self.fc
        self.page_transfer.refresh_server(None)

    def on_exit(self):
        self.close()

    def closeEvent(self, e):
        # 파일 전송 중이면 종료 보류
        try:
            if self.page_transfer.has_running_transfer():
                self.statusBar().showMessage("파일 전송 마무리 중…", 3000)
                self.page_transfer.wait_transfer_finish(15000)
                if self.page_transfer.has_running_transfer():
                    QMessageBox.warning(self, "알림", "파일 전송이 진행 중입니다. 완료 후 종료해 주세요.")
                    e.ignore()
                    return
        except Exception:
            pass
        try:
            self.vc.stop()
            self.vc.wait(1000)
        except Exception:
            pass
        super().closeEvent(e)

    # ===================== 몰입형 전체화면(프레임리스) =====================
    def on_fullscreen(self):
        self._immersive = not getattr(self, "_immersive", False)
        self._apply_immersive(self._immersive)

    def _set_global_filter(self, enable: bool):
        app = QApplication.instance()
        if not app:
            return
        if enable and not getattr(self, "_filter_installed", False):
            app.installEventFilter(self)
            self._filter_installed = True
        elif not enable and getattr(self, "_filter_installed", False):
            app.removeEventFilter(self)
            self._filter_installed = False

    def _apply_immersive(self, state: bool):
        if state:
            # 프레임 제거 + 헤더/상태바 숨김 + 여백 제거 + 뷰어 페이지 고정
            self.setWindowFlag(Qt.FramelessWindowHint, True)
            self.header.setVisible(False)
            self.statusBar().setVisible(False)
            self._viewer_layout.setContentsMargins(0, 0, 0, 0)
            self.stack.setCurrentIndex(0)

            # 전역 마우스 이벤트 필터 (전체화면에서만)
            self._set_global_filter(True)

            # 중앙 X 버튼 준비
            self._layout_immersive_close()
            self._hide_immersive_close(force=True)  # 기본은 숨김

            self.showFullScreen()
            self.raise_()
            self.btn_imm_close.raise_()
            QTimer.singleShot(0, self._layout_immersive_close)
        else:
            # 필터/타이머/버튼 정리
            self._set_global_filter(False)
            try:
                self._immersive_hide_timer.stop()
            except Exception:
                pass
            self._hide_immersive_close(force=True)

            # UI 복구
            self.setWindowFlag(Qt.FramelessWindowHint, False)
            self.header.setVisible(True)
            self.statusBar().setVisible(True)
            self._viewer_layout.setContentsMargins(*self._viewer_margin_norm)
            self.showNormal()

    # -------- 중앙 X 버튼(바 없이) --------
    def _init_immersive_close_button(self):
        # 중앙 X 버튼 (텍스트 대신 아이콘)
        self.btn_imm_close = QPushButton("", self)
        self.btn_imm_close.setObjectName("ImmersiveClose")
        self.btn_imm_close.setVisible(False)
        self.btn_imm_close.clicked.connect(lambda: self._apply_immersive(False))

        # 크기(완전한 원): 56x56
        self.btn_imm_close.setFixedSize(56, 56)

        # 하얀 X 아이콘을 직접 그려서 일관된 모양 보장
        def _make_close_icon(d=26, stroke=3.2, color=QColor("white")) -> QIcon:
            pm = QPixmap(d, d); pm.fill(Qt.transparent)
            p = QPainter(pm); p.setRenderHint(QPainter.Antialiasing, True)
            pen = QPen(color, stroke, Qt.SolidLine, Qt.RoundCap)
            p.setPen(pen)
            m = d * 0.28  # 안쪽 여백
            p.drawLine(m, m, d - m, d - m)
            p.drawLine(d - m, m, m, d - m)
            p.end()
            return QIcon(pm)

        self.btn_imm_close.setIcon(_make_close_icon())
        self.btn_imm_close.setIconSize(QSize(26, 26))

        # 약간의 그림자(띄워 보이도록)
        eff = QGraphicsDropShadowEffect(self.btn_imm_close)
        eff.setBlurRadius(24); eff.setOffset(0, 2); eff.setColor(QColor(0, 0, 0, 140))
        self.btn_imm_close.setGraphicsEffect(eff)

        # 자동 숨김 타이머(기존 로직 유지)
        self._immersive_hide_timer = QTimer(self)
        self._immersive_hide_timer.setSingleShot(True)
        self._immersive_hide_timer.timeout.connect(self._hide_immersive_close)

        # 마우스 트래킹(자식 포함)
        self.setMouseTracking(True)
        cw = self.centralWidget()
        if cw:
            self._enable_mouse_tracking_recursive(cw)


    def _enable_mouse_tracking_recursive(self, w: QWidget):
        w.setMouseTracking(True)
        for ch in w.findChildren(QWidget):
            ch.setMouseTracking(True)

    def _layout_immersive_close(self):
        if not hasattr(self, "btn_imm_close"):
            return
        btn = self.btn_imm_close
        btn.adjustSize()
        bw, bh = btn.width(), btn.height()
        x = (self.width() - bw) // 2
        y = 10
        btn.setGeometry(x, y, bw, bh)

    def _show_immersive_close(self):
        if getattr(self, "_immersive", False):
            if not self.btn_imm_close.isVisible():
                self.btn_imm_close.setVisible(True)
            self.btn_imm_close.raise_()

    def _hide_immersive_close(self, force: bool = False):
        if not hasattr(self, "btn_imm_close"):
            return
        if force or not self.btn_imm_close.underMouse():
            self.btn_imm_close.setVisible(False)

    # 전역 이벤트 필터: 상단 10px 접근 시 중앙 X 표시
    def eventFilter(self, obj, ev):
        if not getattr(self, "_immersive", False):
            return super().eventFilter(obj, ev)

        if ev.type() in (QEvent.MouseMove, QEvent.HoverMove):
            # ✅ 전역 커서 좌표 얻기
            gp = QCursor.pos()              # <-- QApplication.instance().cursor().pos() 대신
            p  = self.mapFromGlobal(gp)

            if not self.rect().contains(p):
                self._immersive_hide_timer.start(300)
                return super().eventFilter(obj, ev)

            # 상단 10px 접근 시 중앙 X 버튼 표시
            if p.y() <= 10 and p.x() >= self.width()/2 - 500 and p.x() <= self.width()/2 + 500:
                self._layout_immersive_close()
                self._show_immersive_close()
                self._immersive_hide_timer.start(1800)
            else:
                # 버튼 영역 밖이면 잠시 뒤 숨김
                if not self.btn_imm_close.geometry().contains(p):
                    self._immersive_hide_timer.start(600)

        return super().eventFilter(obj, ev)
