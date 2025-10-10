# client/net.py
import os, json, time, struct, socket, tempfile, zipfile
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage
import numpy as np, cv2

from utils import recv_exact, send_json, np_bgr_to_qimage

# ---------- 포트 상수 ----------
from common import VIDEO_PORT, CONTROL_PORT, FILE_PORT


# ----- 영상 수신 -----
class VideoClient(QThread):
    sig_status = Signal(float, int, bool, float)  # fps, elapsed, connected, mbps
    sig_frame  = Signal(QImage, int, int)
    def __init__(self, host: str, port: int = VIDEO_PORT):
        super().__init__(); self.host=host; self.port=port
        self._stop=False; self._sock=None
        self._connected=False; self._conn_ts=None
        self._cnt=0; self._last=time.time(); self._bytes=0
    def run(self):
        try:
            self._sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
            self._sock.settimeout(5.0); self._sock.connect((self.host,self.port))
            self._sock.settimeout(None); self._connected=True; self._conn_ts=time.time()
        except Exception:
            self.sig_status.emit(0.0,0,False,0.0); return
        try:
            while not self._stop:
                hdr=recv_exact(self._sock,12)
                if not hdr: break
                data_len,w,h=struct.unpack(">III",hdr)
                blob=recv_exact(self._sock,data_len)
                if not blob: break
                self._bytes += 12+len(blob)
                arr=np.frombuffer(blob,dtype=np.uint8)
                img=cv2.imdecode(arr,cv2.IMREAD_COLOR)
                if img is not None:
                    self.sig_frame.emit(np_bgr_to_qimage(img),w,h)
                    self._cnt+=1
                now=time.time()
                if now-self._last>=1.0:
                    fps=float(self._cnt); self._cnt=0
                    elapsed=int(now-(self._conn_ts or now))
                    mbps=(self._bytes*8.0)/1_000_000.0; self._bytes=0; self._last=now
                    self.sig_status.emit(fps,elapsed,self._connected,mbps)
        finally:
            try:
                if self._sock: self._sock.close()
            except Exception: pass
            self._connected=False; self.sig_status.emit(0.0,0,False,0.0)
    def stop(self): self._stop=True

# ----- 제어 송신 -----
class ControlClient:
    def __init__(self, host:str, port:int=CONTROL_PORT):
        self.host=host; self.port=port; self.sock=None; self.connect()
    def connect(self):
        try:
            if self.sock: self.sock.close()
        except: pass
        try:
            self.sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
            self.sock.settimeout(3.0); self.sock.connect((self.host,self.port))
            self.sock.settimeout(None)
        except Exception:
            self.sock=None
    def send_json(self,obj:dict):
        if not self.sock:
            self.connect()
            if not self.sock: return
        try:
            body=json.dumps(obj).encode("utf-8")
            head=struct.pack(">I",len(body))
            self.sock.sendall(head+body)
        except Exception:
            try: self.sock.close()
            except: pass
            self.sock=None

