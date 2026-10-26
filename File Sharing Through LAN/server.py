"""Multi-threaded LAN file server (protocol V3).

Run:  python server.py [--host 0.0.0.0] [--port 5000]
"""
import argparse
import hashlib
import hmac
import json
import logging
import os
import socket
import sys
import threading
import uuid
from logging.handlers import RotatingFileHandler

from protocol import (CHUNK, DEFAULT_PORT, MAX_FILE, VERSION, ProtocolError,
                      recv_msg, safe_name, send_msg, sha256_file)

BASE = os.path.dirname(os.path.abspath(__file__))
IDLE_TIMEOUT = 300       # seconds of silence before a client is dropped
MAX_LOGIN_TRIES = 5


class FileServer:
    def __init__(self, host="0.0.0.0", port=DEFAULT_PORT, storage_dir=None,
                 users_path=None, log_dir=None):
        self.host, self.port = host, port
        self.storage = storage_dir or os.path.join(BASE, "server_files")
        self.users_path = users_path or os.path.join(BASE, "users.json")
        log_dir = log_dir or os.path.join(BASE, "logs")
        os.makedirs(self.storage, exist_ok=True)
        os.makedirs(log_dir, exist_ok=True)
        self.log = logging.getLogger(f"lanserver.{id(self)}")
        self.log.setLevel(logging.INFO)
        self.log.propagate = False
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        fh = RotatingFileHandler(os.path.join(log_dir, "server.log"),
                                 maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        self.log.addHandler(fh)
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        self.log.addHandler(sh)
        self._sock = None
        self._running = False
        self._lock = threading.Lock()            # guards rename/delete/commit
        self._cleanup_stale_parts()

    # ---------------------------------------------------------- lifecycle
    def _cleanup_stale_parts(self):
        for n in os.listdir(self.storage):
            if n.endswith(".part"):
                try:
                    os.remove(os.path.join(self.storage, n))
                    self.log.info("Removed stale partial upload: %s", n)
                except OSError:
                    pass

    def serve_forever(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, self.port))
        self._sock.listen(32)
        self._running = True
        self.log.info("Server listening on %s:%d (protocol V%d), storage=%s",
                      self.host, self.port, VERSION, self.storage)
        while self._running:
            try:
                conn, addr = self._sock.accept()
            except OSError:
                break
            threading.Thread(target=self._client_thread, args=(conn, addr),
                             daemon=True).start()

    def shutdown(self):
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass

    # ---------------------------------------------------------- users
    def _check_login(self, user, password):
        try:
            with open(self.users_path, "r", encoding="utf-8") as f:
                rec = json.load(f)["users"].get(user)
        except (OSError, ValueError, KeyError):
            return False
        if not rec:
            # burn comparable time so unknown users aren't distinguishable
            hashlib.pbkdf2_hmac("sha256", b"x", b"0" * 16, 1000)
            return False
        digest = hashlib.pbkdf2_hmac("sha256", str(password).encode(),
                                     bytes.fromhex(rec["salt"]), int(rec["iterations"]))
        return hmac.compare_digest(digest.hex(), rec["hash"])

    # ---------------------------------------------------------- per client
    def _client_thread(self, conn, addr):
        who = f"{addr[0]}:{addr[1]}"
        user = None
        conn.settimeout(IDLE_TIMEOUT)
        self.log.info("Connection from %s", who)
        try:
            msg = recv_msg(conn)
            if msg.get("cmd") != "HELLO":
                raise ProtocolError("Expected HELLO")
            if msg.get("version") != VERSION:
                send_msg(conn, {"ok": False, "code": "version",
                                "error": f"Version mismatch: server is V{VERSION}, client is V{msg.get('version')}",
                                "version": VERSION})
                self.log.warning("%s version mismatch (client V%s)", who, msg.get("version"))
                return
            send_msg(conn, {"ok": True, "version": VERSION})

            tries = 0
            while user is None:
                msg = recv_msg(conn)
                if msg.get("cmd") != "LOGIN":
                    send_msg(conn, {"ok": False, "code": "auth", "error": "Login required"})
                    continue
                if self._check_login(msg.get("user", ""), msg.get("password", "")):
                    user = msg["user"]
                    send_msg(conn, {"ok": True, "user": user})
                    self.log.info("%s logged in as '%s'", who, user)
                else:
                    tries += 1
                    self.log.warning("%s failed login for '%s' (%d/%d)", who,
                                     msg.get("user"), tries, MAX_LOGIN_TRIES)
                    send_msg(conn, {"ok": False, "code": "auth", "error": "Invalid username or password"})
                    if tries >= MAX_LOGIN_TRIES:
                        return

            handlers = {"LIST": self._list, "UPLOAD": self._upload,
                        "DOWNLOAD": self._download, "RENAME": self._rename,
                        "DELETE": self._delete}
            while True:
                msg = recv_msg(conn)
                cmd = msg.get("cmd")
                if cmd == "QUIT":
                    send_msg(conn, {"ok": True})
                    break
                fn = handlers.get(cmd)
                if not fn:
                    send_msg(conn, {"ok": False, "error": f"Unknown command: {cmd}"})
                    continue
                try:
                    fn(conn, msg, user, who)
                except ValueError as exc:
                    send_msg(conn, {"ok": False, "code": "invalid", "error": str(exc)})
                except FileNotFoundError:
                    send_msg(conn, {"ok": False, "code": "notfound", "error": "File not found"})
                except FileExistsError as exc:
                    send_msg(conn, {"ok": False, "code": "exists", "error": str(exc)})
        except (ConnectionError, socket.timeout, ProtocolError, OSError) as exc:
            self.log.info("%s disconnected (%s)", who, exc)
        except Exception:
            self.log.exception("Unexpected error with %s", who)
        finally:
            try:
                conn.close()
            except OSError:
                pass
            self.log.info("%s closed (user=%s)", who, user)

    # ---------------------------------------------------------- commands
    def _path(self, name):
        return os.path.join(self.storage, safe_name(name))

    def _list(self, conn, msg, user, who):
        files = []
        for n in sorted(os.listdir(self.storage), key=str.lower):
            p = os.path.join(self.storage, n)
            if n.endswith(".part") or not os.path.isfile(p):
                continue
            st = os.stat(p)
            files.append({"name": n, "size": st.st_size, "mtime": st.st_mtime})
        send_msg(conn, {"ok": True, "files": files})

    def _upload(self, conn, msg, user, who):
        name = safe_name(msg.get("name"))
        size = int(msg.get("size", -1))
        expected = str(msg.get("sha256", "")).lower()
        if size < 0 or size > MAX_FILE:
            raise ValueError("Invalid file size")
        if len(expected) != 64:
            raise ValueError("SHA256 checksum is mandatory")
        dest = os.path.join(self.storage, name)
        if os.path.exists(dest) and not msg.get("overwrite"):
            raise FileExistsError(f"'{name}' already exists")
        send_msg(conn, {"ok": True, "ready": True})

        part = os.path.join(self.storage, f"{name}.{uuid.uuid4().hex[:8]}.part")
        h = hashlib.sha256()
        remaining = size
        try:
            with open(part, "wb") as f:
                while remaining:
                    chunk = conn.recv(min(CHUNK, remaining))
                    if not chunk:
                        raise ConnectionError("Client disconnected mid-upload")
                    f.write(chunk)
                    h.update(chunk)
                    remaining -= len(chunk)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            self._remove(part)
            self.log.warning("%s upload of '%s' aborted; partial file discarded", who, name)
            raise
        if h.hexdigest() != expected:
            self._remove(part)
            self.log.error("%s upload '%s' FAILED integrity check", who, name)
            send_msg(conn, {"ok": False, "code": "integrity",
                            "error": "SHA256 mismatch - upload rejected"})
            return
        with self._lock:
            os.replace(part, dest)               # atomic commit
        self.log.info("%s (%s) uploaded '%s' (%d bytes, sha256 OK)", who, user, name, size)
        send_msg(conn, {"ok": True, "sha256": expected, "size": size})

    def _download(self, conn, msg, user, who):
        path = self._path(msg.get("name"))
        if not os.path.isfile(path):
            raise FileNotFoundError
        digest = sha256_file(path)
        size = os.path.getsize(path)
        send_msg(conn, {"ok": True, "size": size, "sha256": digest})
        sent = 0
        with open(path, "rb") as f:
            while sent < size:
                block = f.read(min(CHUNK, size - sent))
                if not block:
                    raise ConnectionError("File shrank during download")
                conn.sendall(block)
                sent += len(block)
        self.log.info("%s (%s) downloaded '%s' (%d bytes)", who, user, msg["name"], size)

    def _rename(self, conn, msg, user, who):
        old, new = self._path(msg.get("name")), self._path(msg.get("new_name"))
        with self._lock:
            if not os.path.isfile(old):
                raise FileNotFoundError
            if os.path.exists(new):
                raise FileExistsError(f"'{msg['new_name']}' already exists")
            os.rename(old, new)
        self.log.info("%s (%s) renamed '%s' -> '%s'", who, user, msg["name"], msg["new_name"])
        send_msg(conn, {"ok": True})

    def _delete(self, conn, msg, user, who):
        path = self._path(msg.get("name"))
        with self._lock:
            if not os.path.isfile(path):
                raise FileNotFoundError
            os.remove(path)
        self.log.info("%s (%s) deleted '%s'", who, user, msg["name"])
        send_msg(conn, {"ok": True})

    @staticmethod
    def _remove(path):
        try:
            os.remove(path)
        except OSError:
            pass


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main():
    ap = argparse.ArgumentParser(description="LAN File Sharing Server")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args()
    server = FileServer(args.host, args.port)
    print(f"\n  Clients on your LAN should connect to:  {lan_ip()}:{args.port}\n  Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
