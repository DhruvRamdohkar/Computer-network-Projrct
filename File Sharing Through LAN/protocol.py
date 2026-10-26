"""Shared wire protocol (V3) for the LAN File Sharing system.

Framing: every control message is a 4-byte big-endian length followed by a
UTF-8 JSON object.  File contents follow as raw bytes right after the control
message that announces their size.

Handshake:  C -> {"cmd":"HELLO","version":3}   S -> {"ok":true,"version":3}
Login:      C -> {"cmd":"LOGIN","user":..,"password":..}
Commands:   LIST, UPLOAD, DOWNLOAD, RENAME, DELETE, QUIT
"""
import hashlib
import json
import os
import socket
import struct

VERSION = 3
DEFAULT_PORT = 5000
CHUNK = 64 * 1024
MAX_HEADER = 1 << 20          # 1 MiB cap on control messages
MAX_FILE = 8 * 1024 ** 3      # 8 GiB cap per file


class ProtocolError(Exception):
    """Malformed or unexpected data on the wire."""


class RemoteError(Exception):
    """The peer replied with ok=false."""

    def __init__(self, message, code="error"):
        super().__init__(message)
        self.code = code


def send_msg(sock: socket.socket, obj: dict) -> None:
    data = json.dumps(obj).encode("utf-8")
    sock.sendall(struct.pack(">I", len(data)) + data)


def recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Connection closed by peer")
        buf += chunk
    return bytes(buf)


def recv_msg(sock: socket.socket) -> dict:
    (n,) = struct.unpack(">I", recv_exact(sock, 4))
    if n > MAX_HEADER:
        raise ProtocolError("Control message too large")
    try:
        obj = json.loads(recv_exact(sock, n).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("Invalid JSON") from exc
    if not isinstance(obj, dict):
        raise ProtocolError("Control message must be an object")
    return obj


def sha256_file(path: str, progress=None) -> str:
    h = hashlib.sha256()
    done = 0
    with open(path, "rb") as f:
        while True:
            block = f.read(CHUNK)
            if not block:
                break
            h.update(block)
            done += len(block)
            if progress:
                progress(done)
    return h.hexdigest()


def safe_name(name) -> str:
    """Validate a bare file name (no directories, no traversal)."""
    if not isinstance(name, str):
        raise ValueError("Invalid file name")
    name = name.strip()
    if (not name or name in (".", "..") or name != os.path.basename(name)
            or "/" in name or "\\" in name or "\x00" in name
            or name.endswith(".part") or len(name) > 200):
        raise ValueError("Invalid file name")
    return name


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def human_time(sec: float) -> str:
    if sec is None or sec != sec or sec == float("inf"):
        return "--:--"
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
