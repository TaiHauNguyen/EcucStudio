"""Window of the CAN <-> Ethernet gateway generator (standalone or opened from EcucStudio)."""
from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, ttk

from ..gui.theme import COLORS, init_style
from ..gui.widgets import Tooltip, dialog_header
from . import dbcread, report
from .base import Base
from .config import BusInput, GatewayConfig, Naming, SocketSide
from .planner import make_plan
from .writer import generate

NEW = "<create new>"
AUTO = "<auto>"
NEW_CLUSTER = "<new CAN cluster from DBC>"
TITLE = "CAN-Ethernet Gateway Generator"


def _int_or_none(text):
    text = str(text or "").strip()
    if not text:
        return None
    try:
        return int(text, 0)
    except ValueError:
        return None


class Choice:
    """Combobox whose entries show labels but hold values (paths)."""

    def __init__(self, master, width=48, editable=False, on_change=None):
        self.var = tk.StringVar()
        self.cb = ttk.Combobox(master, textvariable=self.var, width=width,
                               state="normal" if editable else "readonly")
        self.items: list[tuple[str, str]] = []
        if on_change:
            self.cb.bind("<<ComboboxSelected>>", lambda _e: on_change())

    def set_items(self, items, keep=True):
        old = self.get()
        self.items = list(items)
        self.cb["values"] = [label for label, _v in self.items]
        if keep and old is not None:
            self.set(old)

    def set(self, value):
        for label, v in self.items:
            if v == value or (value and v.rsplit("/", 1)[-1] == value):
                self.var.set(label)
                return
        self.var.set(value if self.cb.cget("state") == "normal" else (self.items[0][0] if self.items else ""))

    def get(self):
        text = self.var.get()
        for label, v in self.items:
            if label == text:
                return v
        return text.strip()

    def grid(self, **kw):
        self.cb.grid(**kw)
        return self