# ----- 파일 전송 -----
class FileClient:
    def __init__(self, host:str, port:int=FILE_PORT):
        self.host=host; self.port=port
    def _connect(self):
        s=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        s.settimeout(5.0); s.connect((self.host,self.port)); s.settimeout(None)
        return s

    def list_dir_server(self, path:str|None=None):
        s=self._connect()
        try:
            send_json(s, {"cmd":"ls","path": path or ""})
            jlen=struct.unpack(">I",recv_exact(s,4))[0]
            resp=json.loads(recv_exact(s,jlen).decode("utf-8","ignore"))
            return resp
        finally:
            s.close()

    def upload_to_dir(self, target_dir:str, local_paths:list[str], progress=None):
        metas=[]
        for p in local_paths:
            if os.path.isfile(p):
                metas.append({"name":os.path.basename(p),"size":int(os.path.getsize(p)),"path":p})
        if not metas: return (False,"no valid files")
        total=sum(m["size"] for m in metas); done=0
        s=self._connect()
        try:
            head={"cmd":"upload_to","target_dir":target_dir,"files":[{"name":m["name"],"size":m["size"]} for m in metas]}
            send_json(s,head)
            for m in metas:
                with open(m["path"],"rb") as f:
                    while True:
                        buf=f.read(1024*256)
                        if not buf: break
                        s.sendall(buf); done+=len(buf)
                        if progress: progress(done,total)
            jlen=struct.unpack(">I",recv_exact(s,4))[0]
            ack=json.loads(recv_exact(s,jlen).decode("utf-8","ignore"))
            return (bool(ack.get("ok")), "OK" if ack.get("ok") else ack.get("error",""))
        finally:
            s.close()

    def upload_tree_to(self, target_dir:str, local_paths:list[str], progress=None):
        entries=[]
        for p in local_paths:
            p=os.path.abspath(p)
            if os.path.isfile(p):
                entries.append({"rel":os.path.basename(p),"size":int(os.path.getsize(p)),"src":p})
            elif os.path.isdir(p):
                base=os.path.basename(p.rstrip("\\/")) or p
                for root,_dirs,fnames in os.walk(p):
                    for fn in fnames:
                        fp=os.path.join(root,fn)
                        rel_sub=os.path.relpath(fp,p)
                        rel=os.path.join(base,rel_sub)
                        entries.append({"rel":rel,"size":int(os.path.getsize(fp)),"src":fp})
        if not entries: return (False,"no files")
        total=sum(e["size"] for e in entries); done=0
        s=self._connect()
        try:
            head={"cmd":"upload_tree_to","target_dir":target_dir,"files":[{"rel":e["rel"],"size":e["size"]} for e in entries]}
            send_json(s,head)
            for e in entries:
                with open(e["src"],"rb") as f:
                    while True:
                        buf=f.read(1024*256)
                        if not buf: break
                        s.sendall(buf); done+=len(buf)
                        if progress: progress(done,total)
            jlen=struct.unpack(">I",recv_exact(s,4))[0]
            ack=json.loads(recv_exact(s,jlen).decode("utf-8","ignore"))
            return (bool(ack.get("ok")), "OK" if ack.get("ok") else ack.get("error",""))
        finally:
            s.close()

    def download_paths(self, server_paths:list[str], local_target_dir:str, progress=None):
        os.makedirs(local_target_dir, exist_ok=True)
        s=self._connect()
        try:
            send_json(s, {"cmd":"download_paths","paths": server_paths})
            jlen=struct.unpack(">I",recv_exact(s,4))[0]
            head=json.loads(recv_exact(s,jlen).decode("utf-8","ignore"))
            if not head.get("ok"): return (False, head.get("error",""))
            files=head.get("files",[])
            total=sum(int(m["size"]) for m in files); done=0
            for m in files:
                name=os.path.basename(m["name"]); size=int(m["size"])
                dst=os.path.join(local_target_dir,name)
                with open(dst,"wb") as f:
                    remain=size
                    while remain>0:
                        chunk=s.recv(min(1024*256,remain))
                        if not chunk: raise ConnectionError("file stream interrupted")
                        f.write(chunk); remain-=len(chunk); done+=len(chunk)
                        if progress: progress(done,total)
            return (True,"OK")
        finally:
            s.close()

    def download_tree_paths(self, server_paths:list[str], local_target_dir:str, progress=None):
        os.makedirs(local_target_dir, exist_ok=True)
        s=self._connect()
        try:
            send_json(s, {"cmd":"download_tree_paths","paths": server_paths})
            jlen=struct.unpack(">I",recv_exact(s,4))[0]
            head=json.loads(recv_exact(s,jlen).decode("utf-8","ignore"))
            if not head.get("ok"): return (False, head.get("error",""))
            files=head.get("files",[])
            total=sum(int(m["size"]) for m in files); done=0
            for m in files:
                rel=m["rel"]; size=int(m["size"])
                dst=os.path.join(local_target_dir,rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst,"wb") as f:
                    remain=size
                    while remain>0:
                        chunk=s.recv(min(1024*256,remain))
                        if not chunk: raise ConnectionError("file stream interrupted")
                        f.write(chunk); remain-=len(chunk); done+=len(chunk)
                        if progress: progress(done,total)
            return (True,"OK")
        finally:
            s.close()

    def download_paths_as_zip(self, server_paths:list[str], local_target_dir:str, zip_name:str|None=None, progress=None):
        os.makedirs(local_target_dir, exist_ok=True)
        s=self._connect()
        try:
            send_json(s, {"cmd":"download_paths_as_zip","paths": server_paths, "zip_name": zip_name or ""})
            jlen=struct.unpack(">I",recv_exact(s,4))[0]
            head=json.loads(recv_exact(s,jlen).decode("utf-8","ignore"))
            if not head.get("ok"): return (False, head.get("error",""))
            name=head.get("zip_name") or "bundle.zip"; size=int(head.get("size",0))
            dst=os.path.join(local_target_dir,name)
            done=0
            with open(dst,"wb") as f:
                remain=size
                while remain>0:
                    chunk=s.recv(min(1024*256,remain))
                    if not chunk: raise ConnectionError("zip stream interrupted")
                    f.write(chunk); remain-=len(chunk); done+=len(chunk)
                    if progress: progress(done,size)
            return (True,"OK")
        finally:
            s.close()

    def upload_zip_of_local(self, target_dir:str, src_paths:list[str], zip_name:str|None=None, progress=None):
        if not src_paths: return (False,"no source")
        tmp_dir=tempfile.gettempdir()
        if not zip_name:
            base=os.path.basename(os.path.abspath(src_paths[0])).rstrip("\\/")
            zip_name=f"{base}_{int(time.time())}.zip"
        zpath=os.path.join(tmp_dir,zip_name)
        try:
            with zipfile.ZipFile(zpath,"w",zipfile.ZIP_DEFLATED) as zf:
                for p in src_paths:
                    p=os.path.abspath(p)
                    if os.path.isfile(p): zf.write(p, arcname=os.path.basename(p))
                    elif os.path.isdir(p):
                        base=os.path.basename(p.rstrip("\\/")) or p
                        for root,_dirs,fnames in os.walk(p):
                            for fn in fnames:
                                fp=os.path.join(root,fn)
                                rel_sub=os.path.relpath(fp,p)
                                arc=os.path.join(base,rel_sub)
                                zf.write(fp, arcname=arc)
            return self.upload_to_dir(target_dir, [zpath], progress=progress)
        finally:
            try:
                if os.path.exists(zpath): os.remove(zpath)
            except Exception: pass
