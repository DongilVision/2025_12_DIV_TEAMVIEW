import sys
import socket
import threading
import time
import json
import struct
import io
import os
import tempfile
from datetime import datetime
from PySide6.QtWidgets import *
from PySide6.QtCore import *
from PySide6.QtGui import *
import mss
import pickle
import pyperclip
from PIL import Image
import pynput
from pynput.mouse import Button, Controller as MouseController
from pynput.keyboard import Key, Controller as KeyboardController

class RemoteServer(QThread):
    client_connected = Signal(str)
    client_disconnected = Signal()
    stats_updated = Signal(int, int)
    
    def __init__(self, port=5555):
        super().__init__()
        self.port = port
        self.server_socket = None
        self.clients = []
        self.running = False
        self.mouse = MouseController()
        self.keyboard = KeyboardController()
        self.screen_grabber = mss.mss()
        self.clipboard_content = None
        self.clipboard_files = []
        
    def run(self):
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind(('0.0.0.0', self.port))
        self.server_socket.listen(5)
        self.running = True
        
        print(f"서버가 포트 {self.port}에서 실행중...")
        
        while self.running:
            try:
                self.server_socket.settimeout(1.0)
                client_socket, addr = self.server_socket.accept()
                client_thread = threading.Thread(target=self.handle_client, args=(client_socket, addr))
                client_thread.daemon = True
                client_thread.start()
                self.clients.append({'socket': client_socket, 'addr': addr, 'connected_time': time.time()})
                self.client_connected.emit(f"{addr[0]}:{addr[1]}")
                self.update_stats()
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    print(f"연결 수락 오류: {e}")
    
    def handle_client(self, client_socket, addr):
        print(f"클라이언트 연결됨: {addr}")
        
        # 클라이언트가 준비될 때까지 잠시 대기
        time.sleep(0.5)
        
        screen_thread = threading.Thread(target=self.send_screen, args=(client_socket,))
        screen_thread.daemon = True
        screen_thread.start()
        
        try:
            while self.running:
                # 명령 수신
                header = client_socket.recv(8)
                if not header:
                    break
                    
                msg_type, msg_len = struct.unpack('!II', header)
                
                if msg_len > 0:
                    data = b''
                    while len(data) < msg_len:
                        chunk = client_socket.recv(min(4096, msg_len - len(data)))
                        if not chunk:
                            break
                        data += chunk
                    
                    try:
                        if msg_type == 1:  # Mouse move
                            x, y = struct.unpack('!ff', data)
                            self.mouse.position = (x, y)
                        elif msg_type == 2:  # Mouse click
                            x, y, button, pressed = struct.unpack('!ff?B', data[:9])
                            self.mouse.position = (x, y)
                            btn = Button.left if button == 1 else Button.right if button == 2 else Button.middle
                            if pressed:
                                self.mouse.press(btn)
                            else:
                                self.mouse.release(btn)
                        elif msg_type == 3:  # Mouse scroll
                            x, y, dx, dy = struct.unpack('!ffff', data)
                            self.mouse.position = (x, y)
                            self.mouse.scroll(dx, dy)
                        elif msg_type == 4:  # Keyboard
                            key_data = json.loads(data.decode())
                            self.handle_keyboard(key_data)
                        elif msg_type == 5:  # Clipboard text
                            text = data.decode('utf-8')
                            pyperclip.copy(text)
                        elif msg_type == 6:  # File transfer
                            self.handle_file_transfer(data, client_socket)
                        elif msg_type == 7:  # Request clipboard
                            self.send_clipboard(client_socket)
                    except Exception as cmd_error:
                        print(f"명령 처리 오류: {cmd_error}")
                        continue
                        
        except Exception as e:
            print(f"클라이언트 처리 오류: {e}")
        finally:
            # 클라이언트 제거
            self.clients = [c for c in self.clients if c['socket'] != client_socket]
            client_socket.close()
            self.client_disconnected.emit()
            self.update_stats()
            print(f"클라이언트 연결 종료: {addr}")
    
    def send_screen(self, client_socket):
        """화면 데이터를 지속적으로 전송"""
        try:
            while self.running:
                try:
                    monitor = self.screen_grabber.monitors[0]
                    screenshot = self.screen_grabber.grab(monitor)
                    
                    # PIL Image로 변환
                    img = Image.frombytes('RGB', (screenshot.width, screenshot.height), screenshot.rgb)
                    
                    # JPEG로 압축
                    img_byte_arr = io.BytesIO()
                    img.save(img_byte_arr, format='JPEG', quality=70)
                    img_data = img_byte_arr.getvalue()
                    
                    # 화면 크기 정보와 함께 전송
                    header = struct.pack('!IIII', 0, len(img_data), screenshot.width, screenshot.height)
                    client_socket.send(header + img_data)
                    
                    time.sleep(0.033)  # ~30 FPS
                except Exception as screen_error:
                    print(f"화면 캡처 오류: {screen_error}")
                    time.sleep(0.1)  # 오류 시 잠시 대기
                    continue
                    
        except Exception as e:
            print(f"화면 전송 오류: {e}")
    
    def handle_keyboard(self, key_data):
        """키보드 입력 처리"""
        action = key_data.get('action')
        key = key_data.get('key')
        
        if action == 'press':
            if len(key) == 1:
                self.keyboard.press(key)
            else:
                # 특수 키 처리
                special_keys = {
                    'space': Key.space,
                    'enter': Key.enter,
                    'backspace': Key.backspace,
                    'tab': Key.tab,
                    'escape': Key.esc,
                    'shift': Key.shift,
                    'ctrl': Key.ctrl,
                    'alt': Key.alt,
                    'delete': Key.delete,
                    'up': Key.up,
                    'down': Key.down,
                    'left': Key.left,
                    'right': Key.right,
                }
                if key.lower() in special_keys:
                    self.keyboard.press(special_keys[key.lower()])
        elif action == 'release':
            if len(key) == 1:
                self.keyboard.release(key)
            else:
                special_keys = {
                    'space': Key.space,
                    'enter': Key.enter,
                    'backspace': Key.backspace,
                    'tab': Key.tab,
                    'escape': Key.esc,
                    'shift': Key.shift,
                    'ctrl': Key.ctrl,
                    'alt': Key.alt,
                    'delete': Key.delete,
                    'up': Key.up,
                    'down': Key.down,
                    'left': Key.left,
                    'right': Key.right,
                }
                if key.lower() in special_keys:
                    self.keyboard.release(special_keys[key.lower()])
    
    def handle_file_transfer(self, data, client_socket):
        """파일 전송 처리"""
        try:
            file_info = pickle.loads(data)
            file_name = file_info['name']
            file_data = file_info['data']
            
            # 임시 디렉토리에 파일 저장
            temp_dir = tempfile.gettempdir()
            file_path = os.path.join(temp_dir, file_name)
            
            with open(file_path, 'wb') as f:
                f.write(file_data)
            
            # 클립보드에 파일 경로 저장
            self.clipboard_files = [file_path]
            
            print(f"파일 수신 완료: {file_name}")
            
        except Exception as e:
            print(f"파일 전송 오류: {e}")
    
    def send_clipboard(self, client_socket):
        """클립보드 내용 전송"""
        try:
            # 텍스트 클립보드 확인
            text = pyperclip.paste()
            if text:
                data = text.encode('utf-8')
                header = struct.pack('!II', 5, len(data))
                client_socket.send(header + data)
            
            # 파일 클립보드 확인 (Windows 전용 코드 필요)
            # 여기서는 간단한 구현
            
        except Exception as e:
            print(f"클립보드 전송 오류: {e}")
    
    def update_stats(self):
        """연결 통계 업데이트"""
        total_connections = len(self.clients)
        total_time = sum(time.time() - c['connected_time'] for c in self.clients)
        self.stats_updated.emit(total_connections, int(total_time))
    
    def stop(self):
        """서버 종료"""
        self.running = False
        if self.server_socket:
            self.server_socket.close()

class ServerWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.server = RemoteServer()
        self.init_ui()
        self.start_time = time.time()
        self.connections = 0
        
    def init_ui(self):
        self.setWindowTitle("원격 데스크톱 서버")
        self.setFixedSize(300, 150)
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.WindowCloseButtonHint)
        
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)
        
        # 서버 상태
        self.status_label = QLabel("서버 실행 중...")
        self.status_label.setStyleSheet("QLabel { color: green; font-weight: bold; }")
        layout.addWidget(self.status_label)
        
        # IP 주소 표시
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        self.ip_label = QLabel(f"서버 IP: {local_ip}:5555")
        layout.addWidget(self.ip_label)
        
        # 연결 정보
        self.connection_label = QLabel("연결 수: 0")
        layout.addWidget(self.connection_label)
        
        self.time_label = QLabel("총 연결 시간: 0초")
        layout.addWidget(self.time_label)
        
        # 종료 버튼
        self.close_btn = QPushButton("서버 종료")
        self.close_btn.clicked.connect(self.close_server)
        layout.addWidget(self.close_btn)
        
        self.setLayout(layout)
        
        # 서버 시작
        self.server.client_connected.connect(self.on_client_connected)
        self.server.client_disconnected.connect(self.on_client_disconnected)
        self.server.stats_updated.connect(self.update_stats)
        self.server.start()
        
        # 타이머 설정
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_time)
        self.timer.start(1000)
    
    def on_client_connected(self, addr):
        self.connections += 1
        self.connection_label.setText(f"연결 수: {self.connections}")
        QMessageBox.information(self, "연결", f"클라이언트 연결됨: {addr}")
    
    def on_client_disconnected(self):
        self.connections = max(0, self.connections - 1)
        self.connection_label.setText(f"연결 수: {self.connections}")
    
    def update_stats(self, connections, total_time):
        self.connection_label.setText(f"연결 수: {connections}")
        self.time_label.setText(f"총 연결 시간: {total_time}초")
    
    def update_time(self):
        elapsed = int(time.time() - self.start_time)
        self.status_label.setText(f"서버 실행 중... ({elapsed}초)")
    
    def close_server(self):
        reply = QMessageBox.question(self, "확인", "서버를 종료하시겠습니까?",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.server.stop()
            self.close()
    
    def closeEvent(self, event):
        self.server.stop()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ServerWindow()
    window.show()
    sys.exit(app.exec())