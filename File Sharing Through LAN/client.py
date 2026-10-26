"""Tkinter dark-mode GUI client for the LAN File Sharing system.

Run:  python client.py
"""
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from client_core import FileClient
from protocol import DEFAULT_PORT, RemoteError, human_size, human_time

BG, PANEL, FG, MUTED = "#12141a", "#1b1e27", "#e6e8ee", "#8b90a0"
ACCENT, ACCENT_H, DANGER, OK = "#4f8cff", "#6aa0ff", "#e5534b", "#3fb950"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("LAN File Share")
        self.geometry("820x560")
        self.minsize(720, 480)
        self.configure(bg=BG)
        self.client = FileClient()
        self.ui_q = queue.Queue()
        self.busy = False
        self._style()
        self.login_frame = self._build_login()
        self.main_frame = self._build_main()
        self.login_frame.pack(fill="both", expand=True)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(50, self._pump)

    # ------------------------------------------------------------- styling
    def _style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=FG, fieldbackground=PANEL,
                    bordercolor=PANEL, lightcolor=PANEL, darkcolor=PANEL, font=("Segoe UI", 10))
        s.configure("TFrame", background=BG)
        s.configure("Card.TFrame", background=PANEL)
        s.configure("TLabel", background=BG, foreground=FG)
        s.configure("Card.TLabel", background=PANEL)
        s.configure("Muted.TLabel", foreground=MUTED)
        s.configure("Title.TLabel", font=("Segoe UI", 18, "bold"), background=PANEL)
        s.configure("TEntry", fieldbackground=BG, foreground=FG, insertcolor=FG, padding=6)
        s.configure("TButton", background=PANEL, foreground=FG, padding=(12, 7), borderwidth=0)
        s.map("TButton", background=[("active", "#272b38"), ("disabled", PANEL)],
              foreground=[("disabled", MUTED)])
        s.configure("Accent.TButton", background=ACCENT, foreground="white")
        s.map("Accent.TButton", background=[("active", ACCENT_H), ("disabled", "#2b3a58")])
        s.configure("Danger.TButton", foreground=DANGER)
        s.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=FG,
                    rowheight=28, borderwidth=0)
        s.map("Treeview", background=[("selected", "#2c3e66")], foreground=[("selected", "white")])
        s.configure("Treeview.Heading", background="#222633", foreground=MUTED,
                    relief="flat", padding=6, font=("Segoe UI", 9, "bold"))
        s.map("Treeview.Heading", background=[("active", "#222633")])
        s.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=PANEL,
                    borderwidth=0, thickness=14)

    # ------------------------------------------------------------- login
    def _build_login(self):
        outer = ttk.Frame(self)
        card = ttk.Frame(outer, style="Card.TFrame", padding=30)
        card.place(relx=0.5, rely=0.5, anchor="center")
        ttk.Label(card, text="LAN File Share", style="Title.TLabel").grid(columnspan=2, pady=(0, 4))
        ttk.Label(card, text="Sign in to a server on your network", style="Card.TLabel",
                  foreground=MUTED).grid(columnspan=2, pady=(0, 18))
        self.v_host = tk.StringVar(value="127.0.0.1")
        self.v_port = tk.StringVar(value=str(DEFAULT_PORT))
        self.v_user = tk.StringVar()
        self.v_pass = tk.StringVar()
        fields = [("Server IP", self.v_host, ""), ("Port", self.v_port, ""),
                  ("Username", self.v_user, ""), ("Password", self.v_pass, "•")]
        for i, (label, var, mask) in enumerate(fields, start=2):
            ttk.Label(card, text=label, style="Card.TLabel").grid(row=i, column=0, sticky="w", pady=5, padx=(0, 14))
            e = ttk.Entry(card, textvariable=var, show=mask, width=28)
            e.grid(row=i, column=1, pady=5)
            e.bind("<Return>", lambda _e: self._login())
        self.login_btn = ttk.Button(card, text="Connect", style="Accent.TButton", command=self._login)
        self.login_btn.grid(columnspan=2, sticky="ew", pady=(18, 6))
        self.login_msg = ttk.Label(card, text="", style="Card.TLabel", foreground=DANGER, wraplength=300)
        self.login_msg.grid(columnspan=2)
        return outer

    def _login(self):
        if self.busy:
            return
        host, port = self.v_host.get().strip(), self.v_port.get().strip()
        user, pw = self.v_user.get().strip(), self.v_pass.get()
        if not (host and port.isdigit() and user and pw):
            self.login_msg.config(text="Fill in all fields (port must be a number).")
            return
        self.login_msg.config(text="Connecting…", foreground=MUTED)
        self.login_btn.state(["disabled"])

        def work():
            try:
                self.client.connect(host, int(port), user, pw)
                self.ui_q.put(lambda: self._show_main(host, port))
            except RemoteError as e:
                self.ui_q.put(lambda e=e: self._login_failed(str(e)))
            except Exception as e:
                self.ui_q.put(lambda e=e: self._login_failed(f"Could not connect: {e}"))
        threading.Thread(target=work, daemon=True).start()

    def _login_failed(self, msg):
        self.login_btn.state(["!disabled"])
        self.login_msg.config(text=msg, foreground=DANGER)

    # ------------------------------------------------------------- main
    def _build_main(self):
        f = ttk.Frame(self, padding=14)
        top = ttk.Frame(f)
        top.pack(fill="x")
        self.hdr = ttk.Label(top, text="", font=("Segoe UI", 13, "bold"))
        self.hdr.pack(side="left")
        ttk.Button(top, text="Disconnect", command=self._logout).pack(side="right")

        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(12, 8))
        self.buttons = []
        for text, cmd, style in [("⬆  Upload", self._upload, "Accent.TButton"),
                                 ("⬇  Download", self._download, "TButton"),
                                 ("✎  Rename", self._rename, "TButton"),
                                 ("🗑  Delete", self._delete, "Danger.TButton"),
                                 ("⟳  Refresh", self.refresh, "TButton")]:
            b = ttk.Button(bar, text=text, command=cmd, style=style)
            b.pack(side="left", padx=(0, 8))
            self.buttons.append(b)

        wrap = ttk.Frame(f, style="Card.TFrame")
        wrap.pack(fill="both", expand=True)
        cols = ("name", "size", "modified")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="browse")
        for c, t, w, a in [("name", "Name", 400, "w"), ("size", "Size", 110, "e"), ("modified", "Modified", 170, "w")]:
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor=a, stretch=(c == "name"))
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda _e: self._download())

        prog = ttk.Frame(f)
        prog.pack(fill="x", pady=(12, 0))
        self.pbar = ttk.Progressbar(prog, maximum=100)
        self.pbar.pack(fill="x")
        self.p_label = ttk.Label(prog, text="Idle", style="Muted.TLabel")
        self.p_label.pack(anchor="w", pady=(4, 0))
        self.status = ttk.Label(f, text="", style="Muted.TLabel")
        self.status.pack(anchor="w", pady=(2, 0))
        return f

    def _show_main(self, host, port):
        self.login_frame.pack_forget()
        self.v_pass.set("")
        self.login_btn.state(["!disabled"])
        self.login_msg.config(text="")
        self.hdr.config(text=f"{self.client.user}  @  {host}:{port}")
        self.main_frame.pack(fill="both", expand=True)
        self.refresh()

    def _logout(self):
        if self.busy:
            messagebox.showinfo("Busy", "Wait for the current transfer to finish.")
            return
        self.client.close()
        self.main_frame.pack_forget()
        self.login_frame.pack(fill="both", expand=True)

    # ------------------------------------------------------------- helpers
    def _set_busy(self, busy):
        self.busy = busy
        for b in self.buttons:
            b.state(["disabled"] if busy else ["!disabled"])

    def _selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Select a file", "Please select a file in the list first.")
            return None
        return self.tree.item(sel[0], "values")[0]

    def _run(self, task, on_done=None, label="Working…", on_error=None):
        """Run task() in a worker thread; marshal results to the UI thread."""
        if self.busy:
            return
        self._set_busy(True)
        self.status.config(text=label, foreground=MUTED)

        def work():
            try:
                res = task()
                self.ui_q.put(lambda: self._finish(on_done, res, None, on_error))
            except Exception as e:
                self.ui_q.put(lambda e=e: self._finish(on_done, None, e, on_error))
        threading.Thread(target=work, daemon=True).start()

    def _finish(self, on_done, res, err, on_error=None):
        self._set_busy(False)
        if err is not None:
            if not (on_error and on_error(err)):
                self._handle_error(err)
            return
        if on_done:
            on_done(res)

    def _handle_error(self, err):
        if isinstance(err, (ConnectionError, OSError)) and not isinstance(err, FileNotFoundError):
            self.client.close()
            self.main_frame.pack_forget()
            self.login_frame.pack(fill="both", expand=True)
            self.login_msg.config(text=f"Connection lost: {err}", foreground=DANGER)
            return
        self.status.config(text=f"Error: {err}", foreground=DANGER)
        self.p_label.config(text="Failed")
        messagebox.showerror("Error", str(err))

    def _ok(self, text):
        self.status.config(text=text, foreground=OK)

    def _progress_cb(self, done, total, speed, eta):
        # called from worker thread -> queue to UI
        pct = 100 * done / total if total else 100
        txt = f"{pct:.0f}%   {human_size(done)} / {human_size(total)}   {human_size(speed)}/s   ETA {human_time(eta)}"
        self.ui_q.put(lambda: (self.pbar.config(value=pct), self.p_label.config(text=txt)))

    def _pump(self):
        try:
            while True:
                self.ui_q.get_nowait()()
        except queue.Empty:
            pass
        self.after(50, self._pump)

    # ------------------------------------------------------------- actions
    def refresh(self):
        def done(files):
            self.tree.delete(*self.tree.get_children())
            for f in files:
                m = time.strftime("%Y-%m-%d %H:%M", time.localtime(f["mtime"]))
                self.tree.insert("", "end", values=(f["name"], human_size(f["size"]), m))
            self._ok(f"{len(files)} file(s) on server")
        self._run(self.client.list_files, done, "Refreshing…")

    def _upload(self, path=None, overwrite=False):
        path = path or filedialog.askopenfilename(title="Choose a file to upload")
        if not path:
            return
        self.pbar.config(value=0)
        self.p_label.config(text="Hashing & uploading…")

        def task():
            return self.client.upload(path, overwrite=overwrite, progress=self._progress_cb)

        def done(_):
            self._ok(f"Uploaded {os.path.basename(path)} (SHA256 verified)")
            self.refresh()

        def on_error(err):
            if isinstance(err, RemoteError) and err.code == "exists":
                if messagebox.askyesno("File exists", f"'{os.path.basename(path)}' already exists.\nOverwrite it?"):
                    self._upload(path, overwrite=True)
                else:
                    self.p_label.config(text="Cancelled")
                return True
            return False

        self._run(task, done, f"Uploading {os.path.basename(path)}…", on_error)

    def _download(self):
        name = self._selected()
        if not name:
            return
        dest = filedialog.asksaveasfilename(title="Save as", initialfile=name)
        if not dest:
            return
        self.pbar.config(value=0)
        self.p_label.config(text="Downloading…")
        self._run(lambda: self.client.download(name, dest, progress=self._progress_cb),
                  lambda _: self._ok(f"Downloaded {name} (SHA256 verified)"),
                  f"Downloading {name}…")

    def _rename(self):
        name = self._selected()
        if not name:
            return
        new = simpledialog.askstring("Rename", f"New name for '{name}':", initialvalue=name, parent=self)
        if not new or new == name:
            return
        self._run(lambda: self.client.rename(name, new),
                  lambda _: (self._ok(f"Renamed to {new}"), self.refresh()), "Renaming…")

    def _delete(self):
        name = self._selected()
        if not name:
            return
        if not messagebox.askyesno("Delete", f"Permanently delete '{name}' from the server?"):
            return
        self._run(lambda: self.client.delete(name),
                  lambda _: (self._ok(f"Deleted {name}"), self.refresh()), "Deleting…")

    def _on_close(self):
        self.client.close()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
