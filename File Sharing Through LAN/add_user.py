"""Manage users.json.   Usage:
    python add_user.py <username> <password>      add / update a user
    python add_user.py --delete <username>        remove a user
    python add_user.py --list                     list users
Passwords are stored as salted PBKDF2-SHA256 hashes (never plaintext).
"""
import hashlib
import json
import os
import secrets
import sys

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "users.json")
ITERATIONS = 200_000


def load(path=PATH):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"users": {}}


def hash_password(password, salt=None, iterations=ITERATIONS):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), iterations)
    return {"salt": salt, "hash": digest.hex(), "iterations": iterations}


def add_user(username, password, path=PATH):
    data = load(path)
    data["users"][username] = hash_password(password)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def main(argv):
    if len(argv) == 2 and argv[1] == "--list":
        print("\n".join(load()["users"]) or "(no users)")
    elif len(argv) == 3 and argv[1] == "--delete":
        data = load()
        if data["users"].pop(argv[2], None) is None:
            print("No such user"); return 1
        with open(PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"Deleted {argv[2]}")
    elif len(argv) == 3 and not argv[1].startswith("--"):
        add_user(argv[1], argv[2]); print(f"User '{argv[1]}' saved.")
    else:
        print(__doc__); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