class BusDialog(tk.Toplevel):
    """Add / edit one DBC input."""

    def __init__(self, master, bus: BusInput, base: Base | None, dbc_cache: dict):
        super().__init__(master)
        self.title("CAN Bus Input")
        self.transient(master)
        self.resizable(True, False)
        self.bus, self.base, self.cache, self.result = bus, base, dbc_cache, None
        dialog_header(self, "CAN bus input",
                      "Select the DBC and the node that is the gateway ECU in it. Messages the node receives are "
                      "routed CAN -> Ethernet, messages it sends Ethernet -> CAN.")
        f = ttk.Frame(self, padding=10)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)
        r = 0
        ttk.Label(f, text="DBC file:").grid(row=r, column=0, sticky="w", pady=2)
        self.dbc = tk.StringVar(value=bus.dbc)
        ttk.Entry(f, textvariable=self.dbc, width=70).grid(row=r, column=1, sticky="we", pady=2)
        ttk.Button(f, text="Browse…", command=self.browse).grid(row=r, column=2, padx=(4, 0))
        r += 1
        ttk.Label(f, text="Gateway node:").grid(row=r, column=0, sticky="w", pady=2)
        self.node = Choice(f, width=40, on_change=self.node_changed).grid(row=r, column=1, sticky="w", pady=2)
        self.node_info = ttk.Label(f, text="", foreground="#666666")
        self.node_info.grid(row=r, column=1, sticky="e")
        r += 1
        ttk.Label(f, text="CAN channel:").grid(row=r, column=0, sticky="w", pady=2)
        self.channel = Choice(f, width=60, on_change=self.channel_changed).grid(row=r, column=1, sticky="we",
                                                                               pady=2)
        r += 1
        ttk.Label(f, text="Bus name {bus}:").grid(row=r, column=0, sticky="w", pady=2)
        self.busname = tk.StringVar(value=bus.bus)
        ttk.Entry(f, textvariable=self.busname, width=24).grid(row=r, column=1, sticky="w", pady=2)
        r += 1
        bf = ttk.Frame(f)
        bf.grid(row=r, column=1, sticky="w", pady=2)
        ttk.Label(f, text="Baud rate (new):").grid(row=r, column=0, sticky="w")
        self.baud = tk.StringVar(value=bus.baudrate or "")
        self.fdbaud = tk.StringVar(value=bus.fd_baudrate or "")
        ttk.Entry(bf, textvariable=self.baud, width=10).pack(side="left")
        ttk.Label(bf, text="  CAN FD data baud rate:").pack(side="left")
        ttk.Entry(bf, textvariable=self.fdbaud, width=10).pack(side="left")
        ttk.Label(bf, text="  (empty = from DBC)", foreground="#666666").pack(side="left")
        r += 1
        cf = ttk.Frame(f)
        cf.grid(row=r, column=1, sticky="w", pady=(6, 2))
        self.rx = tk.BooleanVar(value=bus.rx)
        self.tx = tk.BooleanVar(value=bus.tx)
        self.nm = tk.BooleanVar(value=bus.include_nm)
        self.diag = tk.BooleanVar(value=bus.include_diag)
        ttk.Checkbutton(cf, text="RX messages: CAN -> ETH", variable=self.rx).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(cf, text="TX messages: ETH -> CAN", variable=self.tx).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(cf, text="include NM", variable=self.nm).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(cf, text="include diagnostic", variable=self.diag).pack(side="left")
        bb = ttk.Frame(self, padding=(10, 0, 10, 10))
        bb.pack(fill="x")
        ttk.Button(bb, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(bb, text="OK", command=self.ok).pack(side="right", padx=6)
        chans = [(NEW_CLUSTER, "")]
        if base is not None:
            chans += [(f"{c.name}   ({c.path})", c.path) for c in base.can_channels()]
        self.channel.set_items(chans, keep=False)
        self.channel.set(bus.channel if not bus.new_channel else "")
        self.load_dbc(select=bus.node)
        self.grab_set()
        self.geometry("+%d+%d" % (master.winfo_rootx() + 80, master.winfo_rooty() + 80))

    def browse(self):
        p = filedialog.askopenfilename(parent=self, title="DBC file",
                                       filetypes=[("CAN database", "*.dbc"), ("All files", "*.*")])
        if p:
            self.dbc.set(os.path.normpath(p))
            self.load_dbc()

    def load_dbc(self, select=None):
        path = self.dbc.get().strip()
        self.db = None
        if not path:
            return
        try:
            key = os.path.abspath(path)
            if key not in self.cache:
                self.cache[key] = dbcread.load(path)
            self.db = self.cache[key]
        except Exception as exc:  # noqa: BLE001 - shown to the user
            messagebox.showerror(TITLE, f"Cannot read {path}:\n{exc}", parent=self)
            return
        nodes = []
        for n in self.db.nodes:
            rx, tx = self.db.node_messages(n)
            nodes.append((f"{n}   (receives {len(rx)}, sends {len(tx)})", n))
        self.node.set_items(nodes, keep=False)
        if select:
            self.node.set(select)
        elif nodes:
            self.node.var.set("")
        if not self.busname.get() and not self.channel.get():
            self.busname.set(self.db.name)
        self.node_changed()

    def node_changed(self):
        if self.db is None or not self.node.get():
            self.node_info.config(text="")
            return
        self.node_info.config(text=f"{self.db.name}{', CAN FD' if self.db.has_fd else ''}")

    def channel_changed(self):
        ch = self.channel.get()
        if ch:
            self.busname.set(ch.rsplit("/", 1)[-1])

    def ok(self):
        if not self.dbc.get().strip() or not self.node.get():
            messagebox.showwarning(TITLE, "Select a DBC file and the gateway node.", parent=self)
            return
        b = self.bus
        b.dbc = os.path.abspath(self.dbc.get().strip())
        b.node = self.node.get()
        b.channel = self.channel.get()
        b.new_channel = not b.channel
        b.bus = self.busname.get().strip()
        b.baudrate = _int_or_none(self.baud.get())
        b.fd_baudrate = _int_or_none(self.fdbaud.get())
        b.rx, b.tx = self.rx.get(), self.tx.get()
        b.include_nm, b.include_diag = self.nm.get(), self.diag.get()
        self.result = b
        self.destroy()


class RouteDialog(tk.Toplevel):
    def __init__(self, master, route):
        super().__init__(master)
        self.title("Route")
        self.transient(master)
        self.result = None
        m = route.message
        dialog_header(self, f"{route.direction}  {m.name}  ({m.id_text})",
                      "Leave the header id empty to use the CAN id (a flag is added automatically when it "
                      "collides). A header id entered here is never changed.")
        f = ttk.Frame(self, padding=10)
        f.pack(fill="both")
        over = route.bus.cfg.messages.get(m.name, {})
        self.enabled = tk.BooleanVar(value=route.enabled)
        ttk.Checkbutton(f, text="Route this message", variable=self.enabled).grid(row=0, column=1, sticky="w")
        ttk.Label(f, text="Header id:").grid(row=1, column=0, sticky="w", pady=3)
        self.hid = tk.StringVar(value=over.get("header_id", ""))
        ttk.Entry(f, textvariable=self.hid, width=14).grid(row=1, column=1, sticky="w")
        ttk.Label(f, text=f"(planned {route.header_text if route.header_id >= 0 else '-'})",
                  foreground="#666666").grid(row=1, column=2, sticky="w")
        ttk.Label(f, text="Ethernet PDU name:").grid(row=2, column=0, sticky="w", pady=3)
        self.eth = tk.StringVar(value=over.get("eth_pdu", ""))
        ttk.Entry(f, textvariable=self.eth, width=50).grid(row=2, column=1, columnspan=2, sticky="we")
        ttk.Label(f, text=f"(planned {route.eth_pdu})", foreground="#666666").grid(row=3, column=1, sticky="w")
        bb = ttk.Frame(self, padding=(10, 0, 10, 10))
        bb.pack(fill="x")
        ttk.Button(bb, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(bb, text="OK", command=self.ok).pack(side="right", padx=6)
        self.grab_set()

    def ok(self):
        h = self.hid.get().strip()
        if h and _int_or_none(h) is None:
            messagebox.showwarning(TITLE, "The header id must be a number (e.g. 0x123).", parent=self)
            return
        self.result = {"enabled": self.enabled.get()}
        if h:
            self.result["header_id"] = h
        if self.eth.get().strip():
            self.result["eth_pdu"] = self.eth.get().strip()
        self.destroy()


class SideFrame(ttk.LabelFrame):
    """Socket settings of one direction."""

    def __init__(self, master, title, app):
        super().__init__(master, text=title, padding=8)
        self.app = app
        self.columnconfigure(1, weight=1)
        rows = (("Local socket:", "local"), ("  new socket name / port:", None), ("Remote socket:", "remote"),
                ("  remote IP / port:", None), ("  remote socket name:", None), ("Socket connection name:", None))
        self.local = Choice(self, width=52, on_change=self.refresh)
        self.remote = Choice(self, width=52, on_change=self.refresh)
        self.local_name, self.local_port = tk.StringVar(), tk.StringVar()
        self.remote_ip, self.remote_port = tk.StringVar(), tk.StringVar()
        self.remote_name, self.conn_name = tk.StringVar(), tk.StringVar()
        for i, (label, _k) in enumerate(rows):
            ttk.Label(self, text=label).grid(row=i, column=0, sticky="w", pady=2)
        self.local.grid(row=0, column=1, sticky="we", pady=2)
        f1 = ttk.Frame(self)
        f1.grid(row=1, column=1, sticky="w")
        self.e_lname = ttk.Entry(f1, textvariable=self.local_name, width=34)
        self.e_lname.pack(side="left")
        self.e_lport = ttk.Entry(f1, textvariable=self.local_port, width=8)
        self.e_lport.pack(side="left", padx=4)
        self.remote.grid(row=2, column=1, sticky="we", pady=2)
        f2 = ttk.Frame(self)
        f2.grid(row=3, column=1, sticky="w")
        self.e_rip = ttk.Combobox(f2, textvariable=self.remote_ip, width=32)
        self.e_rip.pack(side="left")
        self.e_rport = ttk.Entry(f2, textvariable=self.remote_port, width=8)
        self.e_rport.pack(side="left", padx=4)
        self.e_rname = ttk.Entry(self, textvariable=self.remote_name, width=34)
        self.e_rname.grid(row=4, column=1, sticky="w", pady=2)
        ttk.Entry(self, textvariable=self.conn_name, width=34).grid(row=5, column=1, sticky="w", pady=2)
        Tooltip(self.e_rip, "IP address of the other node (an existing endpoint of the channel is reused)")
        Tooltip(self.e_lport, "UDP/TCP port of the gateway ECU")
        Tooltip(self.e_rport, "UDP/TCP port of the other node")

    def fill(self, channel, connector):
        loc, rem = [(NEW, "")], [(NEW, "")]
        ips = []
        if channel is not None:
            for s in channel.sockets:
                label = f"{s.name}   ({s.ip or '?'}:{s.port} {s.protocol})"
                if s.connector:
                    if not connector or s.connector == connector:
                        loc.append((label, s.path))
                else:
                    rem.append((label, s.path))
            own = set(self.app.own_endpoints())
            ips = [f"{e.ip}" for e in channel.endpoints if e.ip and e.path not in own]
        self.local.set_items(loc)
        self.remote.set_items(rem)
        self.e_rip["values"] = sorted(set(ips))
        self.refresh()

    def refresh(self):
        new_local = not self.local.get()
        new_remote = not self.remote.get()
        for w in (self.e_lname, self.e_lport):
            w.configure(state="normal" if new_local else "disabled")
        for w in (self.e_rip, self.e_rport, self.e_rname):
            w.configure(state="normal" if new_remote else "disabled")

    def load(self, s: SocketSide):
        self.local.set(s.local_socket)
        self.remote.set(s.remote_socket)
        self.local_name.set(s.local_name)
        self.local_port.set("" if s.local_port is None else s.local_port)
        self.remote_ip.set(s.remote_ip)
        self.remote_port.set("" if s.remote_port is None else s.remote_port)
        self.remote_name.set(s.remote_name)
        self.conn_name.set(s.connection_name)
        self.refresh()

    def store(self, s: SocketSide):
        s.local_socket = self.local.get()
        s.remote_socket = self.remote.get()
        s.local_name = self.local_name.get().strip()
        s.local_port = _int_or_none(self.local_port.get())
        s.remote_ip = self.remote_ip.get().strip()
        s.remote_port = _int_or_none(self.remote_port.get())
        s.remote_name = self.remote_name.get().strip()
        s.connection_name = self.conn_name.get().strip()


class GatewayWindow:
    def __init__(self, master: tk.Misc, config_path: str | None = None):
        self.win = master
        self.win.title(TITLE)
        self.cfg = GatewayConfig()
        self.cfg_path = None
        self.base: Base | None = None
        self._base_key = None
        self.dbc_cache: dict = {}
        self.plan = None
        self._q = queue.Queue()
        self._busy = False
        self._build()
        self.win.after(100, self._poll)
        if config_path:
            self.open_config(config_path)

    # ------------------------------------------------------------------ layout
    def _build(self):
        w = self.win
        tb = ttk.Frame(w, padding=(6, 4))
        tb.pack(fill="x")
        for text, cmd in (("New", self.new_config), ("Open…", self.open_config_dialog), ("Save", self.save_config),
                          ("Save As…", self.save_config_as)):
            ttk.Button(tb, text=text, command=cmd).pack(side="left", padx=(0, 4))
        ttk.Separator(tb, orient="vertical").pack(side="left", fill="y", padx=6)
        self.b_plan = ttk.Button(tb, text="Analyze", command=self.analyze)
        self.b_plan.pack(side="left", padx=(0, 4))
        self.b_gen = ttk.Button(tb, text="Generate network ARXML", command=self.generate)
        self.b_gen.pack(side="left")
        self.status = ttk.Label(tb, text="Select the base system description and the DBC files.")
        self.status.pack(side="left", padx=12)
        pw = ttk.PanedWindow(w, orient="vertical")
        pw.pack(fill="both", expand=True)
        nb = ttk.Notebook(pw)
        pw.add(nb, weight=0)
        self._tab_input(nb)
        self._tab_eth(nb)
        self._tab_options(nb)
        bottom = ttk.Frame(pw)
        pw.add(bottom, weight=1)
        self._routes(bottom)

    def _tab_input(self, nb):
        f = ttk.Frame(nb, padding=10)
        nb.add(f, text="  Input  ")
        f.columnconfigure(1, weight=1)
        self.v_base, self.v_out = tk.StringVar(), tk.StringVar()
        ttk.Label(f, text="Base system description:").grid(row=0, column=0, sticky="w", pady=2)
        e = ttk.Entry(f, textvariable=self.v_base)
        e.grid(row=0, column=1, sticky="we", pady=2)
        e.bind("<FocusOut>", lambda _e: self.load_base())
        e.bind("<Return>", lambda _e: self.load_base())
        ttk.Button(f, text="Browse…", command=self.browse_base).grid(row=0, column=2, padx=4)
        ttk.Label(f, text="Output file:").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Entry(f, textvariable=self.v_out).grid(row=1, column=1, sticky="we", pady=2)
        ttk.Button(f, text="Browse…", command=self.browse_out).grid(row=1, column=2, padx=4)
        ttk.Label(f, text="Gateway ECU:").grid(row=2, column=0, sticky="w", pady=2)
        self.c_ecu = Choice(f, width=60, on_change=self.ecu_changed).grid(row=2, column=1, sticky="w", pady=2)
        self.base_info = ttk.Label(f, text="", foreground="#666666")
        self.base_info.grid(row=3, column=1, sticky="w")
        lf = ttk.LabelFrame(f, text="CAN buses (DBC)", padding=6)
        lf.grid(row=4, column=0, columnspan=3, sticky="nsew", pady=(8, 0))
        f.rowconfigure(4, weight=1)
        cols = ("dbc", "node", "channel", "bus", "dirs")
        self.t_bus = ttk.Treeview(lf, columns=cols, show="headings", height=4, selectmode="browse")
        for c, text, wd in zip(cols, ("DBC file", "Node", "CAN channel", "Bus name", "Directions"),
                               (300, 120, 320, 110, 170)):
            self.t_bus.heading(c, text=text, anchor="w")
            self.t_bus.column(c, width=wd, anchor="w")
        self.t_bus.pack(side="left", fill="both", expand=True)
        self.t_bus.bind("<Double-1>", lambda _e: self.edit_bus())
        bb = ttk.Frame(lf)
        bb.pack(side="left", fill="y", padx=(6, 0))
        ttk.Button(bb, text="Add DBC…", command=self.add_bus).pack(fill="x")
        ttk.Button(bb, text="Edit…", command=self.edit_bus).pack(fill="x", pady=3)
        ttk.Button(bb, text="Remove", command=self.remove_bus).pack(fill="x")

    def _tab_eth(self, nb):
        f = ttk.Frame(nb, padding=10)
        nb.add(f, text="  Ethernet  ")
        top = ttk.Frame(f)
        top.pack(fill="x")
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="Ethernet channel (VLAN):").grid(row=0, column=0, sticky="w", pady=2)
        self.c_chan = Choice(top, width=70, on_change=self.channel_changed).grid(row=0, column=1, sticky="w")
        ttk.Label(top, text="ECU connector:").grid(row=1, column=0, sticky="w", pady=2)
        self.c_conn = Choice(top, width=70, on_change=self.connector_changed).grid(row=1, column=1, sticky="w")
        ttk.Label(top, text="Local endpoint (ECU IP):").grid(row=2, column=0, sticky="w", pady=2)
        self.c_nep = Choice(top, width=70).grid(row=2, column=1, sticky="w")
        pf = ttk.Frame(top)
        pf.grid(row=3, column=1, sticky="w", pady=2)
        ttk.Label(top, text="Protocol:").grid(row=3, column=0, sticky="w")
        self.v_proto = tk.StringVar(value="UDP")
        ttk.Radiobutton(pf, text="UDP", value="UDP", variable=self.v_proto).pack(side="left")
        ttk.Radiobutton(pf, text="TCP", value="TCP", variable=self.v_proto).pack(side="left", padx=8)
        ttk.Label(pf, text="TCP role:").pack(side="left")
        self.v_role = tk.StringVar(value="CONNECT")
        ttk.Combobox(pf, textvariable=self.v_role, values=("CONNECT", "LISTEN"), width=10,
                     state="readonly").pack(side="left", padx=4)
        ttk.Label(top, text="Header id set:").grid(row=4, column=0, sticky="w", pady=2)
        self.c_idset = Choice(top, width=70, editable=True).grid(row=4, column=1, sticky="w")
        sides = ttk.Frame(f)
        sides.pack(fill="x", pady=(8, 0))
        self.side_tx = SideFrame(sides, "CAN -> ETH  (gateway ECU sends on Ethernet)", self)
        self.side_rx = SideFrame(sides, "ETH -> CAN  (gateway ECU receives from Ethernet)", self)
        self.side_tx.pack(side="left", fill="both", expand=True, padx=(0, 4))
        self.side_rx.pack(side="left", fill="both", expand=True, padx=(4, 0))

    def _tab_options(self, nb):
        f = ttk.Frame(nb, padding=10)
        nb.add(f, text="  Options & Naming  ")
        o = ttk.LabelFrame(f, text="Options", padding=6)
        o.pack(side="left", fill="y")
        self.v_extflag = tk.BooleanVar()
        self.v_sigs = tk.StringVar(value="copy")
        self.v_timing = tk.StringVar(value="event")
        self.v_fibex = tk.BooleanVar(value=True)
        ttk.Checkbutton(o, text="Set bit 31 for extended CAN ids (Can_IdType style)",
                        variable=self.v_extflag).pack(anchor="w")
        ttk.Label(o, text="Header id = CAN id (zero padded to 32 bit). On a collision a flag\n"
                          "is set in bits 29..31 and a warning is shown.", foreground="#666666").pack(anchor="w",
                                                                                                    pady=(0, 6))
        ttk.Label(o, text="Ethernet PDU content:").pack(anchor="w")
        ttk.Radiobutton(o, text="same signal layout as the CAN PDU", value="copy",
                        variable=self.v_sigs).pack(anchor="w", padx=12)
        ttk.Radiobutton(o, text="no signals (opaque PDU)", value="none", variable=self.v_sigs).pack(anchor="w",
                                                                                                  padx=12)
        ttk.Label(o, text="Timing of CAN PDUs sent by the gateway:").pack(anchor="w", pady=(6, 0))
        ttk.Radiobutton(o, text="event (forwarded on reception)", value="event",
                        variable=self.v_timing).pack(anchor="w", padx=12)
        ttk.Radiobutton(o, text="cycle time from the DBC", value="dbc", variable=self.v_timing).pack(anchor="w",
                                                                                                   padx=12)
        ttk.Checkbutton(o, text="Add new elements to the SYSTEM (FIBEX-ELEMENTS)",
                        variable=self.v_fibex).pack(anchor="w", pady=(6, 0))
        n = ttk.LabelFrame(f, text="Short-name patterns", padding=6)
        n.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.v_naming = {}
        fields = [x for x in Naming.__dataclass_fields__]
        half = (len(fields) + 1) // 2
        for i, name in enumerate(fields):
            col = 0 if i < half else 2
            row = i if i < half else i - half
            ttk.Label(n, text=name.replace("_", " ") + ":").grid(row=row, column=col, sticky="w",
                                                                 padx=(8 if col else 0, 4))
            v = tk.StringVar()
            ttk.Entry(n, textvariable=v, width=26).grid(row=row, column=col + 1, sticky="w", pady=1)
            self.v_naming[name] = v
        ttk.Label(n, text="Fields: {bus} {msg} {sig} {ecu} {node} {canid} {frame} {pdu} {signal} {triggering} "
                          "{connector} {eth_pdu}", foreground="#666666").grid(row=half, column=0, columnspan=4,
                                                                             sticky="w", pady=(6, 0))

    def _routes(self, parent):
        top = ttk.Frame(parent)
        top.pack(fill="both", expand=True)
        cols = report.COLUMNS
        self.t_routes = ttk.Treeview(top, columns=cols, show="headings", selectmode="extended")
        widths = (60, 75, 80, 200, 90, 70, 55, 65, 200, 220, 95, 220, 380)
        for c, wd in zip(cols, widths):
            self.t_routes.heading(c, text=c, anchor="w")
            self.t_routes.column(c, width=wd, anchor="w", stretch=c == "Remark")
        ys = ttk.Scrollbar(top, orient="vertical", command=self.t_routes.yview)
        self.t_routes.configure(yscrollcommand=ys.set)
        self.t_routes.pack(side="left", fill="both", expand=True)
        ys.pack(side="left", fill="y")
        self.t_routes.tag_configure("off", foreground="#9e9e9e")
        self.t_routes.tag_configure("flag", foreground=COLORS["warning"])
        self.t_routes.bind("<Double-1>", lambda _e: self.edit_route())
        self.t_routes.bind("<space>", lambda _e: self.toggle_routes())
        self.t_routes.bind("<Button-3>", self.route_menu)
        msg = ttk.Frame(parent)
        msg.pack(fill="x")
        self.t_msg = tk.Text(msg, height=7, wrap="word", font=("Segoe UI", 9), background="#ffffff",
                             relief="flat", borderwidth=1)
        self.t_msg.pack(fill="x", padx=2, pady=2)
        self.t_msg.tag_configure("error", foreground=COLORS["error"])
        self.t_msg.tag_configure("warning", foreground=COLORS["warning"])
        self.t_msg.tag_configure("info", foreground=COLORS["info"])

    # ------------------------------------------------------------------ background work
    def _run(self, label, fn, done):
        if self._busy:
            return
        self._busy = True
        self.status.config(text=label + " …")
        self.win.config(cursor="watch")

        def work():
            try:
                self._q.put((done, fn(), None))
            except Exception as exc:  # noqa: BLE001 - reported in the window
                self._q.put((done, None, (exc, traceback.format_exc())))
        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        try:
            while True:
                done, res, err = self._q.get_nowait()
                self._busy = False
                self.win.config(cursor="")
                if err:
                    self.status.config(text="Failed")
                    self.show_messages(errors=[f"{err[0]}"], extra=err[1].splitlines()[-3:])
                else:
                    done(res)
        except queue.Empty:
            pass
        self.win.after(100, self._poll)

    # ------------------------------------------------------------------ base file
    def browse_base(self):
        p = filedialog.askopenfilename(parent=self.win, title="Base system description",
                                       filetypes=[("AUTOSAR XML", "*.arxml"), ("All files", "*.*")])
        if p:
            self.v_base.set(os.path.normpath(p))
            if not self.v_out.get():
                self.v_out.set(os.path.splitext(p)[0] + "_gateway.arxml")
            self.load_base()

    def browse_out(self):
        p = filedialog.asksaveasfilename(parent=self.win, title="Output file", defaultextension=".arxml",
                                         filetypes=[("AUTOSAR XML", "*.arxml")])
        if p:
            self.v_out.set(os.path.normpath(p))

    def load_base(self, then=None):
        path = self.v_base.get().strip()
        if not path or not os.path.isfile(path):
            return
        key = (os.path.abspath(path), os.path.getmtime(path))
        if key == self._base_key:
            if then:
                then()
            return

        def done(base):
            self.base, self._base_key = base, key
            self.base_info.config(text=f"{base.schema}: {len(base.ecus())} ECU, {len(base.eth_channels())} "
                                       f"Ethernet channel(s), {len(base.can_channels())} CAN channel(s)")
            self.fill_from_base()
            self.status.config(text="Base file loaded.")
            if then:
                then()
        self._run("Loading " + os.path.basename(path), lambda: Base(path), done)

    def fill_from_base(self):
        b = self.base
        ecus = [(AUTO, "")] + [(p.rsplit("/", 1)[-1] + f"   ({p})", p) for p in b.ecus()]
        self.c_ecu.set_items(ecus)
        self.c_ecu.set(self.cfg.ecu)
        self.c_idset.set_items([(AUTO, "")] + [(p.rsplit("/", 1)[-1] + f"   ({p})", p) for p in b.id_sets()])
        self.c_idset.set(self.cfg.ethernet.id_set)
        self.ecu_changed()

    def ecu(self):
        if self.base is None:
            return ""
        e = self.c_ecu.get()
        if e:
            return e
        ecus = self.base.ecus()
        return ecus[0] if len(ecus) == 1 else ""

    def own_endpoints(self):
        b, e = self.base, self.ecu()
        if b is None or not e:
            return []
        from ..arxml import q
        return [p for c in b.ecu_connectors(e)
                for p in b.refs(b.el(c).find(q("NETWORK-ENDPOINT-REFS")), "NETWORK-ENDPOINT-REF")]

    def ecu_changed(self):
        b, ecu = self.base, self.ecu()
        if b is None:
            return
        chans = [c for c in b.eth_channels() if not ecu or any(b.connector_ecu(x) == ecu for x in c.connectors)]
        self._channels = {c.path: c for c in b.eth_channels()}
        self.c_chan.set_items([(AUTO, "")] + [(f"{c.label}   ({c.path})", c.path) for c in chans])
        want = self.cfg.ethernet.channel
        hit = [c.path for c in chans if want in (c.path, c.name) or
               (c.vlan is not None and want.upper() in (f"VLAN{c.vlan}", str(c.vlan)))] if want else []
        self.c_chan.set(hit[0] if hit else "")
        self.channel_changed()

    def channel(self):
        p = self.c_chan.get()
        if p:
            return self._channels.get(p)
        ecu = self.ecu()
        chans = [c for c in self._channels.values()
                 if any(self.base.connector_ecu(x) == ecu for x in c.connectors)] if ecu else []
        return chans[0] if len(chans) == 1 else None

    def channel_changed(self):
        ch, ecu = self.channel(), self.ecu()
        conns = [c for c in (ch.connectors if ch else []) if self.base.connector_ecu(c) == ecu]
        self.c_conn.set_items([(AUTO, "")] + [(c.rsplit("/", 1)[-1], c) for c in conns])
        self.c_conn.set(next((c for c in conns if self.cfg.ethernet.connector in (c, c.rsplit('/', 1)[-1])), ""))
        self.connector_changed()

    def connector(self):
        c = self.c_conn.get()
        if c:
            return c
        ch, ecu = self.channel(), self.ecu()
        conns = [x for x in (ch.connectors if ch else []) if self.base.connector_ecu(x) == ecu]
        return conns[0] if conns else ""

    def connector_changed(self):
        ch = self.channel()
        own = set(self.own_endpoints())
        neps = [(AUTO, "")] + [(f"{e.name}   ({e.ip})", e.path) for e in (ch.endpoints if ch else [])
                               if e.path in own]
        self.c_nep.set_items(neps)
        self.c_nep.set(self.cfg.ethernet.local_endpoint)
        conn = self.connector()
        self.side_tx.fill(ch, conn)
        self.side_rx.fill(ch, conn)
        self.side_tx.load(self.cfg.ethernet.can_to_eth)
        self.side_rx.load(self.cfg.ethernet.eth_to_can)

    # ------------------------------------------------------------------ buses
    def refresh_buses(self):
        self.t_bus.delete(*self.t_bus.get_children())
        for i, b in enumerate(self.cfg.buses):
            dirs = ", ".join(x for x, on in (("CAN->ETH", b.rx), ("ETH->CAN", b.tx)) if on)
            self.t_bus.insert("", "end", iid=str(i), values=(
                os.path.basename(b.dbc), b.node, b.channel or NEW_CLUSTER, b.bus or "(auto)", dirs))

    def add_bus(self):
        d = BusDialog(self.win, BusInput(), self.base, self.dbc_cache)
        self.win.wait_window(d)
        if d.result:
            self.cfg.buses.append(d.result)
            self.refresh_buses()

    def edit_bus(self):
        sel = self.t_bus.selection()
        if not sel:
            return
        d = BusDialog(self.win, self.cfg.buses[int(sel[0])], self.base, self.dbc_cache)
        self.win.wait_window(d)
        self.refresh_buses()

    def remove_bus(self):
        sel = self.t_bus.selection()
        if sel:
            del self.cfg.buses[int(sel[0])]
            self.refresh_buses()

    # ------------------------------------------------------------------ config <-> widgets
    def collect(self) -> GatewayConfig:
        c = self.cfg
        c.base = self.v_base.get().strip()
        c.output = self.v_out.get().strip()
        c.ecu = self.c_ecu.get()
        e = c.ethernet
        e.channel = self.c_chan.get()
        e.connector = self.c_conn.get()
        e.local_endpoint = self.c_nep.get()
        e.protocol = self.v_proto.get()
        e.tcp_role = self.v_role.get()
        e.id_set = self.c_idset.get()
        self.side_tx.store(e.can_to_eth)
        self.side_rx.store(e.eth_to_can)
        c.header.extended_flag = self.v_extflag.get()
        c.options.eth_signals = self.v_sigs.get()
        c.options.can_tx_timing = self.v_timing.get()
        c.options.add_fibex = self.v_fibex.get()
        for k, v in self.v_naming.items():
            setattr(c.naming, k, v.get().strip() or getattr(Naming(), k))
        return c

    def show_config(self):
        c = self.cfg
        self.v_base.set(c.base)
        self.v_out.set(c.output)
        self.v_proto.set(c.ethernet.protocol or "UDP")
        self.v_role.set(c.ethernet.tcp_role or "CONNECT")
        self.v_extflag.set(c.header.extended_flag)
        self.v_sigs.set(c.options.eth_signals)
        self.v_timing.set(c.options.can_tx_timing)
        self.v_fibex.set(c.options.add_fibex)
        for k, v in self.v_naming.items():
            v.set(getattr(c.naming, k))
        self.refresh_buses()
        self._base_key = None
        self.load_base()

    def new_config(self):
        self.cfg, self.cfg_path, self.plan = GatewayConfig(), None, None
        self.show_config()
        self.fill_routes()

    def open_config_dialog(self):
        p = filedialog.askopenfilename(parent=self.win, title="Gateway configuration",
                                       filetypes=[("Gateway configuration", "*.json"), ("All files", "*.*")])
        if p:
            self.open_config(p)

    def open_config(self, path):
        try:
            self.cfg = GatewayConfig.load(path)
        except (OSError, ValueError) as exc:
            messagebox.showerror(TITLE, f"Cannot read {path}:\n{exc}", parent=self.win)
            return
        self.cfg_path = os.path.abspath(path)
        self.win.title(f"{TITLE} - {os.path.basename(path)}")
        self.show_config()

    def save_config(self):
        if not self.cfg_path:
            return self.save_config_as()
        self.collect().save(self.cfg_path)
        self.status.config(text=f"Saved {self.cfg_path}")

    def save_config_as(self):
        p = filedialog.asksaveasfilename(parent=self.win, title="Save gateway configuration",
                                         defaultextension=".json", filetypes=[("Gateway configuration", "*.json")])
        if p:
            self.cfg_path = os.path.abspath(p)
            self.win.title(f"{TITLE} - {os.path.basename(p)}")
            self.save_config()

    # ------------------------------------------------------------------ plan / generate
    def analyze(self, then=None):
        cfg = self.collect()
        if not cfg.base:
            messagebox.showwarning(TITLE, "Select the base system description first.", parent=self.win)
            return

        def run_plan():
            def done(plan):
                self.plan = plan
                self.fill_routes()
                self.show_messages(plan.errors, plan.warnings, plan.infos)
                n = len(plan.enabled_routes)
                self.status.config(text=f"{n} route(s), {len(plan.warnings)} warning(s), {len(plan.errors)} "
                                        f"error(s)")
                if then and plan.ok:
                    then(plan)
            self._run("Analyzing", lambda: make_plan(cfg, self.base, self.dbc_cache), done)
        self.load_base(then=run_plan)

    def generate(self):
        cfg = self.collect()
        if not cfg.output:
            messagebox.showwarning(TITLE, "Select the output file.", parent=self.win)
            return
        if os.path.abspath(cfg.output) == os.path.abspath(cfg.base or "") and not messagebox.askyesno(
                TITLE, "The output file is the base file itself. Overwrite it (a .bak copy is kept)?",
                parent=self.win):
            return

        def write(plan):
            def work():
                res = generate(plan, Base(cfg.base))
                csv_path = os.path.splitext(res.output)[0] + "_gateway_routes.csv"
                report.write_csv(plan, csv_path)
                return res, csv_path

            def done(r):
                res, csv_path = r
                self.show_messages([], res.warnings, [f"Written {res.output}", f"Route table: {csv_path}"])
                self.status.config(text=f"Written {os.path.basename(res.output)}: {len(res.routes)} route(s)")
                messagebox.showinfo(TITLE, f"Written {res.output}\n\n{len(res.routes)} route(s).\n"
                                           f"Import this file into DaVinci Configurator (Input Files) instead of the "
                                           f"base file.", parent=self.win)
            self._run("Generating", work, done)
        self.analyze(then=write)

    def fill_routes(self):
        t = self.t_routes
        t.delete(*t.get_children())
        if not self.plan:
            return
        self._route_by_iid = {}
        for i, (r, row) in enumerate(zip(self.plan.routes, report.route_rows(self.plan))):
            tags = ("off",) if not r.enabled else (("flag",) if r.header_note.startswith("flag") else ())
            t.insert("", "end", iid=str(i), values=row, tags=tags)
            self._route_by_iid[str(i)] = r

    def show_messages(self, errors=(), warnings=(), infos=(), extra=()):
        t = self.t_msg
        t.configure(state="normal")
        t.delete("1.0", "end")
        for tag, items in (("error", errors), ("warning", warnings), ("info", infos), ("info", extra)):
            for m in items:
                t.insert("end", f"[{tag.upper()}] {m}\n", tag)
        t.configure(state="disabled")

    def _selected_routes(self):
        return [self._route_by_iid[i] for i in self.t_routes.selection() if i in getattr(self, "_route_by_iid", {})]

    def edit_route(self):
        routes = self._selected_routes()
        if not routes:
            return
        r = routes[0]
        d = RouteDialog(self.win, r)
        self.win.wait_window(d)
        if d.result is not None:
            r.bus.cfg.messages[r.message.name] = d.result
            self.analyze()

    def toggle_routes(self, value=None):
        routes = self._selected_routes()
        for r in routes:
            over = dict(r.bus.cfg.messages.get(r.message.name, {}))
            over["enabled"] = (not r.enabled) if value is None else value
            r.bus.cfg.messages[r.message.name] = over
        if routes:
            self.analyze()

    def route_menu(self, e):
        iid = self.t_routes.identify_row(e.y)
        if iid and iid not in self.t_routes.selection():
            self.t_routes.selection_set(iid)
        m = tk.Menu(self.win, tearoff=False)
        m.add_command(label="Enable", command=lambda: self.toggle_routes(True))
        m.add_command(label="Disable", command=lambda: self.toggle_routes(False))
        m.add_command(label="Edit…", command=self.edit_route)
        m.add_separator()
        m.add_command(label="Reset overrides of selected", command=self.reset_routes)
        m.tk_popup(e.x_root, e.y_root)

    def reset_routes(self):
        for r in self._selected_routes():
            r.bus.cfg.messages.pop(r.message.name, None)
        self.analyze()


def open_window(master: tk.Misc | None = None, config_path: str | None = None):
    """Open the generator in a Toplevel of *master* (EcucStudio) or in its own root window."""
    if master is None:
        root = tk.Tk()
        init_style(root)
        root.geometry("1400x900")
        root.gateway = GatewayWindow(root, config_path)
        return root
    top = tk.Toplevel(master)
    top.geometry("1400x900")
    top.gateway = GatewayWindow(top, config_path)
    return top


def main(config_path: str | None = None):
    root = open_window(None, config_path)
    root.mainloop()
