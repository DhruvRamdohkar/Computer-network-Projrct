# Client-Server File Sharing System Over a LAN

Python 3 only — standard library, zero dependencies. Custom TCP protocol (V3), mandatory SHA256 integrity, multi-threaded server, Tkinter dark-mode client.

## Quick start
1. **Server machine:** `python server.py` — prints the IP clients should use (default port 5000).
2. **Client machines (same network):** `python client.py`, enter the server IP, log in.

Demo accounts: `admin / admin123`, `student / student123`.
Add your own: `python add_user.py <name> <password>` (list with `--list`, remove with `--delete <name>`).

If clients can't connect, allow TCP port 5000 through the server's firewall.
On Linux, Tkinter may need `sudo apt install python3-tk`.

## Files
| File | Purpose |
|---|---|
| `protocol.py` | V3 framing, constants, SHA256 + helpers |
| `server.py` | Multi-threaded server, auth, storage, audit log |
| `client_core.py` | GUI-free client library (progress, ETA, verification) |
| `client.py` | Tkinter GUI |
| `add_user.py` | Manage `users.json` (salted PBKDF2 hashes) |
| `test_suite.py` | 9 automated end-to-end tests |
| `server_files/` | Uploaded files · `logs/server.log` — audit log |

## Protocol V3
Each control message = 4-byte big-endian length + JSON. Raw file bytes follow the message announcing them.
`HELLO{version}` → `LOGIN{user,password}` → `LIST | UPLOAD | DOWNLOAD | RENAME | DELETE | QUIT`.

## Feature map (matches the review slides)
- **Version handshake** – server rejects clients whose version ≠ 3.
- **Mandatory SHA256** – uploads without a checksum are refused; mismatch → upload rejected; downloads verified by the client.
- **Atomic uploads** – data goes to `<name>.<id>.part`, fsync'd, then `os.replace`d. Crashes/disconnects leave no partial files (stale `.part` files are purged at startup). Downloads use the same trick client-side.
- **Zero dependencies** – stdlib only.
- **Real-time progress** – percentage, speed, ETA.
- **File management** – upload (with overwrite prompt), download, rename, delete, refresh.
- **Security** – hashed passwords, 5-try login limit, path-traversal protection, idle timeout.
- **Audit log** – `logs/server.log` (rotating).

## Notes for your viva
Passwords travel unencrypted over TCP (same weakness the slides list for FTP) — TLS is your stated future scope. Your slide claim "outperforming standard SMB" is about integrity checks being mandatory; avoid claiming it's faster.
