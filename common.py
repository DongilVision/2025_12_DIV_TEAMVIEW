# common.py
import socket

DEFAULT_HOST   = "0.0.0.0"
VIDEO_PORT     = 50007   # 영상 전송
CONTROL_PORT   = 50008   # 입력 제어
FILE_PORT      = 50009   # 파일/클립보드/활성폴더 질의
FRAME_FPS      = 12
JPEG_QUALITY   = 80

def get_local_ip() -> str:
    """현재 머신의 로컬 IP를 반환."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip
