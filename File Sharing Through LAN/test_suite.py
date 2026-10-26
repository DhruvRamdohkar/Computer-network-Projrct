"""Automated end-to-end tests.  Run:  python test_suite.py"""
import hashlib, os, shutil, socket, tempfile, threading, time, unittest

import add_user
from client_core import FileClient
from protocol import RemoteError, recv_msg, send_msg, VERSION
from server import FileServer

PORT = 5877


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        users = os.path.join(cls.tmp, "users.json")
        add_user.add_user("alice", "pw123", users)
        cls.store = os.path.join(cls.tmp, "store")
        cls.srv = FileServer("127.0.0.1", PORT, cls.store, users, os.path.join(cls.tmp, "logs"))
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        time.sleep(0.4)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); shutil.rmtree(cls.tmp, ignore_errors=True)

    def client(self):
        c = FileClient(); c.connect("127.0.0.1", PORT, "alice", "pw123"); self.addCleanup(c.close); return c

    def mkfile(self, name, size):
        p = os.path.join(self.tmp, name)
        with open(p, "wb") as f: f.write(os.urandom(size))
        return p

    def test_version_mismatch(self):
        s = socket.create_connection(("127.0.0.1", PORT))
        send_msg(s, {"cmd": "HELLO", "version": 2}); r = recv_msg(s); s.close()
        self.assertFalse(r["ok"]); self.assertEqual(r["code"], "version")

    def test_bad_login(self):
        c = FileClient()
        with self.assertRaises(RemoteError): c.connect("127.0.0.1", PORT, "alice", "wrong")
        with self.assertRaises(RemoteError): c.connect("127.0.0.1", PORT, "nobody", "x")

    def test_commands_require_login(self):
        s = socket.create_connection(("127.0.0.1", PORT))
        send_msg(s, {"cmd": "HELLO", "version": VERSION}); recv_msg(s)
        send_msg(s, {"cmd": "LIST"}); self.assertFalse(recv_msg(s)["ok"]); s.close()

    def test_roundtrip_rename_delete(self):
        c = self.client(); src = self.mkfile("a.bin", 3_000_017)
        events = []
        c.upload(src, progress=lambda *a: events.append(a))
        self.assertEqual(events[-1][0], events[-1][1])           # reached 100%
        self.assertIn("a.bin", [f["name"] for f in c.list_files()])
        with self.assertRaises(RemoteError) as cm: c.upload(src)
        self.assertEqual(cm.exception.code, "exists")
        c.upload(src, overwrite=True)
        out = os.path.join(self.tmp, "a_dl.bin"); c.download("a.bin", out)
        self.assertEqual(open(src, "rb").read(), open(out, "rb").read())
        self.assertFalse(os.path.exists(out + ".part"))
        c.rename("a.bin", "b.bin")
        names = [f["name"] for f in c.list_files()]
        self.assertIn("b.bin", names); self.assertNotIn("a.bin", names)
        c.delete("b.bin")
        with self.assertRaises(RemoteError): c.delete("b.bin")

    def test_empty_file(self):
        c = self.client(); src = self.mkfile("empty.bin", 0)
        c.upload(src); out = os.path.join(self.tmp, "e.out"); c.download("empty.bin", out)
        self.assertEqual(os.path.getsize(out), 0)

    def test_integrity_rejected(self):
        s = socket.create_connection(("127.0.0.1", PORT))
        send_msg(s, {"cmd": "HELLO", "version": VERSION}); recv_msg(s)
        send_msg(s, {"cmd": "LOGIN", "user": "alice", "password": "pw123"}); recv_msg(s)
        data = b"hello world" * 1000
        send_msg(s, {"cmd": "UPLOAD", "name": "bad.txt", "size": len(data), "sha256": "0" * 64})
        self.assertTrue(recv_msg(s)["ok"]); s.sendall(data)
        r = recv_msg(s); s.close()
        self.assertEqual(r["code"], "integrity")
        self.assertNotIn("bad.txt", os.listdir(self.store))
        self.assertFalse([n for n in os.listdir(self.store) if n.endswith(".part")])

    def test_aborted_upload_leaves_no_file(self):
        s = socket.create_connection(("127.0.0.1", PORT))
        send_msg(s, {"cmd": "HELLO", "version": VERSION}); recv_msg(s)
        send_msg(s, {"cmd": "LOGIN", "user": "alice", "password": "pw123"}); recv_msg(s)
        send_msg(s, {"cmd": "UPLOAD", "name": "crash.bin", "size": 1_000_000,
                     "sha256": hashlib.sha256(b"x").hexdigest()})
        recv_msg(s); s.sendall(b"x" * 1000); s.close(); time.sleep(0.5)
        left = os.listdir(self.store)
        self.assertNotIn("crash.bin", left)
        self.assertFalse([n for n in left if n.endswith(".part")])

    def test_path_traversal_blocked(self):
        c = self.client(); src = self.mkfile("t.bin", 10)
        for bad in ("../evil.txt", "..\\evil.txt", "/etc/passwd", ".."):
            with self.assertRaises(RemoteError, msg=bad): c.upload(src, remote_name=bad)
        with self.assertRaises(RemoteError): c.download("../users.json", os.path.join(self.tmp, "x"))

    def test_concurrent_clients(self):
        errs = []
        def worker(i):
            try:
                c = self.client(); p = self.mkfile(f"c{i}.bin", 500_000 + i)
                c.upload(p); o = p + ".dl"; c.download(f"c{i}.bin", o)
                assert open(p, "rb").read() == open(o, "rb").read()
            except Exception as e: errs.append(e)
        ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(errs, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
