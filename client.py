import sys
import socket
import threading
import time
import json
import struct
import io
import os
import pickle
from PySide6.QtWidgets import *
from PySide6.QtCore import *
from PySide6.QtGui import *
from PIL import Image
import pyperclip

class RemoteClient(QThread):
    screen_received = Signal(QImage, int, int)
    connection_status = Signal(bool)
    stats_updated = Signal(float)
    
    def __init__(self):
        super().__init__()
        self.socket = None
        self.connected = False
        self.running = False
        self.server_ip = "192.168.2.130"
        self.server_port = 5555
        self.last_frame_time = time.time()
        self.fps = 0
        self.frame_count = 0
        self.fps_timer = time.time()
        
    def connect_to_server(self, ip, port):
        """서버에 연결"""
        try:
            self.server_ip = ip
            self.server_port = port
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.connect((ip, port))
            self.connected = True
            self.running = True
            self.connection_status.emit(True)
            return True
        except Exception as e:
            print(f"연결 실패: {e}")
            self.connection_status.emit(False)
            return False
    
    def run(self):
        """화면 데이터 수신"""
        try:
            while self.running and self.connected:
                # 헤더 수신
                header = self.socket.recv(16)
                if not header or len(header) < 16:
                    break
                
                msg_type, msg_len, width, height = struct.unpack('!IIII', header)
                
                if msg_type == 0:  # Screen data
                    # 이미지 데이터 수신
                    img_data = b''
                    while len(img_data) < msg_len:
                        chunk = self.socket.recv(min(65536, msg_len - len(img_data)))
                        if not chunk:
                            break
                        img_data += chunk
                    
                    if len(img_data) == msg_len:
                        # 이미지 디코드
                        img = Image.open(io.BytesIO(img_data))
                        
                        # QImage로 변환
                        img_rgb = img.convert('RGB')
                        data = img_rgb.tobytes('raw', 'RGB')
                        qimg = QImage(data, img_rgb.width, img_rgb.height, QImage.Format_RGB888)
                        
                        self.screen_received.emit(qimg, width, height)
                        
                        # FPS 계산
                        self.frame_count += 1
                        current_time = time.time()
                        if current_time - self.fps_timer >= 1.0:
                            self.fps = self.frame_count
                            self.frame_count = 0
                            self.fps_timer = current_time
                            self.stats_updated.emit(self.fps)
                            
        except Exception as e:
            print(f"수신 오류: {e}")
        finally:
            self.disconnect()
    
    def send_mouse_move(self, x, y):
        """마우스 이동 전송"""
        if self.connected:
            try:
                data = struct.pack('!ff', x, y)
                header = struct.pack('!II', 1, len(data))
                self.socket.send(header + data)
            except:
                pass
    
    def send_mouse_click(self, x, y, button, pressed):
        """마우스 클릭 전송"""
        if self.connected:
            try:
                data = struct.pack('!ff?B', x, y, pressed, button)
                header = struct.pack('!II', 2, len(data))
                self.socket.send(header + data)
            except:
                pass
    
    def send_mouse_scroll(self, x, y, dx, dy):
        """마우스 스크롤 전송"""
        if self.connected:
            try:
                data = struct.pack('!ffff', x, y, dx, dy)
                header = struct.pack('!II', 3, len(data))
                self.socket.send(header + data)
            except:
                pass
    
    def send_keyboard(self, action, key):
        """키보드 입력 전송"""
        if self.connected:
            try:
                key_data = json.dumps({'action': action, 'key': key})
                data = key_data.encode()
                header = struct.pack('!II', 4, len(data))
                self.socket.send(header + data)
            except:
                pass
    
    def send_clipboard_text(self, text):
        """클립보드 텍스트 전송"""
        if self.connected:
            try:
                data = text.encode('utf-8')
                header = struct.pack('!II', 5, len(data))
                self.socket.send(header + data)
            except:
                pass
    
    def send_file(self, file_path):
        """파일 전송"""
        if self.connected and os.path.exists(file_path):
            try:
                file_name = os.path.basename(file_path)
                with open(file_path, 'rb') as f:
                    file_data = f.read()
                
                file_info = {
                    'name': file_name,
                    'data': file_data
                }
                
                data = pickle.dumps(file_info)
                header = struct.pack('!II', 6, len(data))
                self.socket.send(header + data)
                print(f"파일 전송 완료: {file_name}")
            except Exception as e:
                print(f"파일 전송 오류: {e}")
    
    def request_clipboard(self):
        """클립보드 내용 요청"""
        if self.connected:
            try:
                header = struct.pack('!II', 7, 0)
                self.socket.send(header)
            except:
                pass
    
    def disconnect(self):
        """연결 종료"""
        self.connected = False
        self.running = False
        if self.socket:
            self.socket.close()
        self.connection_status.emit(False)

