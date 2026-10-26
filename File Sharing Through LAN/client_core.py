"""GUI-independent client library for the LAN File Sharing protocol (V3)."""
import hashlib
import os
import socket
import threading
import time

from protocol import (CHUNK, VERSION, ProtocolError, RemoteError, recv_msg,
                      send_msg, sha256_file)


class Progress:
    """Throttled progress reporter: cb(done, total, speed_bytes_per_s, eta_s)."""

    def __init__(self, total, cb, label=""):
        self.total, self.cb, self.label = total, cb, label
        self.start = time.monotonic()
        self.last = 0.0

    def update(self, done, force=False):
        if not self.cb:
            return
        now = time.monotonic()
        if not force and now - self.last < 0.1 and done < self.total:
            return
        self.last = now
        elapsed = max(now - self.start, 1e-6)
        speed = done / elapsed
        eta = (self.total - done) / speed if speed > 0 else float("inf")
        self.cb(done, self.total, speed, eta)


class FileClient:
    def __init__(self, timeout=30):
        self.sock = None
        self.timeout = timeout
        self.lock = threading.RLock()
        self.user = None

    # ------------------------------------------------------------ session
    def connect(self, host, port, user, password):
        self.close()
        s = socket.create_connection((host, int(port)), timeout=self.timeout)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock = s
        try:
            reply = self._call({"cmd": "HELLO", "version": VERSION})
            reply = self._call({"cmd": "LOGIN", "user": user, "password": password})
            self.user = reply.get("user", user)
        except Exception:
            self.close()
            raise

    def close(self):
        with self.lock:
            if self.sock:
                try:
                    send_msg(self.sock, {"cmd": "QUIT"})
                except OSError:
                    pass
                try:
                    self.sock.close()
                except OSError:
                    pass
            self.sock = None
            self.user = None

    @property
    def connected(self):
        return self.sock is not None

    def _call(self, msg):
        send_msg(self.sock, msg)
        return self._reply()

    def _reply(self):
        r = recv_msg(self.sock)
        if not r.get("ok"):
            raise RemoteError(r.get("error", "Unknown error"), r.get("code", "error"))
        return r

    # ------------------------------------------------------------ commands
    def list_files(self):
        with self.lock:
            return self._call({"cmd": "LIST"})["files"]

    def rename(self, name, new_name):
        with self.lock:
            self._call({"cmd": "RENAME", "name": name, "new_name": new_name})

    def delete(self, name):
        with self.lock:
            self._call({"cmd": "DELETE", "name": name})

    def upload(self, path, remote_name=None, overwrite=False, progress=None):
        """Upload with mandatory SHA256.  Raises RemoteError(code='exists') if present."""
        name = remote_name or os.path.basename(path)
        size = os.path.getsize(path)
        digest = sha256_file(path)
        with self.lock:
            self._call({"cmd": "UPLOAD", "name": name, "size": size,
                        "sha256": digest, "overwrite": overwrite})
            prog = Progress(size, progress)
            sent = 0
            prog.update(0, force=True)
            with open(path, "rb") as f:
                while sent < size:
                    block = f.read(CHUNK)
                    if not block:
                        raise IOError("File changed during upload")
                    self.sock.sendall(block)
                    sent += len(block)
                    prog.update(sent)
            prog.update(size, force=True)
            return self._reply()

    def download(self, name, dest, progress=None):
        """Download to dest atomically (via .part) and verify SHA256."""
        part = dest + ".part"
        with self.lock:
            meta = self._call({"cmd": "DOWNLOAD", "name": name})
            size, expected = int(meta["size"]), meta["sha256"]
            prog = Progress(size, progress)
            prog.update(0, force=True)
            h = hashlib.sha256()
            got = 0
            try:
                with open(part, "wb") as f:
                    while got < size:
                        chunk = self.sock.recv(min(CHUNK, size - got))
                        if not chunk:
                            raise ConnectionError("Connection lost during download")
                        f.write(chunk)
                        h.update(chunk)
                        got += len(chunk)
                        prog.update(got)
                if h.hexdigest() != expected:
                    raise RemoteError("SHA256 mismatch - downloaded file is corrupt", "integrity")
                os.replace(part, dest)
            except BaseException:
                try:
                    os.remove(part)
                except OSError:
                    pass
                raise
            prog.update(size, force=True)
            return expected
