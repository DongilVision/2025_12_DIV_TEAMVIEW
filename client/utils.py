# client/utils.py
import os, json, struct
import numpy as np, cv2
from datetime import datetime
from PySide6.QtGui import QImage
from PySide6.QtCore import Qt

# ----- 이미지 변환 -----
def np_bgr_to_qimage(bgr: np.ndarray) -> QImage:
    h, w, _ = bgr.shape
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    return QImage(rgb.data, w, h, 3*w, QImage.Format_RGB888).copy()

# ----- 소켓 I/O -----
def recv_exact(sock, n: int) -> bytes | None:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk: return None
        buf += chunk
    return bytes(buf)

def send_json(sock, obj: dict):
    raw = json.dumps(obj).encode("utf-8")
    sock.sendall(struct.pack(">I", len(raw)) + raw)

# ----- 표시 유틸 -----
def human_size(n: int) -> str:
    if n is None: return ""
    if n < 1024: return f"{n} B"
    units=["KB","MB","GB","TB","PB"]; x=float(n); i=-1
    while x >= 1024 and i < len(units)-1:
        x/=1024.0; i+=1
    return f"{x:.1f} {units[i]}"

def fmt_mtime(epoch: float|int|None) -> str:
    if not epoch: return ""
    try: return datetime.fromtimestamp(float(epoch)).strftime("%Y-%m-%d %H:%M")
    except Exception: return ""

# ----- Qt Key → Windows VK 매핑 -----
VK = {
    "F1":0x70,"F2":0x71,"F3":0x72,"F4":0x73,"F5":0x74,"F6":0x75,"F7":0x76,"F8":0x77,
    "F9":0x78,"F10":0x79,"F11":0x7A,"F12":0x7B,"ESC":0x1B,"TAB":0x09,"ENTER":0x0D,"BACK":0x08,
    "SPACE":0x20,"LEFT":0x25,"UP":0x26,"RIGHT":0x27,"DOWN":0x28,"INSERT":0x2D,"DELETE":0x2E,
    "HOME":0x24,"END":0x23,"PGUP":0x21,"PGDN":0x22,"CAPSLOCK":0x14,"NUMLOCK":0x90,"SCROLLLOCK":0x91,
    "PRINT":0x2C,"PAUSE":0x13,"SHIFT":0x10,"CTRL":0x11,"ALT":0x12,"WIN":0x5B,"RWIN":0x5C,"APPS":0x5D,
    "HANGUL":0x15,"HANJA":0x19,"NP0":0x60,"NP1":0x61,"NP2":0x62,"NP3":0x63,"NP4":0x64,"NP5":0x65,
    "NP6":0x66,"NP7":0x67,"NP8":0x68,"NP9":0x69,"NP_MUL":0x6A,"NP_ADD":0x6B,"NP_SEP":0x6C,
    "NP_SUB":0x6D,"NP_DEC":0x6E,"NP_DIV":0x6F,"OEM_1":0xBA,"OEM_PLUS":0xBB,"OEM_COMMA":0xBC,
    "OEM_MINUS":0xBD,"OEM_PERIOD":0xBE,"OEM_2":0xBF,"OEM_3":0xC0,"OEM_4":0xDB,"OEM_5":0xDC,
    "OEM_6":0xDD,"OEM_7":0xDE,
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
    if k == Qt.Key_Left: return VK["LEFT"]
    if k == Qt.Key_Right: return VK["RIGHT"]
    if k == Qt.Key_Up: return VK["UP"]
    if k == Qt.Key_Down: return VK["DOWN"]
    if k == Qt.Key_Insert: return VK["INSERT"]
    if k == Qt.Key_Delete: return VK["DELETE"]
    if k == Qt.Key_Home: return VK["HOME"]
    if k == Qt.Key_End: return VK["END"]
    if k == Qt.Key_PageUp: return VK["PGUP"]
    if k == Qt.Key_PageDown: return VK["PGDN"]
    if k == Qt.Key_CapsLock: return VK["CAPSLOCK"]
    if k == Qt.Key_NumLock:  return VK["NUMLOCK"]
    if k == Qt.Key_ScrollLock: return VK["SCROLLLOCK"]
    if k == Qt.Key_Print: return VK["PRINT"]
    if k == Qt.Key_Pause: return VK["PAUSE"]
    if k == Qt.Key_Menu:  return VK["APPS"]
    if k == Qt.Key_Hangul: return VK["HANGUL"]
    if k == Qt.Key_Hangul_Hanja: return VK["HANJA"]
    if Qt.Key_F1 <= k <= Qt.Key_F24: return 0x70 + (k - Qt.Key_F1)
    if (mods & Qt.KeypadModifier):
        mapping = {
            Qt.Key_0:"NP0", Qt.Key_1:"NP1", Qt.Key_2:"NP2", Qt.Key_3:"NP3", Qt.Key_4:"NP4",
            Qt.Key_5:"NP5", Qt.Key_6:"NP6", Qt.Key_7:"NP7", Qt.Key_8:"NP8", Qt.Key_9:"NP9",
            Qt.Key_Asterisk:"NP_MUL", Qt.Key_Plus:"NP_ADD", Qt.Key_Minus:"NP_SUB",
            Qt.Key_Slash:"NP_DIV", Qt.Key_Period:"NP_DEC"
        }
        for qk, name in mapping.items():
            if k == qk: return VK[name]
    if Qt.Key_0 <= k <= Qt.Key_9: return ord(str(k - Qt.Key_0))
    if Qt.Key_A <= k <= Qt.Key_Z: return ord(chr(k))
    oem_map = {
        Qt.Key_Semicolon:"OEM_1", Qt.Key_Equal:"OEM_PLUS", Qt.Key_Comma:"OEM_COMMA",
        Qt.Key_Minus:"OEM_MINUS", Qt.Key_Period:"OEM_PERIOD", Qt.Key_Slash:"OEM_2",
        Qt.Key_QuoteLeft:"OEM_3", Qt.Key_BracketLeft:"OEM_4", Qt.Key_Backslash:"OEM_5",
        Qt.Key_BracketRight:"OEM_6", Qt.Key_Apostrophe:"OEM_7",
    }
    for qk,name in oem_map.items():
        if k == qk: return VK[name]
    return 0
