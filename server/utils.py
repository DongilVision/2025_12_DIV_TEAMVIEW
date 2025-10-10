# server/utils.py
import json, struct

def recv_exact(sock, n: int) -> bytes | None:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)

def send_json(sock, obj: dict):
    raw = json.dumps(obj).encode("utf-8")
    sock.sendall(struct.pack(">I", len(raw)) + raw)

def hms(sec: int) -> str:
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:02d}"
