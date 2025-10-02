# common.py
import socket

DEFAULT_HOST   = "0.0.0.0"
VIDEO_PORT     = 50007   # 영상 전송
CONTROL_PORT   = 50008   # 입력/클립보드 이벤트 채널(양방향)
FILE_PORT      = 50009   # 파일/클립보드 파일 업로드·다운로드
FRAME_FPS      = 12
JPEG_QUALITY   = 80

def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip
