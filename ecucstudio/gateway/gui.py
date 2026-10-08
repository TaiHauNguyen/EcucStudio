"""Window of the CAN gateway generator: CAN <-> Ethernet and CAN -> CAN (standalone or opened from EcucStudio)."""
from __future__ import annotations

import copy
import os
import queue
import threading
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, ttk

from ..gui.theme import COLORS, init_style
from ..gui.widgets import Tooltip, dialog_header
from . import dbcread, dvproject, paths, report, start
from .base import DEFAULT_SCHEMA, SCHEMAS, Base, new_document
from .config import BusInput, EthPeer, GatewayConfig, Naming, PduCollection, SocketSide
from .planner import CAN_TO_ETH, ETH_TO_CAN, CanRoute, Route, SignalRoute, load_base, make_plan, new_ecu_name
from .regen import config_from_file
from .suggest import apply as apply_suggestions
from .suggest import suggest as suggest_settings
from .writer import generate

NEW = "<create new>"
AUTO = "<auto>"
NEW_CLUSTER = "<new CAN cluster from DBC>"
NEW_CHANNEL = "<create new channel (VLAN)>"
NEW_VALUE = "*new*"
TITLE = "CAN Gateway Generator"


def _float_or(text, default):
    try:
        return float(str(text).strip().replace(",", "."))
    except ValueError:
        return default


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

    def __init__(self, master, bus: BusInput, base: Base | None, dbc_cache: dict, project_ecu: str = "",
                 target_node: str = "", networks=()):
        super().__init__(master)
        self.title("CAN Bus Input")
        self.target_node = target_node      # gateway node of the ECU being generated (from the other buses)
        self.transient(master)
        self.resizable(True, False)
        self.bus, self.base, self.cache, self.result = bus, base, dbc_cache, None
        self.project_ecu = project_ecu
        if project_ecu:
            text = ("Select a CAN channel of the DaVinci project: the messages the ECU "
                    f"{project_ecu.rsplit('/', 1)[-1]} receives are routed CAN -> Ethernet, the ones it sends "
                    "Ethernet -> CAN. A DBC file is only needed for a bus that is not in the project.")
        else:
            text = ("Select the DBC and the node that is the gateway ECU in it. Messages the node receives are "
                    "routed CAN -> Ethernet, messages it sends Ethernet -> CAN.")
        dialog_header(self, "CAN bus input", text)
        f = ttk.Frame(self, padding=10)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)
        r = 0
        ttk.Label(f, text="DBC file (optional):" if project_ecu else "DBC file:").grid(row=r, column=0, sticky="w",
                                                                                      pady=2)
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
        self.e_busname = ttk.Entry(f, textvariable=self.busname, width=24)
        self.e_busname.grid(row=r, column=1, sticky="w", pady=2)
        r += 1
        ttk.Label(f, text="Routing table network:").grid(row=r, column=0, sticky="w", pady=2)
        self.network = tk.StringVar(value=bus.table_network)
        self.networks = list(networks)
        c_net = ttk.Combobox(f, textvariable=self.network, values=[""] + self.networks, width=24)
        c_net.grid(row=r, column=1, sticky="w", pady=2)
        self.network_info = ttk.Label(f, text="", foreground="#666666")
        self.network_info.grid(row=r, column=1, sticky="w", padx=(200, 0))
        Tooltip(c_net, "Network column (S / D) of the routing table that is this bus. Empty = the column named like "
                       "the bus, the DBName or the DBC file (also one word of the file name, e.g. BusA in "
                       "Vehicle_BusA_v3.dbc)")
        r += 1
        bf = ttk.Frame(f)
        bf.grid(row=r, column=1, sticky="w", pady=2)
        ttk.Label(f, text="Baud rate (new):").grid(row=r, column=0, sticky="w")
        self.baud = tk.StringVar(value=bus.baudrate or "")
        self.fdbaud = tk.StringVar(value=bus.fd_baudrate or "")
        self.e_baud = ttk.Entry(bf, textvariable=self.baud, width=10)
        self.e_baud.pack(side="left")
        ttk.Label(bf, text="  CAN FD data baud rate:").pack(side="left")
        self.e_fdbaud = ttk.Entry(bf, textvariable=self.fdbaud, width=10)
        self.e_fdbaud.pack(side="left")
        ttk.Label(bf, text="  (empty = from DBC)", foreground="#666666").pack(side="left")
        r += 1
        cf = ttk.Frame(f)
        cf.grid(row=r, column=1, sticky="w", pady=(6, 2))
        self.rx = tk.BooleanVar(value=bus.rx)
        self.tx = tk.BooleanVar(value=bus.tx)
        self.nm = tk.BooleanVar(value=bus.include_nm)
        self.diag = tk.BooleanVar(value=bus.include_diag)
        self.cb_rx = ttk.Checkbutton(cf, text="RX messages: CAN -> ETH", variable=self.rx)
        self.cb_rx.pack(side="left", padx=(0, 12))
        self.cb_tx = ttk.Checkbutton(cf, text="TX messages: ETH -> CAN", variable=self.tx)
        self.cb_tx.pack(side="left", padx=(0, 12))
        ttk.Checkbutton(cf, text="include NM", variable=self.nm).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(cf, text="include diagnostic", variable=self.diag).pack(side="left")
        # bus of another ECU: explained here, its CAN channel settings are not used
        self.remote_info = ttk.Label(self, text="", foreground=COLORS["info"], wraplength=680, justify="left",
                                     padding=(10, 0, 10, 8))
        self.remote_info.pack(fill="x")
        bb = ttk.Frame(self, padding=(10, 0, 10, 10))
        bb.pack(fill="x")
        ttk.Button(bb, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(bb, text="OK", command=self.ok).pack(side="right", padx=6)
        chans = [(NEW_CLUSTER, "")]
        if base is not None and project_ecu:
            counts = {ch: (rx, tx) for ch, rx, tx in dvproject.ecu_can_channels(base, project_ecu)}
            chans = [(f"{c.cluster_name}   ({c.path})   - ECU receives {counts[c.path][0]}, sends "
                      f"{counts[c.path][1]}", c.path) for c in base.can_channels() if c.path in counts] + chans
        elif base is not None:
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
        if self.networks:
            from .planner import _match_network
            auto = _match_network(self.networks, (self.busname.get().strip(), self.db.name,
                                                  os.path.splitext(os.path.basename(path))[0]))
            self.network_info.config(text=f"empty = {auto}" if auto else "empty = no column has this name")
        self.node_changed()

    def is_remote(self) -> bool:
        """The chosen node is another ECU and the DBC has no node of the ECU being generated: a bus of that ECU."""
        node, t = self.node.get(), self.target_node
        return bool(self.db is not None and node and t and node != t and t not in self.db.nodes)

    def node_changed(self):
        if self.db is None or not self.node.get():
            self.node_info.config(text="")
            self.remote_info.config(text="")
            return
        self.node_info.config(text=f"{self.db.name}{', CAN FD' if self.db.has_fd else ''}")
        remote = self.is_remote()
        node = self.node.get()
        self.remote_info.config(text=(
            f"{self.db.name} is a bus of {node}, not of {self.target_node} ({self.target_node} is not in this DBC). "
            f"Messages between the buses of {self.target_node} and this bus go over Ethernet: CAN -> ETH to {node} "
            f"and ETH -> CAN from {node}; no CAN -> CAN and no CAN channel of {self.target_node} for it. The tool "
            f"asks for the IP address and ports of {node}.") if remote else "")
        for w in (self.channel.cb, self.e_busname, self.e_baud, self.e_fdbaud):
            w.configure(state="disabled" if remote else ("readonly" if w is self.channel.cb else "normal"))
        for w in (self.cb_rx, self.cb_tx):
            w.state(["disabled"] if remote else ["!disabled"])

    def channel_changed(self):
        ch = self.channel.get()
        if ch and self.base is not None and self.base.el(ch) is not None:
            c = next((x for x in self.base.can_channels() if x.path == ch), None)
            generic = c is not None and c.name.upper() in ("CHNL", "CHANNEL", "CH")
            self.busname.set(c.cluster_name if generic else ch.rsplit("/", 1)[-1])

    def ok(self):
        if self.is_remote():
            b = self.bus
            b.dbc, b.node = os.path.abspath(self.dbc.get().strip()), self.node.get()
            b.remote_ecu, b.channel, b.new_channel, b.bus = self.node.get(), "", False, ""
            b.include_nm, b.include_diag = self.nm.get(), self.diag.get()
            b.table_network = self.network.get().strip()
            self.result = b
            self.destroy()
            return
        self.bus.remote_ecu = ""
        project_only = self.project_ecu and not self.dbc.get().strip()
        if project_only and not self.channel.get():
            messagebox.showwarning(TITLE, "Select a CAN channel of the project.", parent=self)
            return
        if not project_only and (not self.dbc.get().strip() or not self.node.get()):
            messagebox.showwarning(TITLE, "Select a DBC file and the gateway node.", parent=self)
            return
        b = self.bus
        b.dbc = "" if project_only else os.path.abspath(self.dbc.get().strip())
        b.node = "" if project_only else self.node.get()
        b.channel = self.channel.get()
        b.new_channel = not b.channel
        b.bus = self.busname.get().strip()
        b.baudrate = _int_or_none(self.baud.get())
        b.fd_baudrate = _int_or_none(self.fdbaud.get())
        b.rx, b.tx = self.rx.get(), self.tx.get()
        b.include_nm, b.include_diag = self.nm.get(), self.diag.get()
        b.table_network = self.network.get().strip()
        self.result = b
        self.destroy()


class RouteDialog(tk.Toplevel):
    def __init__(self, master, route, peers=()):
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
        # Ethernet peers (only when more than one node is configured)
        self.peers, self.v_peers, self.v_peer = list(peers), {}, tk.StringVar()
        if len(self.peers) > 1:
            pf = ttk.Frame(f)
            pf.grid(row=4, column=1, columnspan=2, sticky="w", pady=(4, 0))
            current = route.peers or self.peers[:1]
            if route.direction == CAN_TO_ETH:
                ttk.Label(f, text="Send to:").grid(row=4, column=0, sticky="w", pady=(4, 0))
                for name in self.peers:
                    v = self.v_peers[name] = tk.BooleanVar(value=name in current)
                    ttk.Checkbutton(pf, text=name, variable=v).pack(side="left", padx=(0, 8))
            else:
                ttk.Label(f, text="Receive from:").grid(row=4, column=0, sticky="w", pady=(4, 0))
                self.v_peer.set(current[0])
                ttk.Combobox(pf, textvariable=self.v_peer, values=self.peers, width=24,
                             state="readonly").pack(side="left")
        self.direction = route.direction
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
        if len(self.peers) > 1:
            if self.direction == CAN_TO_ETH:
                chosen = [n for n in self.peers if self.v_peers[n].get()]
                if not chosen:
                    messagebox.showwarning(TITLE, "Select at least one Ethernet peer.", parent=self)
                    self.result = None
                    return
                if chosen != self.peers[:1]:
                    self.result["eth_peers"] = chosen
            elif self.v_peer.get() and self.v_peer.get() != self.peers[0]:
                self.result["eth_peer"] = self.v_peer.get()
        self.destroy()


class PeerPickDialog(tk.Toplevel):
    """Ethernet peers of several routes: destinations (CAN -> ETH) or the source (ETH -> CAN)."""

    def __init__(self, master, peers, multi: bool, current=()):
        super().__init__(master)
        self.title("Ethernet peers")
        self.transient(master)
        self.result, self.peers, self.multi = None, list(peers), multi
        dialog_header(self, "Send the selected messages to" if multi else "Receive the selected messages from",
                      "CAN -> ETH: one PDU can go to several nodes (one header id for all of them)." if multi else
                      "ETH -> CAN: a CAN PDU has one Ethernet source.")
        f = ttk.Frame(self, padding=10)
        f.pack(fill="both")
        self.vars, self.one = {}, tk.StringVar(value=(list(current) or self.peers)[0])
        for name in self.peers:
            if multi:
                v = self.vars[name] = tk.BooleanVar(value=name in current)
                ttk.Checkbutton(f, text=name, variable=v).pack(anchor="w")
            else:
                ttk.Radiobutton(f, text=name, value=name, variable=self.one).pack(anchor="w")
        bb = ttk.Frame(self, padding=(10, 0, 10, 10))
        bb.pack(fill="x")
        ttk.Button(bb, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(bb, text="OK", command=self.ok).pack(side="right", padx=6)
        self.grab_set()

    def ok(self):
        chosen = [n for n in self.peers if self.vars[n].get()] if self.multi else [self.one.get()]
        if not chosen:
            messagebox.showwarning(TITLE, "Select at least one Ethernet peer.", parent=self)
            return
        self.result = chosen
        self.destroy()


class NodesDialog(tk.Toplevel):
    """Ethernet nodes of the vehicle network (name, MAC, IPv4, port base): default values the generator fills into
    empty fields. Kept in the user settings, not in project files."""

    COLS = ("Name", "MAC address", "IPv4 address", "Port base")

    def __init__(self, master):
        from . import nodes as nodemod
        super().__init__(master)
        self.title("Ethernet nodes")
        self.transient(master)
        self.result = None
        self.nodemod = nodemod
        dialog_header(self, "Ethernet nodes (default values)",
                      "MAC address, IPv4 address and port base of every Ethernet node of the network. Empty fields "
                      "are filled from here by node name: the IP / MAC / port of the ECU, the address and port of "
                      "the default node and of every peer. One socket per node: the node sends and receives on its "
                      "port base; a socket per direction: port base and port base + 1. Saved in the user settings.")
        f = ttk.Frame(self, padding=10)
        f.pack(fill="both", expand=True)
        self.t = ttk.Treeview(f, columns=self.COLS, show="headings", height=8, selectmode="browse")
        for c, w in zip(self.COLS, (140, 160, 140, 90)):
            self.t.heading(c, text=c, anchor="w")
            self.t.column(c, width=w, anchor="w")
        self.t.pack(fill="both", expand=True)
        self.t.bind("<<TreeviewSelect>>", lambda _e: self._pick())
        ef = ttk.Frame(f)
        ef.pack(fill="x", pady=(6, 0))
        self.v = [tk.StringVar() for _ in self.COLS]
        for v, w in zip(self.v, (18, 20, 16, 8)):
            ttk.Entry(ef, textvariable=v, width=w).pack(side="left", padx=(0, 4))
        ttk.Button(ef, text="Add / Update", command=self._put).pack(side="left", padx=(6, 0))
        ttk.Button(ef, text="Remove", command=self._remove).pack(side="left", padx=4)
        for n in nodemod.load():
            self.t.insert("", "end", values=(n.name, n.mac, n.ip, "" if n.port is None else n.port))
        bb = ttk.Frame(self, padding=(10, 0, 10, 10))
        bb.pack(fill="x")
        ttk.Button(bb, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(bb, text="Save", command=self.ok).pack(side="right", padx=6)
        self.grab_set()

    def _pick(self):
        sel = self.t.selection()
        if sel:
            for v, x in zip(self.v, self.t.item(sel[0], "values")):
                v.set(x)

    def _put(self):
        import re
        from .planner import _valid_ip
        name, mac, ip, port = (v.get().strip() for v in self.v)
        if not name:
            messagebox.showwarning(TITLE, "Enter the node name.", parent=self)
            return
        if mac and not re.fullmatch(r"([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}", mac):
            messagebox.showwarning(TITLE, f"'{mac}' is not a MAC address (02:00:00:00:00:01).", parent=self)
            return
        if ip and not _valid_ip(ip):
            messagebox.showwarning(TITLE, f"'{ip}' is not an IPv4 address.", parent=self)
            return
        if port and not (port.isdigit() and 0 < int(port) < 65535):
            messagebox.showwarning(TITLE, f"'{port}' is not a port.", parent=self)
            return
        row = next((i for i in self.t.get_children() if self.t.item(i, "values")[0].lower() == name.lower()), None)
        if row is None:
            self.t.insert("", "end", values=(name, mac, ip, port))
        else:
            self.t.item(row, values=(name, mac, ip, port))

    def _remove(self):
        for i in self.t.selection():
            self.t.delete(i)

    def ok(self):
        out = []
        for i in self.t.get_children():
            name, mac, ip, port = (str(x) for x in self.t.item(i, "values"))
            out.append(self.nodemod.EthNode(name, mac, ip, int(port) if port.isdigit() else None))
        try:
            self.nodemod.save(out)
        except OSError as exc:
            messagebox.showerror(TITLE, f"Cannot save the settings:\n{exc}", parent=self)
            return
        self.result = out
        self.destroy()


def set_enabled(widget, enabled: bool):
    """Enable / disable a frame and everything in it."""
    for w in widget.winfo_children():
        set_enabled(w, enabled)
    try:
        widget.state(["!disabled"] if enabled else ["disabled"])
    except (AttributeError, tk.TclError):
        try:
            widget.configure(state="normal" if enabled else "disabled")
        except tk.TclError:
            pass


class FanoutDialog(tk.Toplevel):
    """ETH -> CAN routes of one CAN id on several buses: compare their signal layouts, then forward them from one
    Ethernet PDU (1:N, also when the layout differs), give each bus its own PDU, or let the tool decide."""

    def __init__(self, master, routes, current=None):
        super().__init__(master)
        self.title("ETH -> CAN 1:N")
        self.transient(master)
        self.result, self.done = None, False
        m0 = routes[0].message
        dialog_header(self, f"CAN id {m0.id_text} on {len(routes)} buses",
                      "One Ethernet PDU forwarded to every bus (one header id), or an own Ethernet PDU per bus. PduR "
                      "forwards the whole PDU unchanged: with another signal layout the receivers of the other bus "
                      "read the bytes with their own layout.")
        f = ttk.Frame(self, padding=10)
        f.pack(fill="both", expand=True)
        text = tk.Text(f, height=min(24, 4 + sum(len(r.message.signals) + 2 for r in routes)), width=96,
                       font=("Consolas", 9), wrap="none")
        text.pack(fill="both", expand=True)
        text.tag_configure("diff", foreground="#b03a00")
        text.tag_configure("head", font=("Consolas", 9, "bold"))
        layouts = [{s.name: (s.start, s.length, s.little_endian) for s in r.message.signals} for r in routes]
        common = set.intersection(*(set(x.values()) for x in layouts)) if layouts else set()
        for r, lay in zip(routes, layouts):
            text.insert("end", f"{r.bus.name} / {r.message.name}  (length {r.length}, header "
                               f"{r.header_text if r.header_id >= 0 else '-'})\n", "head")
            for name, (start, length, le) in sorted(lay.items(), key=lambda x: x[1][0]):
                line = f"   {name:<40} start {start:>4}  length {length:>3}  {'Intel' if le else 'Motorola'}\n"
                text.insert("end", line, () if (start, length, le) in common else ("diff",))
            text.insert("end", "\n")
        text.configure(state="disabled")
        self.choice = tk.StringVar(value={True: "on", False: "off"}.get(current, "auto"))
        for value, label in (("on", "One Ethernet PDU for all these buses (1:N), also when the signal layout differs"),
                             ("off", "An own Ethernet PDU and header id per bus"),
                             ("auto", "Automatic: 1:N when the CAN id and the length are the same")):
            ttk.Radiobutton(f, text=label, value=value, variable=self.choice).pack(anchor="w")
        lengths = {r.length for r in routes}
        if len(lengths) > 1:
            ttk.Label(f, text="The lengths differ: 1:N is not possible (" +
                      ", ".join(f"{r.bus.name} {r.length}" for r in routes) + ").",
                      foreground="#b03a00").pack(anchor="w", pady=(6, 0))
        bb = ttk.Frame(self, padding=(10, 0, 10, 10))
        bb.pack(fill="x")
        ttk.Button(bb, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(bb, text="OK", command=self.ok).pack(side="right", padx=6)
        self.grab_set()

    def ok(self):
        self.result = {"on": True, "off": False, "auto": None}[self.choice.get()]
        self.done = True
        self.destroy()


class PeerDialog(tk.Toplevel):
    """Another Ethernet node: its sockets for both directions (the local sockets are shared by default)."""

    def __init__(self, master, app, peer: EthPeer, taken=()):
        super().__init__(master)
        self.title("Ethernet peer")
        self.transient(master)
        self.result, self.taken = None, set(taken)
        dialog_header(self, "Ethernet peer",
                      "Another node the gateway ECU exchanges PDUs with (for example another zone ECU). Leave the "
                      "local socket at <create new> with empty name and port to share the local sockets of the "
                      "default peer (one sending and one receiving port for all nodes).")
        f = ttk.Frame(self, padding=10)
        f.pack(fill="both", expand=True)
        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Label(top, text="Name:").pack(side="left")
        self.v_name = tk.StringVar(value=peer.name)
        ttk.Entry(top, textvariable=self.v_name, width=30).pack(side="left", padx=6)
        sides = ttk.Frame(f)
        sides.pack(fill="both", expand=True, pady=(8, 0))
        self.tx = SideFrame(sides, "CAN -> ETH  (sent to this node)", app)
        self.rx = SideFrame(sides, "ETH -> CAN  (received from this node)", app)
        self.tx.pack(side="left", fill="both", expand=True, padx=(0, 4))
        self.rx.pack(side="left", fill="both", expand=True, padx=(4, 0))
        ch, conn = app.channel(), app.connector()
        for frame, side in ((self.tx, peer.can_to_eth), (self.rx, peer.eth_to_can)):
            frame.fill(ch, conn)
            frame.load(side)
        if app.v_onesock.get():                 # both directions use the CAN -> ETH socket
            self.tx.config(text="Socket  (both directions with this node)")
            self.rx.config(text="ETH -> CAN  (same socket)")
            set_enabled(self.rx, False)
        bb = ttk.Frame(self, padding=(10, 0, 10, 10))
        bb.pack(fill="x")
        ttk.Button(bb, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(bb, text="OK", command=self.ok).pack(side="right", padx=6)
        self.grab_set()

    def ok(self):
        name = self.v_name.get().strip()
        if not name:
            messagebox.showwarning(TITLE, "Enter the name of the peer.", parent=self)
            return
        if name in self.taken or name == "default":
            messagebox.showwarning(TITLE, f"The name {name} is already used.", parent=self)
            return
        peer = EthPeer(name=name)
        self.tx.store(peer.can_to_eth)
        self.rx.store(peer.eth_to_can)
        self.result = peer
        self.destroy()


class LinkDialog(tk.Toplevel):
    """Pair a message the node receives on one bus with a message it sends on another (CAN -> CAN)."""

    def __init__(self, master, plan, preset=None):
        super().__init__(master)
        self.title("CAN -> CAN link")
        self.transient(master)
        self.result = None
        dialog_header(self, "Add a CAN -> CAN link",
                      "The whole PDU received on the source bus is sent unchanged on the destination bus (same "
                      "length). Use it for messages the automatic pairing does not find or finds on several buses.")
        f = ttk.Frame(self, padding=10)
        f.pack(fill="both")
        label = lambda r: f"{r.bus.name} / {r.message.name}  ({r.message.id_text}, {r.length} byte)"
        self._src = {label(r): r for r in plan.routes if r.direction == CAN_TO_ETH and not r.can_problem}
        self._dst = {label(r): r for r in plan.routes if r.direction == ETH_TO_CAN and not r.can_problem}
        ttk.Label(f, text="Received on (source):").grid(row=0, column=0, sticky="w", pady=3)
        self.c_src = ttk.Combobox(f, values=sorted(self._src), width=60, state="readonly")
        self.c_src.grid(row=0, column=1, sticky="we")
        ttk.Label(f, text="Sent on (destination):").grid(row=1, column=0, sticky="w", pady=3)
        self.c_dst = ttk.Combobox(f, values=sorted(self._dst), width=60, state="readonly")
        self.c_dst.grid(row=1, column=1, sticky="we")
        for r in preset or ():
            if r.direction == CAN_TO_ETH and label(r) in self._src:
                self.c_src.set(label(r))
            elif r.direction == ETH_TO_CAN and label(r) in self._dst:
                self.c_dst.set(label(r))
        bb = ttk.Frame(self, padding=(10, 0, 10, 10))
        bb.pack(fill="x")
        ttk.Button(bb, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(bb, text="OK", command=self.ok).pack(side="right", padx=6)
        self.grab_set()

    def ok(self):
        s, d = self._src.get(self.c_src.get()), self._dst.get(self.c_dst.get())
        if s is None or d is None:
            messagebox.showwarning(TITLE, "Select the source and the destination message.", parent=self)
            return
        if s.bus is d.bus:
            messagebox.showwarning(TITLE, "Source and destination must be on different buses.", parent=self)
            return
        self.result = {"src_bus": s.bus.name, "src_msg": s.message.name,
                       "dst_bus": d.bus.name, "dst_msg": d.message.name}
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
        self.project = None                 # DvProject when the base is a DaVinci project (.dpa)
        self._base_key = None
        self.dbc_cache: dict = {}
        self.plan = None
        self._q = queue.Queue()
        self._busy = False
        self._done_text = ""            # after Generate: what to do next in DaVinci
        self._project_gw = ""           # gateway file of this tool found in the loaded project
        self._hint_state = None
        self._build()
        self.win.after(100, self._poll)
        self.win.after(500, self._hint_tick)
        if config_path:
            self.open_config(config_path)

    # ------------------------------------------------------------------ layout
    def _build(self):
        w = self.win
        tb = ttk.Frame(w, padding=(6, 4))
        tb.pack(fill="x")
        b_start = ttk.Button(tb, text="Start…", command=self.start_wizard)
        b_start.pack(side="left", padx=(0, 4))
        Tooltip(b_start, "Guided start: only DBC files / DaVinci project with the DBC files / update a gateway file "
                         "that is already in the project")
        for text, cmd in (("New", self.new_config), ("Open…", self.open_config_dialog), ("Save", self.save_config),
                          ("Save As…", self.save_config_as)):
            ttk.Button(tb, text=text, command=cmd).pack(side="left", padx=(0, 4))
        ttk.Separator(tb, orient="vertical").pack(side="left", fill="y", padx=6)
        self.b_plan = ttk.Button(tb, text="Analyze", command=self.analyze)
        self.b_plan.pack(side="left", padx=(0, 4))
        self.b_gen = ttk.Button(tb, text="Generate network ARXML", command=self.generate)
        self.b_gen.pack(side="left")
        b_rep = ttk.Button(tb, text="Message Report", command=self.message_report)
        b_rep.pack(side="left", padx=(4, 0))
        Tooltip(b_rep, "Write and open the message path report: for every message (by CAN id) where it comes from, "
                       "which gateway it passes and where it goes (HTML + CSV next to the output file)")
        b_capl = ttk.Button(tb, text="CAPL Test (ETH->CAN)", command=self.capl_test)
        b_capl.pack(side="left", padx=(4, 0))
        Tooltip(b_capl, "Write a CANoe CAPL node (.can) per Ethernet source node that sends the Ethernet PDUs of the "
                        "ETH -> CAN routes to the gateway ECU (SoAd header + DBC initial values), next to the output "
                        "file: check the CAN frames in the Trace window")
        ttk.Separator(tb, orient="vertical").pack(side="left", fill="y", padx=6)
        b_edit = ttk.Button(tb, text="Edit Existing Gateway…", command=self.open_editor)
        b_edit.pack(side="left")
        Tooltip(b_edit, "Open a network file that already contains a gateway (e.g. the generated output) to change "
                        "header ids, sockets, ports, IP addresses or to delete routes")
        self.status = ttk.Label(tb, text="Add the DBC files (and the base system description if the project has "
                                         "one).")
        self.status.pack(side="left", padx=12)
        # "next step" bar: what to do now, with the button that does it
        self.hint = tk.Frame(w, background="#e8f1fb", highlightthickness=1, highlightbackground="#b7d0ea")
        self.hint.pack(fill="x", padx=6, pady=(0, 4))
        tk.Label(self.hint, text="Next step:", background="#e8f1fb", font=("Segoe UI", 9, "bold")).pack(
            side="left", padx=(8, 4), pady=4)
        self.hint_text = tk.Label(self.hint, text="", background="#e8f1fb", anchor="w", justify="left")
        self.hint_text.pack(side="left", fill="x", expand=True, pady=4)
        self.hint_buttons = ttk.Frame(self.hint)
        self.hint_buttons.pack(side="right", padx=6, pady=2)
        pw = ttk.PanedWindow(w, orient="vertical")
        pw.pack(fill="both", expand=True)
        nb = ttk.Notebook(pw)
        self.nb = nb
        pw.add(nb, weight=0)
        self._tab_input(nb)
        self._tab_eth(nb)
        self._tab_options(nb)
        bottom = ttk.Frame(pw)
        pw.add(bottom, weight=1)
        # give the settings notebook its full height (the Ethernet tab is the tallest)
        self.win.after(150, lambda: pw.sashpos(0, max(nb.winfo_reqheight(), 360)))
        self._routes(bottom)

    def _tab_input(self, nb):
        f = ttk.Frame(nb, padding=10)
        nb.add(f, text="  Input  ")
        f.columnconfigure(1, weight=1)
        self.v_base, self.v_out = tk.StringVar(), tk.StringVar()
        ttk.Label(f, text="Network file / DaVinci project:").grid(row=0, column=0, sticky="w", pady=2)
        e = ttk.Entry(f, textvariable=self.v_base)
        e.grid(row=0, column=1, sticky="we", pady=2)
        e.bind("<FocusOut>", lambda _e: self.load_base())
        e.bind("<Return>", lambda _e: self.load_base())
        ttk.Button(f, text="Browse…", command=self.browse_base).grid(row=0, column=2, padx=4)
        Tooltip(e, "Network ARXML of the project, or the DaVinci project (.dpa) in which the CAN databases are "
                   "already imported. Leave empty when you only have DBC files: a new network file is created")
        ttk.Label(f, text="Output file:").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Entry(f, textvariable=self.v_out).grid(row=1, column=1, sticky="we", pady=2)
        ttk.Button(f, text="Browse…", command=self.browse_out).grid(row=1, column=2, padx=4)
        ttk.Label(f, text="Previous gateway file:").grid(row=2, column=0, sticky="w", pady=2)
        self.v_prev = tk.StringVar()
        pf = ttk.Frame(f)
        pf.grid(row=2, column=1, columnspan=2, sticky="we", pady=2)
        e_prev = ttk.Entry(pf, textvariable=self.v_prev)
        e_prev.pack(side="left", fill="x", expand=True)
        e_prev.bind("<FocusOut>", lambda _e: self.load_base())
        ttk.Button(pf, text="Browse…", command=self.browse_prev).pack(side="left", padx=4)
        ttk.Button(pf, text="Clear", command=lambda: (self.v_prev.set(""), self.load_base())).pack(side="left")
        Tooltip(e_prev, "Gateway file generated before and already imported in DaVinci. Its elements are taken "
                        "out of the base and the routes that stay keep their Ethernet PDU names and header ids, so "
                        "DaVinci keeps their configuration on the next Update. Set automatically by Open… of a "
                        "generated .arxml")
        ttk.Label(f, text="Gateway ECU:").grid(row=3, column=0, sticky="w", pady=2)
        ef = ttk.Frame(f)
        ef.grid(row=3, column=1, sticky="w", pady=2)
        self.c_ecu = Choice(ef, width=60, on_change=self.ecu_changed)
        self.c_ecu.cb.pack(side="left")
        self.c_ecu.cb.bind("<FocusOut>", lambda _e: self.new_file_changed())
        self.c_ecu.cb.bind("<Return>", lambda _e: self.new_file_changed())
        ttk.Label(ef, text="   Schema (new file):").pack(side="left")
        self.v_schema = tk.StringVar(value=DEFAULT_SCHEMA)
        self.c_schema = ttk.Combobox(ef, textvariable=self.v_schema, values=SCHEMAS, width=16, state="readonly")
        self.c_schema.pack(side="left", padx=4)
        self.c_schema.bind("<<ComboboxSelected>>", lambda _e: self.new_file_changed())
        Tooltip(self.c_schema, "Only for a new file (no base file). DaVinci 5.24 reads up to AUTOSAR_00049, "
                               "DaVinci 5.31 up to AUTOSAR_00053")
        self.base_info = ttk.Label(f, text="", foreground="#666666")
        self.base_info.grid(row=4, column=1, sticky="w")
        lf = ttk.LabelFrame(f, text="CAN buses (DBC)", padding=6)
        lf.grid(row=5, column=0, columnspan=3, sticky="nsew", pady=(8, 0))
        f.rowconfigure(5, weight=1)
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
        tf = ttk.LabelFrame(f, text="Routing table (CAN -> CAN)", padding=6)
        tf.grid(row=6, column=0, columnspan=3, sticky="we", pady=(8, 0))
        tf.columnconfigure(1, weight=1)
        ttk.Label(tf, text="Excel / CSV file:").grid(row=0, column=0, sticky="w")
        self.v_table = tk.StringVar()
        e_table = ttk.Entry(tf, textvariable=self.v_table)
        e_table.grid(row=0, column=1, sticky="we", padx=4)
        ttk.Button(tf, text="Browse…", command=self.browse_table).grid(row=0, column=2)
        ttk.Button(tf, text="Clear", command=lambda: self.v_table.set("")).grid(row=0, column=3, padx=(4, 0))
        Tooltip(e_table, "Routing table of the customer, given with the DBC files (.xlsx or .csv). When set, the CAN "
                         "-> CAN routes come only from it: a message row (Routing Type 0) becomes a PduR route, a "
                         "signal row (Routing Type 1) a Com signal gateway (.vsde file). Network columns are matched "
                         "to the buses by name (Edit DBC: Routing table network). Empty = messages are paired by "
                         "name / CAN id")
        self.v_table_hw = tk.BooleanVar(value=False)
        cb_hw = ttk.Checkbutton(tf, text="Route HW accelerator rows too (HW-Accelerator = 1)", variable=self.v_table_hw)
        cb_hw.grid(row=1, column=1, columnspan=3, sticky="w", padx=4, pady=(2, 0))
        Tooltip(cb_hw, "Off: rows with HW-Accelerator = 1 are left to the LLCE / PFE (no PduR / Com route; their "
                       "target message is not sent from Ethernet either). On: they are routed like the others")

    def _tab_eth(self, nb):
        f = ttk.Frame(nb, padding=10)
        nb.add(f, text="  Ethernet  ")
        row = ttk.Frame(f)
        row.pack(fill="x")
        top = ttk.Frame(row)
        top.pack(side="left", fill="x", expand=True, anchor="n")
        top.columnconfigure(1, weight=1)
        self._new_eth_frame(row)
        ttk.Label(top, text="Ethernet channel (VLAN):").grid(row=0, column=0, sticky="w", pady=2)
        self.c_chan = Choice(top, width=62, on_change=self.channel_changed).grid(row=0, column=1, sticky="w")
        b_sug = ttk.Button(top, text="Suggest values", command=self.suggest_eth)
        b_sug.grid(row=0, column=2, padx=(6, 0))
        Tooltip(b_sug, "Fill the empty Ethernet settings (channel, ECU IP, remote node, ports, MAC) from what the "
                       "base file / project already contains. Values you entered are kept.")
        ttk.Label(top, text="ECU connector:").grid(row=1, column=0, sticky="w", pady=2)
        self.c_conn = Choice(top, width=62, on_change=self.connector_changed).grid(row=1, column=1, sticky="w")
        ttk.Label(top, text="Local endpoint (ECU IP):").grid(row=2, column=0, sticky="w", pady=2)
        self.c_nep = Choice(top, width=62).grid(row=2, column=1, sticky="w")
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
        self.c_idset = Choice(top, width=62, editable=True).grid(row=4, column=1, sticky="w")
        osf = ttk.Frame(f)
        osf.pack(fill="x", pady=(8, 0))
        self.v_onesock = tk.BooleanVar(value=True)
        cb = ttk.Checkbutton(osf, text="One socket for both directions (CAN -> ETH and ETH -> CAN use the same socket "
                                       "and socket connection)", variable=self.v_onesock,
                             command=self._one_socket_changed)
        cb.pack(side="left")
        Tooltip(cb, "The ETH -> CAN PDUs are received on the socket the CAN -> ETH PDUs are sent from: one port "
                    "per node (SA_<ECU>_CanGw), one socket connection per node pair.")
        b_nodes = ttk.Button(osf, text="Ethernet nodes…", command=self.edit_nodes)
        b_nodes.pack(side="right")
        Tooltip(b_nodes, "MAC, IPv4 and port base of the Ethernet nodes: empty fields are filled from this table "
                         "by node name (Analyze, Suggest values).")
        sides = ttk.Frame(f)
        sides.pack(fill="x", pady=(4, 0))
        self.side_tx = SideFrame(sides, "CAN -> ETH  (gateway ECU sends on Ethernet)", self)
        self.side_rx = SideFrame(sides, "ETH -> CAN  (gateway ECU receives from Ethernet)", self)
        self.side_tx.pack(side="left", fill="both", expand=True, padx=(0, 4))
        self.side_rx.pack(side="left", fill="both", expand=True, padx=(4, 0))
        # PDU collection: several CAN -> ETH PDUs in one UDP datagram (SoAd nPdu)
        cf = ttk.LabelFrame(f, text="PDU collection (CAN -> ETH: several PDUs in one UDP datagram, SoAd nPdu)",
                            padding=6)
        cf.pack(fill="x", pady=(8, 0))
        self.v_col_on = tk.BooleanVar(value=False)
        cb = ttk.Checkbutton(cf, text="Collect", variable=self.v_col_on)
        cb.pack(side="left")
        Tooltip(cb, "The PDUs wait in the SoAd buffer of the socket connection and leave together in one UDP datagram "
                    "(each PDU keeps its header id). Every received CAN frame is sent once (QUEUED); nothing is sent "
                    "when nothing was received. DaVinci derives SoAdSocketnPduUdpTxBufferMin, "
                    "SoAdSocketUdpTriggerTimeout and SoAdTxUdpTriggerMode / Timeout.")
        ttk.Label(cf, text="timeout ms:").pack(side="left", padx=(12, 2))
        self.v_col_timeout = tk.StringVar(value="5")
        e_t = ttk.Entry(cf, textvariable=self.v_col_timeout, width=6)
        e_t.pack(side="left")
        Tooltip(e_t, "A collected PDU is sent at the latest after this time (SoAd checks it in its main function: "
                     "use the SoAd main function period or a multiple of it).")
        ttk.Label(cf, text="buffer bytes:").pack(side="left", padx=(12, 2))
        self.v_col_buffer = tk.StringVar(value="1400")
        e_b = ttk.Entry(cf, textvariable=self.v_col_buffer, width=6)
        e_b.pack(side="left")
        Tooltip(e_b, "Maximum UDP payload (PDU headers included); a full buffer is sent and a new one started. "
                     "<= 1472 avoids IP fragmentation.")
        self.v_col_mode = tk.StringVar(value="all")
        ttk.Radiobutton(cf, text="collect every PDU", value="all", variable=self.v_col_mode).pack(side="left",
                                                                                                   padx=(12, 0))
        ttk.Radiobutton(cf, text="send at once: event messages and cycle <=", value="cycle",
                        variable=self.v_col_mode).pack(side="left", padx=(8, 2))
        self.v_col_cycle = tk.StringVar(value="20")
        ttk.Entry(cf, textvariable=self.v_col_cycle, width=5).pack(side="left")
        ttk.Label(cf, text="ms   (per message: route table, right click)").pack(side="left", padx=(2, 0))
        # in the free space under the channel settings (the tab does not get taller)
        pl = ttk.LabelFrame(top, text="Ethernet peers (nodes the PDUs are sent to / received from)", padding=6)
        pl.grid(row=5, column=0, columnspan=3, sticky="we", pady=(10, 0))
        left = ttk.Frame(pl)
        left.pack(side="left", fill="y")
        ttk.Label(left, text="Name of the node above:").pack(anchor="w")
        self.v_defpeer = tk.StringVar()
        e_def = ttk.Entry(left, textvariable=self.v_defpeer, width=22)
        e_def.pack(anchor="w", pady=(2, 0))
        Tooltip(e_def, "Name of the node of the two socket frames above (the default peer of every route). "
                       "Empty = default.")
        cols = ("Peer", "CAN -> ETH to", "ETH -> CAN from")
        self.t_peers = ttk.Treeview(pl, columns=cols, show="headings", height=3, selectmode="browse")
        for c, wd in zip(cols, (140, 200, 200)):
            self.t_peers.heading(c, text=c, anchor="w")
            self.t_peers.column(c, width=wd, anchor="w")
        self.t_peers.pack(side="left", fill="x", expand=True, padx=(10, 0))
        self.t_peers.bind("<Double-1>", lambda _e: self.edit_peer())
        pb = ttk.Frame(pl)
        pb.pack(side="left", fill="y", padx=(6, 0))
        ttk.Button(pb, text="Add peer…", command=self.add_peer).pack(fill="x")
        ttk.Button(pb, text="Edit…", command=self.edit_peer).pack(fill="x", pady=3)
        ttk.Button(pb, text="Remove", command=self.remove_peer).pack(fill="x")

    # ------------------------------------------------------------------ Ethernet peers
    def peer_names(self) -> list[str]:
        e = self.cfg.ethernet
        return [self.v_defpeer.get().strip() or "default"] + [p.name for p in e.peers]

    def refresh_peers(self):
        t = self.t_peers
        t.delete(*t.get_children())

        def where(s: SocketSide):
            if s.remote_socket:
                return s.remote_socket.rsplit("/", 1)[-1]
            return f"{s.remote_ip or '?'}:{s.remote_port if s.remote_port is not None else '?'}"
        for i, p in enumerate(self.cfg.ethernet.peers):
            t.insert("", "end", iid=str(i), values=(p.name, where(p.can_to_eth), where(p.eth_to_can)))

    def add_peer(self):
        e = self.cfg.ethernet
        self.side_tx.store(e.can_to_eth)
        self.side_rx.store(e.eth_to_can)
        # the same ports on every node (one receiving / one sending port): prefilled from the default peer
        new = EthPeer(can_to_eth=SocketSide(remote_port=e.can_to_eth.remote_port),
                      eth_to_can=SocketSide(remote_port=e.eth_to_can.remote_port))
        d = PeerDialog(self.win, self, new, taken=self.peer_names())
        self.win.wait_window(d)
        if d.result is not None:
            e.peers.append(d.result)
            self.refresh_peers()

    def edit_peer(self):
        sel = self.t_peers.selection()
        if not sel:
            return
        i = int(sel[0])
        peers = self.cfg.ethernet.peers
        old = peers[i].name
        d = PeerDialog(self.win, self, peers[i], taken=[n for n in self.peer_names() if n != old])
        self.win.wait_window(d)
        if d.result is not None:
            peers[i] = d.result
            if d.result.name != old:
                self._rename_peer(old, d.result.name)
            self.refresh_peers()

    def remove_peer(self):
        sel = self.t_peers.selection()
        if not sel:
            return
        name = self.cfg.ethernet.peers[int(sel[0])].name
        del self.cfg.ethernet.peers[int(sel[0])]
        self._rename_peer(name, None)
        self.refresh_peers()

    def _rename_peer(self, old, new):
        """Message overrides follow a renamed peer; a removed peer is dropped from them."""
        for b in self.cfg.buses:
            for over in b.messages.values():
                if over.get("eth_peer") == old:
                    if new:
                        over["eth_peer"] = new
                    else:
                        over.pop("eth_peer")
                if isinstance(over.get("eth_peers"), list) and old in over["eth_peers"]:
                    rest = [new if x == old else x for x in over["eth_peers"] if new or x != old]
                    if rest:
                        over["eth_peers"] = rest
                    else:
                        over.pop("eth_peers")

    def _new_eth_frame(self, parent):
        """Fields for a new Ethernet channel / ECU connection (base file without Ethernet, new VLAN, or an
        ECU that is not connected to the channel yet)."""
        nf = ttk.LabelFrame(parent, text="New channel / ECU connection", padding=6)
        nf.pack(side="left", fill="y", padx=(10, 0))
        self.c_cluster = Choice(nf, width=30)
        self.c_ctrl = Choice(nf, width=30)
        self.v_chname, self.v_vlan = tk.StringVar(), tk.StringVar()
        self.v_ecuip, self.v_mask, self.v_mac = tk.StringVar(), tk.StringVar(value="255.255.255.0"), tk.StringVar()
        rows = (("Cluster:", self.c_cluster.cb), ("Channel name:", ttk.Entry(nf, textvariable=self.v_chname, width=33)),
                ("VLAN id:", ttk.Entry(nf, textvariable=self.v_vlan, width=8)),
                ("ECU IP / netmask:", None), ("Controller:", self.c_ctrl.cb),
                ("MAC (new controller):", ttk.Entry(nf, textvariable=self.v_mac, width=20)))
        for i, (label, w) in enumerate(rows):
            ttk.Label(nf, text=label).grid(row=i, column=0, sticky="w", pady=1)
            if w is not None:
                w.grid(row=i, column=1, sticky="w", pady=1)
        ipf = ttk.Frame(nf)
        ipf.grid(row=3, column=1, sticky="w")
        ttk.Entry(ipf, textvariable=self.v_ecuip, width=16).pack(side="left")
        ttk.Entry(ipf, textvariable=self.v_mask, width=16).pack(side="left", padx=(4, 0))
        self.new_eth_hint = ttk.Label(nf, text="", foreground="#666666", wraplength=360, justify="left")
        self.new_eth_hint.grid(row=6, column=0, columnspan=2, sticky="w", pady=(4, 0))

    def _tab_options(self, nb):
        f = ttk.Frame(nb, padding=10)
        nb.add(f, text="  Options & Naming  ")
        o = ttk.LabelFrame(f, text="Options", padding=6)
        o.pack(side="left", fill="y")
        self.v_ethroutes = tk.BooleanVar(value=True)
        self.v_canroutes = tk.BooleanVar(value=True)
        self.v_canmatchid = tk.BooleanVar(value=True)
        ttk.Checkbutton(o, text="CAN <-> Ethernet routes", variable=self.v_ethroutes).pack(anchor="w")
        ttk.Checkbutton(o, text="CAN -> CAN routes (a message the node receives on one bus and sends\n"
                                "on another)", variable=self.v_canroutes).pack(anchor="w")
        ttk.Checkbutton(o, text="CAN -> CAN: also pair renamed messages (same CAN id and length)",
                        variable=self.v_canmatchid).pack(anchor="w", padx=(18, 0), pady=(0, 6))
        self.v_fanout = tk.BooleanVar(value=True)
        ttk.Checkbutton(o, text="ETH -> CAN 1:N: a message sent on several buses (same CAN id, length and layout)\n"
                                "is one Ethernet PDU forwarded to every bus",
                        variable=self.v_fanout).pack(anchor="w", pady=(0, 6))
        self.v_nocom = tk.BooleanVar(value=True)
        ttk.Checkbutton(o, text="ETH -> CAN: Com does not send the CAN PDUs fed from Ethernet (DBC files imported\n"
                                "in DaVinci: written to the .vsde file)",
                        variable=self.v_nocom).pack(anchor="w", pady=(0, 6))
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
        self.v_onlyprev = tk.BooleanVar()
        ttk.Checkbutton(o, text="Regeneration: on the buses of the previous gateway file, route only\n"
                                "its messages (new messages stay off until you enable them)",
                        variable=self.v_onlyprev).pack(anchor="w", pady=(6, 0))
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
        self.t_routes = ttk.Treeview(top, columns=cols, show="headings", selectmode="extended", height=8)
        widths = (60, 65, 75, 120, 200, 90, 70, 55, 65, 200, 220, 90, 130, 95, 220, 380)
        for c, wd in zip(cols, widths):
            self.t_routes.heading(c, text=c, anchor="w")
            self.t_routes.column(c, width=wd, anchor="w", stretch=c == "Remark")
        ys = ttk.Scrollbar(top, orient="vertical", command=self.t_routes.yview)
        self.t_routes.configure(yscrollcommand=ys.set)
        self.t_routes.pack(side="left", fill="both", expand=True)
        ys.pack(side="left", fill="y")
        self.t_routes.tag_configure("off", foreground="#9e9e9e")
        self.t_routes.tag_configure("flag", foreground=COLORS["warning"])
        self.t_routes.tag_configure("new", foreground=COLORS["info"])
        self.t_routes.tag_configure("removed", foreground=COLORS["error"])
        self.t_routes.bind("<Double-1>", lambda _e: self.edit_route())
        self.t_routes.bind("<space>", lambda _e: self.toggle_routes())
        self.t_routes.bind("<Button-3>", self.route_menu)
        msg = ttk.Frame(parent)
        msg.pack(fill="x")
        self.t_msg = tk.Text(msg, height=6, wrap="word", font=("Segoe UI", 9), background="#ffffff",
                             relief="flat", borderwidth=1)
        self.t_msg.pack(fill="x", padx=2, pady=2)
        self.t_msg.tag_configure("error", foreground=COLORS["error"])
        self.t_msg.tag_configure("warning", foreground=COLORS["warning"])
        self.t_msg.tag_configure("info", foreground=COLORS["info"])

    # ------------------------------------------------------------------ background work
    def _run(self, label, fn, done):
        if self._busy:                  # one job at a time: try again when the running one has finished
            self.win.after(200, lambda: self._run(label, fn, done))
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
        p = filedialog.askopenfilename(parent=self.win, title="Network file or DaVinci project",
                                       filetypes=[("Network ARXML / DaVinci project", "*.arxml *.dpa"),
                                                  ("AUTOSAR XML", "*.arxml"), ("DaVinci project", "*.dpa"),
                                                  ("All files", "*.*")])
        if p:
            self.v_base.set(os.path.normpath(p))
            if not self.v_out.get() and not dvproject.is_project(p):
                self.v_out.set(os.path.splitext(p)[0] + "_gateway.arxml")
            self.load_base()

    def browse_out(self):
        p = filedialog.asksaveasfilename(parent=self.win, title="Output file", defaultextension=".arxml",
                                         filetypes=[("AUTOSAR XML", "*.arxml")])
        if p:
            self.v_out.set(os.path.normpath(p))

    def new_file_changed(self):
        if not self.v_base.get().strip():
            self.load_base()

    def load_base(self, then=None):
        path = self.v_base.get().strip()
        new_mode = not path
        self.c_schema.configure(state="readonly" if new_mode else "disabled")
        self.c_ecu.cb.configure(state="normal" if new_mode else "readonly")
        if new_mode:
            # no base file: the generator creates a new system description with this ECU
            cfg = GatewayConfig(ecu=self.c_ecu.get(), buses=self.cfg.buses, schema=self.v_schema.get())
            name = new_ecu_name(cfg)
            key = ("new", name, cfg.schema)
            if key != self._base_key:
                self.project = None
                self.base, self._base_key = new_document("new.arxml", name, cfg.schema), key
                self.base_info.config(text=f"No base file: a new network file ({cfg.schema}) is created with the "
                                           f"ECU {name} (type another name in Gateway ECU).")
                self.fill_from_base()
                self.c_ecu.var.set(name)
            if then:
                then()
            return
        if not os.path.isfile(path):
            return
        prev = self.v_prev.get().strip()
        prev_ok = bool(prev) and os.path.isfile(prev)
        key = (os.path.abspath(path), os.path.getmtime(path), os.path.abspath(prev) if prev_ok else "",
               os.path.getmtime(prev) if prev_ok else 0)
        if key == self._base_key:
            if then:
                then()
            return

        def done(res):
            proj, base = res
            self.project, self.base, self._base_key = proj, base, key
            self._project_gw = ""
            if proj:
                gws = start.project_gateway_files(proj.path)
                prev = os.path.normcase(os.path.abspath(self.v_prev.get().strip())) if self.v_prev.get().strip() else ""
                if gws and prev not in {os.path.normcase(x) for x in gws}:
                    self._project_gw = gws[0]
                chans = dvproject.ecu_can_channels(base, proj.ecu_path)
                self.base_info.config(text=f"DaVinci project {proj.name} ({base.schema}): ECU instance "
                                           f"{proj.ecu_name}, {len(chans)} CAN channel(s). The output is an "
                                           f"additional input file (Ethernet + gateway); the DBC files stay imported.")
                if not self.v_out.get():
                    self.v_out.set(os.path.join(proj.dir, f"{proj.ecu_name}_CanEthGateway.arxml"))
            else:
                self.base_info.config(text=f"{base.schema}: {len(base.ecus())} ECU, {len(base.eth_channels())} "
                                           f"Ethernet channel(s), {len(base.can_channels())} CAN channel(s)")
            n = sum(getattr(base, "prev_removed", {}).values()) if getattr(base, "prev_removed", None) else 0
            if n:
                self.base_info.config(text=self.base_info.cget("text") + f"  -  {n} element(s) of the previous "
                                                                          f"gateway file taken out")
            self.fill_from_base()
            self.status.config(text="Project loaded." if proj else "Base file loaded.")
            if then:
                then()

        out = self.v_out.get().strip()          # read the widgets here: work() runs in a thread

        def work():
            c = GatewayConfig(base=path, previous=prev if prev_ok else "", output=out)
            return (dvproject.read(path) if dvproject.is_project(path) else None), load_base(c)
        self._run("Loading " + os.path.basename(path), work, done)

    def browse_table(self):
        p = filedialog.askopenfilename(parent=self.win, title="Routing table",
                                       filetypes=[("Routing table", "*.xlsx *.xlsm *.csv *.tsv *.txt"),
                                                  ("All files", "*.*")])
        if p:
            self.v_table.set(os.path.normpath(p))

    def table_networks(self) -> list[str]:
        """Network columns of the routing table ([] when there is none or it cannot be read)."""
        path = self.v_table.get().strip()
        if not path or not os.path.isfile(path):
            return []
        key = (os.path.abspath(path), os.path.getmtime(path))
        if getattr(self, "_table_key", None) != key:
            from . import routing_table
            try:
                self._table_nets = routing_table.read(path).networks
            except Exception:  # noqa: BLE001 - the analysis reports the problem
                self._table_nets = []
            self._table_key = key
        return self._table_nets

    def browse_prev(self):
        p = filedialog.askopenfilename(parent=self.win, title="Previous gateway file (already imported in DaVinci)",
                                       filetypes=[("AUTOSAR XML", "*.arxml"), ("All files", "*.*")])
        if p:
            self.v_prev.set(os.path.normpath(p))
            if not self.v_out.get().strip():
                self.v_out.set(os.path.normpath(p))
            self.load_base()

    def fill_from_base(self):
        b = self.base
        ecus = [(AUTO, "")] + [(p.rsplit("/", 1)[-1] + f"   ({p})", p) for p in b.ecus()]
        self.c_ecu.set_items(ecus)
        self.c_ecu.set(self.cfg.ecu)
        self.c_idset.set_items([(AUTO, "")] + [(p.rsplit("/", 1)[-1] + f"   ({p})", p) for p in b.id_sets()])
        self.c_idset.set(self.cfg.ethernet.id_set)
        self.c_cluster.set_items([(AUTO, "")] + [(p.rsplit("/", 1)[-1], p) for p in b.eth_clusters()])
        self.c_cluster.set(self.cfg.ethernet.cluster)
        self.ecu_changed()

    def project_ecu(self) -> str:
        """ECU instance of the loaded DaVinci project ('' when the base is not a project)."""
        proj = getattr(self, "project", None)
        if not proj or self.base is None:
            return ""
        return self.ecu() or proj.ecu_path

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
        chans = b.eth_channels()
        self._channels = {c.path: c for c in chans}
        connected = [c for c in chans if ecu and any(b.connector_ecu(x) == ecu for x in c.connectors)]
        items = [(AUTO, "")] if chans else []
        items += [(NEW_CHANNEL, NEW_VALUE)]
        items += [(f"{c.label}   ({c.path})" + ("" if c in connected else "   - ECU not connected"), c.path)
                  for c in connected + [c for c in chans if c not in connected]]
        self.c_chan.set_items(items)
        e = self.cfg.ethernet
        want = e.channel
        hit = [c.path for c in chans if want in (c.path, c.name) or
               (c.vlan is not None and want.upper() in (f"VLAN{c.vlan}", str(c.vlan)))] if want else []
        self.c_chan.set(NEW_VALUE if (e.new_channel or not chans) else (hit[0] if hit else ""))
        ctrls = b.ecu_controllers(ecu, "ETHERNET-COMMUNICATION-CONTROLLER") if ecu else []
        self.c_ctrl.set_items([(AUTO if ctrls else NEW, "")] + [(p.rsplit("/", 1)[-1], p) for p in ctrls])
        self.c_ctrl.set(e.controller)
        self.channel_changed()

    def channel(self):
        p = self.c_chan.get()
        if p == NEW_VALUE:
            return None
        if p:
            return self._channels.get(p)
        ecu = self.ecu()
        chans = [c for c in self._channels.values()
                 if any(self.base.connector_ecu(x) == ecu for x in c.connectors)] if ecu else []
        return chans[0] if len(chans) == 1 else None

    def channel_changed(self):
        ch, ecu = self.channel(), self.ecu()
        conns = [c for c in (ch.connectors if ch else []) if self.base.connector_ecu(c) == ecu]
        self.c_conn.set_items([(AUTO if conns else NEW, "")] + [(c.rsplit("/", 1)[-1], c) for c in conns])
        self.c_conn.set(next((c for c in conns if self.cfg.ethernet.connector in (c, c.rsplit('/', 1)[-1])), ""))
        new_channel = self.c_chan.get() == NEW_VALUE or not self._channels
        if new_channel:
            hint = ("A new Ethernet channel is created" +
                    ("" if self._channels else " in a new Ethernet cluster") +
                    ", with a connector and the IP address of the ECU. Empty VLAN id = untagged.")
        elif ch is not None and not conns:
            hint = ("The ECU is not connected to this channel: a connector is created. Enter the ECU IP "
                    "address unless the channel already has it.")
        elif ch is not None and not [e for e in ch.endpoints if e.path in set(self.own_endpoints())]:
            hint = ("The ECU has no IP address on this channel: enter the ECU IP address (its network endpoint "
                    "is created).")
        else:
            hint = "Not used: the ECU is already connected to the selected channel."
        self.new_eth_hint.config(text=hint)
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

    def open_editor(self):
        """Gateway Editor for the output file (if it exists), else the base network file, else a chosen file."""
        from .editor_gui import open_editor
        cands = [self.v_out.get().strip(), self.v_base.get().strip()]
        path = next((p for p in cands if p and p.lower().endswith(".arxml") and os.path.isfile(p)), None)
        if path is None:
            path = filedialog.askopenfilename(parent=self.win, title="Network file with a gateway",
                                              filetypes=[("AUTOSAR XML", "*.arxml"), ("All files", "*.*")])
            if not path:
                return
        open_editor(self.win, path)

    # ------------------------------------------------------------------ suggestions
    def suggest_eth(self):
        """Fill the empty Ethernet settings with suggested values (and tell why)."""
        if not self.v_base.get().strip() and not self.cfg.buses:
            messagebox.showwarning(TITLE, "Select the network file / DaVinci project or add a DBC file first.",
                                   parent=self.win)
            return

        def run():
            cfg = self.collect()
            filled = self.fill_from_nodes(cfg)          # the node table first, then the suggestions
            try:
                sugg = suggest_settings(cfg, self.base)
            except Exception as exc:  # noqa: BLE001 - shown to the user
                self.show_messages(errors=[f"No suggestion possible: {exc}"])
                return
            applied = apply_suggestions(cfg, sugg)
            e = cfg.ethernet
            self.c_chan.set(NEW_VALUE if e.new_channel else e.channel)
            self.v_vlan.set("" if e.vlan_id is None else e.vlan_id)
            self.v_ecuip.set(e.ecu_ip)
            self.v_mask.set(e.ecu_netmask or "255.255.255.0")
            self.v_mac.set(e.mac)
            self.channel_changed()          # refreshes connector, endpoint and both socket frames from cfg
            kept = [s for s in sugg if s not in applied]
            self.show_messages(infos=filled + [f"Suggested {s}" for s in applied] +
                               [f"Kept your value for {s.field} (suggestion: {s.value})" for s in kept]
                               or ["Nothing to suggest: all Ethernet settings are filled in."])
            self.status.config(text=f"{len(applied)} Ethernet setting(s) suggested - check them, then Analyze.")
        self.load_base(then=run)

    # ------------------------------------------------------------------ buses
    def refresh_buses(self):
        self.t_bus.delete(*self.t_bus.get_children())
        for i, b in enumerate(self.cfg.buses):
            if b.remote_ecu:
                self.t_bus.insert("", "end", iid=str(i), values=(
                    os.path.basename(b.dbc), f"{b.node}  (other ECU)", "- (bus of another ECU)", "-",
                    f"to / from {b.remote_ecu} over Ethernet"))
                continue
            dirs = ", ".join(x for x, on in (("CAN->ETH", b.rx), ("ETH->CAN", b.tx)) if on)
            self.t_bus.insert("", "end", iid=str(i), values=(
                os.path.basename(b.dbc) if b.dbc else "(DaVinci project)", b.node or "(project ECU)",
                b.channel or NEW_CLUSTER, b.bus or "(auto)", dirs))

    def target_node(self, skip=None) -> str:
        """Gateway node of the ECU being generated: the node of most of its own (not other ECUs') DBC buses."""
        import collections as _c
        nodes = _c.Counter(b.node for i, b in enumerate(self.cfg.buses)
                           if i != skip and b.dbc and b.node and not b.remote_ecu)
        return nodes.most_common(1)[0][0] if nodes else ""

    # ------------------------------------------------------------------ Ethernet nodes (default values)
    def _one_socket_changed(self):
        one = self.v_onesock.get()
        self.side_tx.config(text="Socket  (gateway ECU sends and receives on Ethernet)" if one else
                            "CAN -> ETH  (gateway ECU sends on Ethernet)")
        self.side_rx.config(text="ETH -> CAN  (same socket as CAN -> ETH)" if one else
                            "ETH -> CAN  (gateway ECU receives from Ethernet)")
        set_enabled(self.side_rx, not one)
        if not one:
            self.side_rx.refresh()

    def _show_eth_fields(self):
        e = self.cfg.ethernet
        self.v_ecuip.set(e.ecu_ip)
        self.v_mac.set(e.mac)
        self.side_tx.load(e.can_to_eth)
        self.side_rx.load(e.eth_to_can)
        self._one_socket_changed()
        self.refresh_peers()

    def fill_from_nodes(self, cfg: GatewayConfig) -> list[str]:
        """Fill the empty Ethernet fields from the Ethernet node table; returns info lines."""
        from . import nodes as nodemod
        try:
            done = nodemod.fill_gateway(cfg, nodemod.load())
        except (OSError, ValueError):
            return []
        if done:
            self._show_eth_fields()
        return [f"Filled from Ethernet nodes: {x}" for x in done]

    def edit_nodes(self):
        d = NodesDialog(self.win)
        self.win.wait_window(d)
        if d.result is not None:
            filled = self.fill_from_nodes(self.collect())
            self.show_messages(infos=filled or ["Ethernet nodes saved (no empty field to fill)."])

    def ensure_peer(self, name: str):
        """A bus of another ECU needs that ECU as Ethernet peer (its IP address and ports, same VLAN)."""
        if name in self.peer_names():
            return
        e = self.cfg.ethernet
        self.side_tx.store(e.can_to_eth)
        self.side_rx.store(e.eth_to_can)
        messagebox.showinfo(TITLE, f"{name} is another ECU: messages go to / come from it over Ethernet. Enter its "
                                   f"IP address and ports (on the Ethernet channel of the default node).",
                            parent=self.win)
        def port(s):
            # same ports on every node: those of the default node (also when it uses an existing socket)
            if s.remote_port is not None or not s.remote_socket:
                return s.remote_port
            ch = self.channel()
            return next((x.port for x in (ch.sockets if ch else []) if x.path == s.remote_socket), None)
        from . import nodes as nodemod
        node = nodemod.find(nodemod.load(), name)
        if node is not None:                    # its address and port base from the Ethernet node table
            tx, rx = nodemod.ports(node, e.one_socket)
            new = EthPeer(name=name, can_to_eth=SocketSide(remote_ip=node.ip, remote_port=rx),
                          eth_to_can=SocketSide(remote_ip=node.ip, remote_port=tx))
        else:
            new = EthPeer(name=name, can_to_eth=SocketSide(remote_port=port(e.can_to_eth)),
                          eth_to_can=SocketSide(remote_port=port(e.eth_to_can)))
        d = PeerDialog(self.win, self, new, taken=self.peer_names())
        self.win.wait_window(d)
        if d.result is not None:
            e.peers.append(d.result)
            self.refresh_peers()

    def add_bus(self):
        d = BusDialog(self.win, BusInput(), self.base, self.dbc_cache, self.project_ecu(), self.target_node(),
                      self.table_networks())
        self.win.wait_window(d)
        if d.result:
            self.cfg.buses.append(d.result)
            self.refresh_buses()
            if d.result.remote_ecu:
                self.ensure_peer(d.result.remote_ecu)
                return
            if not self.v_base.get().strip():
                if not self.v_out.get().strip():
                    db = self.dbc_cache.get(os.path.abspath(d.result.dbc))
                    stem = db.name if db else os.path.splitext(os.path.basename(d.result.dbc))[0]
                    self.v_out.set(os.path.join(os.path.dirname(d.result.dbc), f"{stem}_network.arxml"))
                self.load_base()

    def edit_bus(self):
        sel = self.t_bus.selection()
        if not sel:
            return
        i = int(sel[0])
        d = BusDialog(self.win, self.cfg.buses[i], self.base, self.dbc_cache, self.project_ecu(),
                      self.target_node(skip=i), self.table_networks())
        self.win.wait_window(d)
        self.refresh_buses()
        if d.result is not None and d.result.remote_ecu:
            self.ensure_peer(d.result.remote_ecu)

    def remove_bus(self):
        sel = self.t_bus.selection()
        if sel:
            del self.cfg.buses[int(sel[0])]
            self.refresh_buses()

    # ------------------------------------------------------------------ config <-> widgets
    def collect(self, into: GatewayConfig | None = None) -> GatewayConfig:
        """Widgets -> configuration (self.cfg, or a copy given as *into*)."""
        c = into if into is not None else self.cfg
        c.base = self.v_base.get().strip()
        c.output = self.v_out.get().strip()
        c.ecu = self.c_ecu.get()
        c.schema = self.v_schema.get() or DEFAULT_SCHEMA
        e = c.ethernet
        chan = self.c_chan.get()
        e.new_channel = chan == NEW_VALUE
        e.channel = "" if e.new_channel else chan
        e.connector = self.c_conn.get()
        e.cluster = self.c_cluster.get()
        e.channel_name = self.v_chname.get().strip()
        e.vlan_id = _int_or_none(self.v_vlan.get())
        e.ecu_ip = self.v_ecuip.get().strip()
        e.ecu_netmask = self.v_mask.get().strip() or "255.255.255.0"
        e.controller = self.c_ctrl.get()
        e.mac = self.v_mac.get().strip()
        e.local_endpoint = self.c_nep.get()
        e.protocol = self.v_proto.get()
        e.tcp_role = self.v_role.get()
        e.id_set = self.c_idset.get()
        self.side_tx.store(e.can_to_eth)
        self.side_rx.store(e.eth_to_can)
        e.default_peer = self.v_defpeer.get().strip()
        e.one_socket = self.v_onesock.get()
        e.collection = PduCollection(
            enabled=self.v_col_on.get(), timeout_ms=_float_or(self.v_col_timeout.get(), 5),
            buffer=_int_or_none(self.v_col_buffer.get()) or 1400, mode=self.v_col_mode.get(),
            immediate_cycle_ms=_int_or_none(self.v_col_cycle.get()) or 20)
        c.header.extended_flag = self.v_extflag.get()
        c.options.eth_signals = self.v_sigs.get()
        c.options.can_tx_timing = self.v_timing.get()
        c.options.add_fibex = self.v_fibex.get()
        c.options.only_previous = self.v_onlyprev.get()
        c.options.eth_routes = self.v_ethroutes.get()
        c.options.can_routes = self.v_canroutes.get()
        c.options.can_match_id = self.v_canmatchid.get()
        c.options.eth_fanout = self.v_fanout.get()
        c.options.eth_no_com = self.v_nocom.get()
        c.options.table_hw = self.v_table_hw.get()
        c.routing_table = self.v_table.get().strip()
        c.previous = self.v_prev.get().strip()
        for k, v in self.v_naming.items():
            setattr(c.naming, k, v.get().strip() or getattr(Naming(), k))
        return c

    def show_config(self, then=None):
        c = self.cfg
        self.v_base.set(c.base)
        self.v_out.set(c.output)
        self.v_proto.set(c.ethernet.protocol or "UDP")
        self.v_schema.set(c.schema or DEFAULT_SCHEMA)
        self.v_chname.set(c.ethernet.channel_name)
        self.v_vlan.set("" if c.ethernet.vlan_id is None else c.ethernet.vlan_id)
        self.v_ecuip.set(c.ethernet.ecu_ip)
        self.v_mask.set(c.ethernet.ecu_netmask or "255.255.255.0")
        self.v_mac.set(c.ethernet.mac)
        self.v_role.set(c.ethernet.tcp_role or "CONNECT")
        self.v_defpeer.set(c.ethernet.default_peer)
        self.v_onesock.set(c.ethernet.one_socket)
        self._one_socket_changed()
        col = c.ethernet.collection
        self.v_col_on.set(col.enabled)
        self.v_col_timeout.set(f"{col.timeout_ms:g}")
        self.v_col_buffer.set(str(col.buffer))
        self.v_col_mode.set(col.mode if col.mode in ("all", "cycle") else "all")
        self.v_col_cycle.set(str(col.immediate_cycle_ms))
        self.refresh_peers()
        self.v_extflag.set(c.header.extended_flag)
        self.v_sigs.set(c.options.eth_signals)
        self.v_timing.set(c.options.can_tx_timing)
        self.v_fibex.set(c.options.add_fibex)
        self.v_onlyprev.set(c.options.only_previous)
        self.v_ethroutes.set(c.options.eth_routes)
        self.v_canroutes.set(c.options.can_routes)
        self.v_canmatchid.set(c.options.can_match_id)
        self.v_fanout.set(c.options.eth_fanout)
        self.v_nocom.set(c.options.eth_no_com)
        self.v_table_hw.set(c.options.table_hw)
        self.v_table.set(c.routing_table)
        self.v_prev.set(c.previous)
        for k, v in self.v_naming.items():
            v.set(getattr(c.naming, k))
        self.refresh_buses()
        self.c_ecu.var.set(c.ecu)
        self._base_key = None
        self.load_base(then=then)

    def new_config(self):
        self.cfg, self.cfg_path, self.plan = GatewayConfig(), None, None
        self.cfg.ethernet.one_socket = True         # new configuration: one socket for both directions
        self.show_config()
        self.fill_routes()

    def open_config_dialog(self):
        p = filedialog.askopenfilename(
            parent=self.win, title="Gateway configuration or generated gateway file",
            filetypes=[("Gateway configuration / generated gateway", "*.json *.arxml"),
                       ("Gateway configuration", "*.json"), ("Generated gateway file", "*.arxml"),
                       ("All files", "*.*")])
        if p:
            self.open_config(p)

    def open_config(self, path):
        notes = []
        try:
            if path.lower().endswith(".arxml"):
                self.cfg, notes = config_from_file(path)
                self.cfg_path = None
                self.win.title(f"{TITLE} - {os.path.basename(path)} (regenerate)")
            else:
                self.cfg = GatewayConfig.load(path)
                self.cfg_path = os.path.abspath(path)
                self.win.title(f"{TITLE} - {os.path.basename(path)}")
        except Exception as exc:  # noqa: BLE001 - shown to the user
            messagebox.showerror(TITLE, f"Cannot read {path}:\n{exc}", parent=self.win)
            return
        self.plan = None
        self.fill_routes()
        if notes:
            self.show_messages(infos=notes)
        self.show_config(then=self.analyze if self.cfg.previous and self.cfg.base else None)

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
        if not self.v_base.get().strip() and not self.cfg.buses:
            messagebox.showwarning(TITLE, "Add a DBC file (and select the base system description if the project "
                                          "already has one).", parent=self.win)
            return

        def run_plan():
            cfg = self.collect()        # now the widgets show the loaded base and the configuration
            filled = self.fill_from_nodes(cfg)

            def done(plan):
                self.plan = plan
                self._done_text = ""
                self.fill_routes()
                self.show_messages(plan.errors, plan.warnings, filled + plan.infos)
                n = report.count_text(len(plan.enabled_routes), len(plan.enabled_can_routes),
                                      len(plan.enabled_signal_routes))
                self.status.config(text=f"{n}, {len(plan.warnings)} warning(s), {len(plan.errors)} error(s)")
                if then and plan.ok:
                    then(plan)
            self._run("Analyzing", lambda: make_plan(cfg, self.base, self.dbc_cache), done)
        self.load_base(then=run_plan)

    def generate(self):
        out, base_path = self.v_out.get().strip(), self.v_base.get().strip()
        if not out:
            messagebox.showwarning(TITLE, "Select the output file.", parent=self.win)
            return
        if os.path.abspath(out) == os.path.abspath(base_path or "") and not messagebox.askyesno(
                TITLE, "The output file is the base file itself. Overwrite it (a .bak copy is kept)?",
                parent=self.win):
            return

        def write(plan):
            cfg = plan.cfg

            def work():
                res = generate(plan, load_base(cfg))
                csv_path = os.path.splitext(res.output)[0] + "_gateway_routes.csv"
                report.write_csv(plan, csv_path)
                self._report_files = paths.write_report(paths.report_of_plan(plan), os.path.splitext(res.output)[0])
                return res, csv_path

            def done(r):
                res, csv_path = r
                ext = ""
                if res.extension:
                    ncan = sum(1 for c in res.can_routes if c.vsde)
                    ntx = sum(1 for r in res.routes if r.no_com)
                    nsig = len(res.signal_routes)
                    ext = (f"{os.path.basename(res.extension)}: CAN -> CAN {ncan} route(s) in PduR only, ETH -> CAN "
                           f"{ntx} CAN PDU(s) not sent by Com" + (f", {nsig} signal route(s) by Com (signal gateway: "
                                                                   f"set Com/ComGeneral/ComSignalGateway once)"
                                                                   if nsig else "") +
                           ". Add it to the Input Files of the DaVinci project next to the DBC files (once), then "
                           "run Update. The DBC converter then logs 'ECU ... does not receive source pdu ...' for "
                           "the ETH -> CAN PDUs: expected.")
                self.show_messages([], res.warnings, [f"Written {res.output}"] +
                                   ([f"Written {res.extension}", ext] if ext else []) +
                                   [f"Route table: {csv_path}", f"Message paths: {self._report_files[0]}"])
                n = report.count_text(len(res.routes), len(res.can_routes), len(res.signal_routes))
                self.status.config(text=f"Written {os.path.basename(res.output)}: {n}")
                regen = cfg.previous and os.path.abspath(cfg.previous) == os.path.abspath(res.output)
                done_routes = plan.enabled_routes + plan.enabled_can_routes
                kept = sum(1 for r in done_routes if r.change == "kept")
                new = sum(1 for r in done_routes if r.change == "new")
                if regen:
                    how = (f"again: it is already in Input Files, so run Update in DaVinci. Kept {kept}, new {new}, "
                           f"removed {len(plan.removed)} route(s); the configuration of the kept routes stays")
                elif dvproject.is_project(cfg.base):
                    how = ("as an additional system description for the ECU instance next to the DBC files, "
                           "then run Update")
                elif cfg.base:
                    how = "instead of the base file"
                else:
                    how = f"for the ECU instance {new_ecu_name(cfg)} (do not import the same DBC files again)"
                self._done_text = (f"Written {os.path.basename(res.output)} ({n}). In DaVinci Configurator: "
                                   + (f"run Update (the file is already in Input Files)." if regen else
                                      f"import it (Input Files) {how}.") + (f" {ext}" if ext else ""))
                self.update_hint()
                messagebox.showinfo(TITLE, f"Written {res.output}\n\n{n}.\n"
                                           f"Import this file into DaVinci Configurator (Input Files) {how}." +
                                           (f"\n\n{ext}" if ext else ""),
                                    parent=self.win)
            self._run("Generating", work, done)
        self.analyze(then=write)

    # ------------------------------------------------------------------ message path report
    def message_report(self):
        """Message path report of the current plan (analyzed first), opened in the browser."""
        def write(plan):
            stem = os.path.splitext(plan.cfg.output or os.path.join(os.getcwd(), "gateway"))[0]
            try:
                files = paths.write_report(paths.report_of_plan(plan), stem)
            except OSError as exc:
                messagebox.showerror(TITLE, f"Cannot write the report:\n{exc}", parent=self.win)
                return
            self.show_messages(infos=[f"Message paths: {files[0]}", f"Message paths (CSV): {files[1]}"])
            if hasattr(os, "startfile"):
                os.startfile(files[0])
        self.analyze(then=write)

    def capl_test(self):
        """CANoe CAPL node(s) sending the Ethernet PDUs of the ETH -> CAN routes (analyzed first)."""
        def write(plan):
            from . import capl
            stem = os.path.splitext(plan.cfg.output or os.path.join(os.getcwd(), "gateway"))[0]
            try:
                files, problems = capl.write_eth_to_can(plan, stem, self.base)
            except OSError as exc:
                messagebox.showerror(TITLE, f"Cannot write the CAPL file:\n{exc}", parent=self.win)
                return
            if not files:
                messagebox.showinfo(TITLE, "There is no enabled ETH -> CAN route.", parent=self.win)
                return
            self.show_messages([], problems, [f"CAPL test node: {f}" for f in files] +
                               ["Add it as a simulation node on the Ethernet network in CANoe (TCP/IP stack of the "
                                "node = the address of the Ethernet node it plays), start the measurement and press "
                                "'a' (send all), 'n' (next), 'c' (cyclic), 'p' (counter payload), 'l' (list)."])
            messagebox.showinfo(TITLE, "Written:\n" + "\n".join(files) + "\n\nSee the file header for the CANoe "
                                "setup and the keys.", parent=self.win)
        self.analyze(then=write)

    # ------------------------------------------------------------------ guided start / next step
    def start_wizard(self):
        from .wizard import StartWizard

        def topology():
            from .topology.gui import open_topology
            open_topology(self.win)
        def topology_file(path, target=None):
            from .topology.gui import open_topology
            open_topology(self.win, path, select=target)
        StartWizard(self.win, self.apply_start, on_topology=topology, on_open=self.open_config_dialog,
                    dbc_cache=self.dbc_cache, on_topology_file=topology_file, on_open_gateway=self.open_config)

    def apply_start(self, cfg: GatewayConfig, case: str, notes=()):
        self.cfg, self.cfg_path, self.plan, self._done_text = cfg, None, None, ""
        regen = case == "update"
        if not regen:
            cfg.ethernet.one_socket = True          # new gateway: one socket for both directions
        name = os.path.basename(cfg.output) if cfg.output else "new"
        self.win.title(f"{TITLE} - {name}" + (" (regenerate)" if regen else ""))
        self.fill_routes()
        self.show_messages(infos=list(notes))
        self.show_config(then=self.analyze)

    def _missing_ethernet(self, c: GatewayConfig) -> str:
        if not c.options.eth_routes:
            return ""
        miss = []
        p = self.plan
        # after an analysis only the directions that really use the default node need its sockets
        used = ({r.direction for r in p.enabled_routes if p.default_peer in r.peers} if p is not None and not p.errors
                else {"CAN->ETH", "ETH->CAN"})
        sides = (("CAN -> ETH", c.ethernet.can_to_eth), ("ETH -> CAN", c.ethernet.eth_to_can))
        if c.ethernet.one_socket:                   # one socket: the CAN -> ETH settings serve both directions
            sides = (("CAN -> ETH", c.ethernet.can_to_eth),) if used else ()
            used = {"CAN->ETH"}
        for label, s in sides:
            if label.replace(" ", "") not in used:
                continue
            if not s.local_socket and s.local_port is None:
                miss.append(f"the ECU port for {label}")
            if not s.remote_socket and not ((s.remote_ip or s.remote_endpoint) and s.remote_port is not None):
                miss.append(f"the IP address / port of the other node for {label}")
        if not c.base and not c.ethernet.ecu_ip and not c.ethernet.local_endpoint:
            miss.insert(0, "the IP address of the ECU")
        return ", ".join(miss)

    def next_step(self, c: GatewayConfig | None = None):
        """(text, [(button, command)]) of the next thing to do."""
        c, p = c or self.cfg, self.plan
        if self._done_text:
            folder = os.path.dirname(os.path.abspath(c.output)) if c.output else ""
            return self._done_text, ([("Open folder", lambda: os.startfile(folder))] if folder and
                                     hasattr(os, "startfile") else [])
        if not c.base and not c.buses:
            return ("Start here: tell the tool what you have (only DBC files, a DaVinci project with the DBC "
                    "files, or a gateway file to update).", [("Start…", self.start_wizard)])
        if self._project_gw:
            gw = self._project_gw
            return (f"This project already contains the gateway file {os.path.basename(gw)} made by this tool. To "
                    f"change it, update that file instead of making a second one.",
                    [("Update it", lambda: (setattr(self, "_project_gw", ""), self.open_config(gw))),
                     ("Ignore", lambda: (setattr(self, "_project_gw", ""), self.update_hint()))])
        if not c.buses:
            return ("Add the CAN buses: DBC files, or the CAN channels of the project (Input tab).",
                    [("Add bus…", self.add_bus)])
        if not c.output:
            return "Select the output file (Input tab).", [("Browse…", self.browse_out)]
        miss = self._missing_ethernet(c)
        if miss:
            return (f"Ethernet settings missing: {miss}.",
                    [("Suggest values", self.suggest_eth), ("Ethernet tab", lambda: self.nb.select(1))])
        if p is None:
            return "Everything needed is filled in: Analyze shows the routes before anything is written.", [
                ("Analyze", self.analyze)]
        if p.errors:
            return (f"{len(p.errors)} error(s) to fix (see the messages under the table), then Analyze again.",
                    [("Analyze", self.analyze)])
        if not p.enabled_routes and not p.enabled_can_routes:
            return ("No message is routed: check the gateway node / channels of the buses and the Options tab.",
                    [("Input tab", lambda: self.nb.select(0))])
        n = report.count_text(len(p.enabled_routes), len(p.enabled_can_routes), len(p.enabled_signal_routes))
        if p.previous:
            every = p.enabled_routes + p.enabled_can_routes
            kept = sum(1 for r in every if r.change == "kept")
            new = sum(1 for r in every if r.change == "new")
            n += f": {kept} kept, {new} new, {len(p.removed)} removed"
        warn = f", {len(p.warnings)} warning(s) to read" if p.warnings else ""
        return f"Ready: {n}{warn}. Generate writes {os.path.basename(c.output)}.", [("Generate", self.generate)]

    def update_hint(self):
        try:
            snap = copy.deepcopy(self.cfg)
            if not self._busy:
                self.collect(into=snap)          # a copy: never changes the configuration itself
            text, buttons = self.next_step(snap)
        except Exception:  # noqa: BLE001 - the hint must never break the window
            return
        state = (text, tuple(b for b, _ in buttons))
        if state == self._hint_state:
            return
        self._hint_state = state
        self.hint_text.config(text=text, wraplength=max(self.win.winfo_width() - 360, 400))
        for w in self.hint_buttons.winfo_children():
            w.destroy()
        for label, cmd in buttons:
            ttk.Button(self.hint_buttons, text=label, command=cmd).pack(side="left", padx=2)

    def _hint_tick(self):
        if self.win.winfo_exists():
            self.update_hint()
            self.win.after(800, self._hint_tick)

    def fill_routes(self):
        t = self.t_routes
        # keep the selection and the scroll position over a new analysis (rows are found again by their key)
        keep = {getattr(r, "key", None) for r in self._selected_routes()} - {None}
        top = t.yview()[0]
        t.delete(*t.get_children())
        if not self.plan:
            return
        self._route_by_iid = {}
        items = report.route_items(self.plan)
        removed = [row for r, row in items if not isinstance(r, (Route, CanRoute, SignalRoute))]
        for k, row in enumerate(removed):
            t.insert("", "end", iid=f"removed-{k}", values=row, tags=("removed",))
        again = []
        for i, (r, row) in enumerate((r, row) for r, row in items if isinstance(r, (Route, CanRoute, SignalRoute))):
            if not r.enabled:
                tags = ("off",)
            elif isinstance(r, Route) and r.header_note.startswith("flag"):
                tags = ("flag",)
            else:
                tags = ("new",) if r.change == "new" else ()
            t.insert("", "end", iid=str(i), values=row, tags=tags)
            self._route_by_iid[str(i)] = r
            if r.key in keep:
                again.append(str(i))
        if again:
            t.selection_set(again)
        t.yview_moveto(top)

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
        if isinstance(r, (CanRoute, SignalRoute)):
            if isinstance(r, SignalRoute):
                text = (f"{r.src.bus.name} / {r.src.message.name}.{r.src_signal.name}  ->  {r.dst.bus.name} / "
                        f"{r.dst.message.name}.{r.dst_signal.name}\nRouting table {r.row}\n" +
                        "".join(f"{x}\n" for x in ([r.reason] if r.reason else []) + r.notes) +
                        "\nRoute this signal (Com signal gateway)?")
            else:
                text = (f"{r.src.bus.name} / {r.src.message.name}  ->  {r.dst.bus.name} / {r.dst.message.name}\n"
                        f"Pairing: {r.match}\n" + "".join(f"{x}\n" for x in ([r.reason] if r.reason else []) +
                                                          r.notes) +
                        "\nRoute this message from bus to bus?")
            ans = messagebox.askyesnocancel(TITLE, text, parent=self.win)
            if ans is not None:
                self.cfg.can_gateway[r.key] = {"enabled": ans}
                self.analyze()
            return
        d = RouteDialog(self.win, r, self.peer_names())
        self.win.wait_window(d)
        if d.result is not None:
            old = r.bus.cfg.messages.get(r.message.name, {}) or {}
            for key in ("fanout", "eth_send"):          # set in other dialogs / menus, not in this one
                if key in old:
                    d.result[key] = old[key]
            r.bus.cfg.messages[r.message.name] = d.result
            self.analyze()

    def toggle_routes(self, value=None):
        routes = self._selected_routes()
        for r in routes:
            if isinstance(r, (CanRoute, SignalRoute)):
                self.cfg.can_gateway[r.key] = {"enabled": (not r.enabled) if value is None else value}
                continue
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
        if self.cfg.ethernet.peers:
            m.add_command(label="Ethernet peers…", command=self.set_peers)
        if self._fanout_routes():
            m.add_command(label="1:N with the same CAN id…", command=self.set_fanout)
        if self.cfg.ethernet.collection.enabled and any(
                isinstance(r, Route) and r.direction == CAN_TO_ETH for r in self._selected_routes()):
            sm = tk.Menu(m, tearoff=False)
            sm.add_command(label="Collect (wait for the collection timeout)", command=lambda: self.set_eth_send("collect"))
            sm.add_command(label="Send immediately (flush the datagram)", command=lambda: self.set_eth_send("immediate"))
            sm.add_command(label="Automatic (collection settings)", command=lambda: self.set_eth_send(None))
            m.add_cascade(label="ETH send (PDU collection)", menu=sm)
        m.add_separator()
        m.add_command(label="Add CAN -> CAN link…", command=self.add_link)
        sel = self._selected_routes()
        if any(isinstance(r, CanRoute) and r.match == "link" for r in sel):
            m.add_command(label="Remove CAN -> CAN link", command=self.remove_links)
        m.add_separator()
        m.add_command(label="Reset overrides of selected", command=self.reset_routes)
        m.tk_popup(e.x_root, e.y_root)

    def set_eth_send(self, value):
        """PDU collection of the selected CAN -> ETH messages: 'collect', 'immediate' or None (automatic)."""
        for r in self._selected_routes():
            if not isinstance(r, Route) or r.direction != CAN_TO_ETH:
                continue
            over = dict(r.bus.cfg.messages.get(r.message.name, {}))
            over.pop("eth_send", None)
            if value:
                over["eth_send"] = value
            r.bus.cfg.messages[r.message.name] = over
        self.analyze()

    def _fanout_routes(self) -> list:
        """Enabled ETH -> CAN routes of the CAN id of the selected route on other buses (with it), or []."""
        sel = [r for r in self._selected_routes() if isinstance(r, Route) and r.direction == ETH_TO_CAN]
        if not sel or not self.plan:
            return []
        m = sel[0].message
        same = [r for r in self.plan.enabled_routes if r.direction == ETH_TO_CAN and not r.can_problem
                and (r.message.can_id, r.message.extended) == (m.can_id, m.extended)]
        return same if len({r.bus.name for r in same}) > 1 else []

    def set_fanout(self):
        routes = self._fanout_routes()
        if not routes:
            return
        cur = {(r.bus.cfg.messages.get(r.message.name, {}) or {}).get("fanout") for r in routes}
        d = FanoutDialog(self.win, routes, cur.pop() if len(cur) == 1 else None)
        self.win.wait_window(d)
        if not d.done:
            return
        for r in routes:
            over = dict(r.bus.cfg.messages.get(r.message.name, {}))
            over.pop("fanout", None)
            if d.result is not None:
                over["fanout"] = d.result
            r.bus.cfg.messages[r.message.name] = over
        self.analyze()

    def reset_routes(self):
        for r in self._selected_routes():
            if isinstance(r, (CanRoute, SignalRoute)):
                self.cfg.can_gateway.pop(r.key, None)
            else:
                r.bus.cfg.messages.pop(r.message.name, None)
        self.analyze()

    def set_peers(self):
        routes = [r for r in self._selected_routes() if isinstance(r, Route)]
        dirs = {r.direction for r in routes}
        if len(dirs) != 1:
            if routes:
                messagebox.showwarning(TITLE, "Select routes of one direction (CAN->ETH or ETH->CAN).",
                                       parent=self.win)
            return
        multi = dirs == {CAN_TO_ETH}
        names = self.peer_names()
        current = routes[0].peers if routes[0].peers else names[:1]
        d = PeerPickDialog(self.win, names, multi, current)
        self.win.wait_window(d)
        if d.result is None:
            return
        for r in routes:
            over = dict(r.bus.cfg.messages.get(r.message.name, {}))
            over.pop("eth_peers", None)
            over.pop("eth_peer", None)
            if d.result != names[:1]:
                if multi:
                    over["eth_peers"] = d.result
                else:
                    over["eth_peer"] = d.result[0]
            r.bus.cfg.messages[r.message.name] = over
        self.analyze()

    def add_link(self):
        if not self.plan:
            return
        d = LinkDialog(self.win, self.plan, [r for r in self._selected_routes() if isinstance(r, Route)])
        self.win.wait_window(d)
        if d.result is not None:
            self.cfg.can_links = [x for x in self.cfg.can_links
                                  if (x.get("dst_bus"), x.get("dst_msg")) != (d.result["dst_bus"], d.result["dst_msg"])]
            self.cfg.can_links.append(d.result)
            self.analyze()

    def remove_links(self):
        dead = {(r.dst.bus.name, r.dst.message.name) for r in self._selected_routes()
                if isinstance(r, CanRoute) and r.match == "link"}
        self.cfg.can_links = [x for x in self.cfg.can_links if (x.get("dst_bus"), x.get("dst_msg")) not in dead]
        self.analyze()


def open_window(master: tk.Misc | None = None, config_path: str | None = None, wizard: bool = True):
    """Open the generator in a Toplevel of *master* (EcucStudio) or in its own root window. Without a configuration
    the start wizard opens on top of it."""
    from ..gui.startup import bring_to_front, show_main_window
    if master is None:
        root = tk.Tk()
        init_style(root)
        root.gateway = GatewayWindow(root, config_path)
        show_main_window(root, TITLE, None, "1400x900")
        win = root
    else:
        win = tk.Toplevel(master)
        win.geometry("1400x900")
        win.gateway = GatewayWindow(win, config_path)
        bring_to_front(win)
    if wizard and not config_path:
        win.after(400, win.gateway.start_wizard)
    return win


def main(config_path: str | None = None):
    root = open_window(None, config_path)
    root.mainloop()