class RemoteDesktopViewer(QWidget):
    def __init__(self):
        super().__init__()
        self.client = RemoteClient()
        self.screen_width = 1920
        self.screen_height = 1080
        self.scale_mode = "fit"  # fit or original
        self.connection_time = 0
        self.connected = False
        self.current_image = None
        self.init_ui()
        
    def init_ui(self):
        self.setWindowTitle("원격 데스크톱 클라이언트")
        self.resize(1024, 768)
        
        # 메인 레이아웃
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # 상태바 (24픽셀 높이)
        self.status_bar = QWidget()
        self.status_bar.setFixedHeight(24)
        self.status_bar.setStyleSheet("background-color: #2c3e50; color: white;")
        
        status_layout = QHBoxLayout()
        status_layout.setContentsMargins(5, 0, 5, 0)
        
        # 서버 IP
        self.server_label = QLabel("미연결")
        self.server_label.setStyleSheet("color: white; font-size: 12px;")
        status_layout.addWidget(self.server_label)
        
        # 연결 시간
        self.time_label = QLabel("연결 시간: 00:00:00")
        self.time_label.setStyleSheet("color: white; font-size: 12px;")
        status_layout.addWidget(self.time_label)
        
        # FPS
        self.fps_label = QLabel("FPS: 0")
        self.fps_label.setStyleSheet("color: white; font-size: 12px;")
        status_layout.addWidget(self.fps_label)
        
        status_layout.addStretch()
        
        # 크기 조절 버튼들
        self.fit_btn = QPushButton("전체 크기")
        self.fit_btn.setFixedHeight(20)
        self.fit_btn.clicked.connect(lambda: self.set_scale_mode("fit"))
        status_layout.addWidget(self.fit_btn)
        
        self.original_btn = QPushButton("원격 해상도 유지")
        self.original_btn.setFixedHeight(20)
        self.original_btn.clicked.connect(lambda: self.set_scale_mode("original"))
        status_layout.addWidget(self.original_btn)
        
        self.status_bar.setLayout(status_layout)
        main_layout.addWidget(self.status_bar)
        
        # 스크롤 영역 (원격 화면 표시)
        self.scroll_area = QScrollArea()
        self.screen_label = QLabel()
        self.screen_label.setAlignment(Qt.AlignCenter)
        self.screen_label.setStyleSheet("background-color: black;")
        self.screen_label.setMinimumSize(800, 600)
        self.screen_label.setMouseTracking(True)
        
        # 마우스 이벤트 설치
        self.screen_label.installEventFilter(self)
        
        self.scroll_area.setWidget(self.screen_label)
        self.scroll_area.setWidgetResizable(True)
        main_layout.addWidget(self.scroll_area)
        
        self.setLayout(main_layout)
        
        # 신호 연결
        self.client.screen_received.connect(self.update_screen)
        self.client.connection_status.connect(self.on_connection_status)
        self.client.stats_updated.connect(self.update_fps)
        
        # 타이머 설정
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_connection_time)
        self.timer.start(1000)
        
        # 연결 대화상자 표시
        self.show_connection_dialog()
    
    def show_connection_dialog(self):
        """연결 대화상자"""
        dialog = QDialog(self)
        dialog.setWindowTitle("서버 연결")
        dialog.setModal(True)
        
        layout = QVBoxLayout()
        
        # IP 입력
        ip_layout = QHBoxLayout()
        ip_layout.addWidget(QLabel("서버 IP:"))
        self.ip_input = QLineEdit("127.0.0.1")
        ip_layout.addWidget(self.ip_input)
        layout.addLayout(ip_layout)
        
        # 포트 입력
        port_layout = QHBoxLayout()
        port_layout.addWidget(QLabel("포트:"))
        self.port_input = QLineEdit("5555")
        port_layout.addWidget(self.port_input)
        layout.addLayout(port_layout)
        
        # 버튼
        btn_layout = QHBoxLayout()
        connect_btn = QPushButton("연결")
        connect_btn.clicked.connect(lambda: self.connect_to_server(dialog))
        btn_layout.addWidget(connect_btn)
        
        cancel_btn = QPushButton("취소")
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(cancel_btn)
        
        layout.addLayout(btn_layout)
        dialog.setLayout(layout)
        
        dialog.exec()
    
    def connect_to_server(self, dialog):
        """서버 연결"""
        ip = self.ip_input.text()
        port = int(self.port_input.text())
        
        if self.client.connect_to_server(ip, port):
            self.server_label.setText(f"서버: {ip}:{port}")
            self.connection_start_time = time.time()
            self.connected = True
            self.client.start()
            dialog.accept()
        else:
            QMessageBox.warning(self, "연결 실패", "서버에 연결할 수 없습니다.")
    
    def on_connection_status(self, connected):
        """연결 상태 변경"""
        self.connected = connected
        if not connected:
            self.server_label.setText("미연결")
            QMessageBox.information(self, "연결 종료", "서버와의 연결이 종료되었습니다.")
    
    def update_screen(self, qimage, width, height):
        """화면 업데이트"""
        self.screen_width = width
        self.screen_height = height
        self.current_image = qimage
        
        if self.scale_mode == "fit":
            # 창 크기에 맞춤
            pixmap = QPixmap.fromImage(qimage)
            scaled_pixmap = pixmap.scaled(self.scroll_area.size() - QSize(2, 2), 
                                          Qt.KeepAspectRatio, 
                                          Qt.SmoothTransformation)
            self.screen_label.setPixmap(scaled_pixmap)
        else:
            # 원본 크기 유지
            pixmap = QPixmap.fromImage(qimage)
            self.screen_label.setPixmap(pixmap)
            self.screen_label.resize(pixmap.size())
    
    def update_fps(self, fps):
        """FPS 업데이트"""
        self.fps_label.setText(f"FPS: {fps}")
    
    def update_connection_time(self):
        """연결 시간 업데이트"""
        if self.connected:
            elapsed = int(time.time() - self.connection_start_time)
            hours = elapsed // 3600
            minutes = (elapsed % 3600) // 60
            seconds = elapsed % 60
            self.time_label.setText(f"연결 시간: {hours:02d}:{minutes:02d}:{seconds:02d}")
    
    def set_scale_mode(self, mode):
        """화면 크기 모드 설정"""
        self.scale_mode = mode
        if self.current_image:
            self.update_screen(self.current_image, self.screen_width, self.screen_height)
    
    def eventFilter(self, source, event):
        """이벤트 필터 (마우스/키보드 이벤트 처리)"""
        if source == self.screen_label and self.connected:
            # 마우스 좌표 계산
            if self.scale_mode == "fit" and self.screen_label.pixmap():
                scale_x = self.screen_width / self.screen_label.pixmap().width()
                scale_y = self.screen_height / self.screen_label.pixmap().height()
            else:
                scale_x = 1.0
                scale_y = 1.0
            
            if event.type() == QEvent.MouseMove:
                x = event.position().x() * scale_x
                y = event.position().y() * scale_y
                self.client.send_mouse_move(x, y)
                
            elif event.type() == QEvent.MouseButtonPress:
                x = event.position().x() * scale_x
                y = event.position().y() * scale_y
                button = 1 if event.button() == Qt.LeftButton else 2 if event.button() == Qt.RightButton else 3
                self.client.send_mouse_click(x, y, button, True)
                
            elif event.type() == QEvent.MouseButtonRelease:
                x = event.position().x() * scale_x
                y = event.position().y() * scale_y
                button = 1 if event.button() == Qt.LeftButton else 2 if event.button() == Qt.RightButton else 3
                self.client.send_mouse_click(x, y, button, False)
                
            elif event.type() == QEvent.Wheel:
                x = event.position().x() * scale_x
                y = event.position().y() * scale_y
                delta = event.angleDelta()
                dx = delta.x() / 120.0
                dy = delta.y() / 120.0
                self.client.send_mouse_scroll(x, y, dx, dy)
        
        return super().eventFilter(source, event)
    
    def keyPressEvent(self, event):
        """키보드 눌림 이벤트"""
        if self.connected:
            key = event.text() if event.text() else QKeySequence(event.key()).toString()
            
            # Ctrl+C/V 처리 (파일 복사/붙여넣기)
            if event.modifiers() & Qt.ControlModifier:
                if event.key() == Qt.Key_C:
                    # 클립보드 내용 가져오기
                    clipboard = QApplication.clipboard()
                    mime_data = clipboard.mimeData()
                    
                    if mime_data.hasText():
                        self.client.send_clipboard_text(mime_data.text())
                    elif mime_data.hasUrls():
                        # 파일 복사
                        for url in mime_data.urls():
                            file_path = url.toLocalFile()
                            if os.path.exists(file_path):
                                self.client.send_file(file_path)
                    
                elif event.key() == Qt.Key_V:
                    # 서버로부터 클립보드 요청
                    self.client.request_clipboard()
            
            self.client.send_keyboard('press', key)
    
    def keyReleaseEvent(self, event):
        """키보드 놓임 이벤트"""
        if self.connected:
            key = event.text() if event.text() else QKeySequence(event.key()).toString()
            self.client.send_keyboard('release', key)
    
    def closeEvent(self, event):
        """창 닫기 이벤트"""
        if self.connected:
            self.client.disconnect()
        event.accept()

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.init_ui()
        
    def init_ui(self):
        self.setWindowTitle("원격 데스크톱 클라이언트")
        
        # 중앙 위젯
        self.viewer = RemoteDesktopViewer()
        self.setCentralWidget(self.viewer)
        
        # 메뉴바
        menubar = self.menuBar()
        
        # 파일 메뉴
        file_menu = menubar.addMenu("파일")
        
        connect_action = QAction("새 연결", self)
        connect_action.triggered.connect(self.viewer.show_connection_dialog)
        file_menu.addAction(connect_action)
        
        disconnect_action = QAction("연결 끊기", self)
        disconnect_action.triggered.connect(self.disconnect)
        file_menu.addAction(disconnect_action)
        
        file_menu.addSeparator()
        
        exit_action = QAction("종료", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        
        # 보기 메뉴
        view_menu = menubar.addMenu("보기")
        
        fit_action = QAction("전체 화면에 맞춤", self)
        fit_action.triggered.connect(lambda: self.viewer.set_scale_mode("fit"))
        view_menu.addAction(fit_action)
        
        original_action = QAction("원본 크기", self)
        original_action.triggered.connect(lambda: self.viewer.set_scale_mode("original"))
        view_menu.addAction(original_action)
        
        view_menu.addSeparator()
        
        fullscreen_action = QAction("전체 화면", self)
        fullscreen_action.setShortcut("F11")
        fullscreen_action.triggered.connect(self.toggle_fullscreen)
        view_menu.addAction(fullscreen_action)
        
        # 도구 메뉴
        tools_menu = menubar.addMenu("도구")
        
        clipboard_action = QAction("클립보드 동기화", self)
        clipboard_action.triggered.connect(self.sync_clipboard)
        tools_menu.addAction(clipboard_action)
        
        file_transfer_action = QAction("파일 전송", self)
        file_transfer_action.triggered.connect(self.show_file_transfer)
        tools_menu.addAction(file_transfer_action)
        
        # 도움말 메뉴
        help_menu = menubar.addMenu("도움말")
        
        about_action = QAction("정보", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)
        
        # 상태바
        self.statusBar().showMessage("준비")
        
        # 창 크기 설정
        self.resize(1024, 768)
        self.center_window()
    
    def center_window(self):
        """창을 화면 중앙에 배치"""
        screen = QApplication.primaryScreen().geometry()
        size = self.geometry()
        x = (screen.width() - size.width()) // 2
        y = (screen.height() - size.height()) // 2
        self.move(x, y)
    
    def disconnect(self):
        """연결 끊기"""
        if self.viewer.connected:
            reply = QMessageBox.question(self, "확인", "연결을 끊으시겠습니까?",
                                        QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.Yes:
                self.viewer.client.disconnect()
                self.statusBar().showMessage("연결 종료됨")
    
    def toggle_fullscreen(self):
        """전체 화면 토글"""
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()
    
    def sync_clipboard(self):
        """클립보드 동기화"""
        if self.viewer.connected:
            self.viewer.client.request_clipboard()
            QMessageBox.information(self, "클립보드", "클립보드가 동기화되었습니다.")
        else:
            QMessageBox.warning(self, "경고", "서버에 연결되어 있지 않습니다.")
    
    def show_file_transfer(self):
        """파일 전송 대화상자"""
        if not self.viewer.connected:
            QMessageBox.warning(self, "경고", "서버에 연결되어 있지 않습니다.")
            return
        
        dialog = QDialog(self)
        dialog.setWindowTitle("파일 전송")
        dialog.setModal(True)
        dialog.resize(400, 200)
        
        layout = QVBoxLayout()
        
        # 설명
        info_label = QLabel("파일을 선택하여 원격 컴퓨터로 전송합니다.")
        layout.addWidget(info_label)
        
        # 파일 선택
        file_layout = QHBoxLayout()
        self.file_path_edit = QLineEdit()
        file_layout.addWidget(self.file_path_edit)
        
        browse_btn = QPushButton("찾아보기...")
        browse_btn.clicked.connect(self.browse_file)
        file_layout.addWidget(browse_btn)
        
        layout.addLayout(file_layout)
        
        # 전송 버튼
        btn_layout = QHBoxLayout()
        send_btn = QPushButton("전송")
        send_btn.clicked.connect(lambda: self.send_file(dialog))
        btn_layout.addWidget(send_btn)
        
        cancel_btn = QPushButton("취소")
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(cancel_btn)
        
        layout.addLayout(btn_layout)
        dialog.setLayout(layout)
        
        dialog.exec()
    
    def browse_file(self):
        """파일 찾아보기"""
        file_path, _ = QFileDialog.getOpenFileName(self, "파일 선택")
        if file_path:
            self.file_path_edit.setText(file_path)
    
    def send_file(self, dialog):
        """파일 전송"""
        file_path = self.file_path_edit.text()
        if os.path.exists(file_path):
            self.viewer.client.send_file(file_path)
            QMessageBox.information(self, "전송 완료", f"파일이 전송되었습니다: {os.path.basename(file_path)}")
            dialog.accept()
        else:
            QMessageBox.warning(self, "오류", "파일을 찾을 수 없습니다.")
    
    def show_about(self):
        """프로그램 정보"""
        about_text = """
        <h2>원격 데스크톱 클라이언트</h2>
        <p>버전 1.0.0</p>
        <p>원격 컴퓨터를 제어하고 파일을 전송할 수 있는 프로그램입니다.</p>
        <p>기능:</p>
        <ul>
        <li>실시간 화면 전송</li>
        <li>마우스/키보드 제어</li>
        <li>파일 복사/붙여넣기</li>
        <li>클립보드 동기화</li>
        </ul>
        """
        QMessageBox.about(self, "정보", about_text)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("원격 데스크톱 클라이언트")
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())