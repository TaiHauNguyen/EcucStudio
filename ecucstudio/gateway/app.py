"""Main window of the CAN gateway generator for a zonal Ethernet network.

One page per step, in the order of the work:

    1 Network        the Ethernet nodes: the central computer (HPC) and the zonal ECUs (MAC, IPv4, port)
    2 CAN buses      the DBC files: the gateway node in each and the zonal ECU it belongs to
    3 Routing table  the routing table (Excel) of the customer and what it means for this network
    4 Generate       the ECU to generate: its gateway file (+ .vsde), the message report

The work is saved as a network file (topology JSON, gateway/zonal.py describes the model and its rules). The other
windows (one-ECU generator, topology editor, gateway editor) are in the Advanced menu.
"""
from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, ttk

from ..gui.theme import COLORS, init_style
from ..gui.widgets import Tooltip, dialog_header
from . import dbcread, nodes, report, routing_table, start, zonal
from .planner import CAN_TO_ETH, CanRoute, Route, SignalRoute
from .topology import TopologyConfig, generate_topology, make_topology_plan

TITLE = "CAN Gateway"
RECENT_KEY = "gateway_network_recent"
_HINT_BG = "#e8f0fb"


def _int(text):
    try:
        return int(str(text).strip())
    except ValueError:
        return None


def _settings():
    from ..settings import Settings
    return Settings()


# ---------------------------------------------------------------------------- dialogs
class NodeDialog(tk.Toplevel):
    """Add / edit an Ethernet node."""

    def __init__(self, master, row: zonal.NodeRow | None):
        super().__init__(master)
        self.title("Ethernet node")
        self.transient(master)
        self.resizable(False, False)
        self.result = None
        dialog_header(self, "Ethernet node", "The central computer (HPC) gets every message the zonal ECUs receive "
                                             "on CAN and sends the messages they send on CAN, unless the routing "
                                             "table names another ECU. Zonal ECUs own the CAN buses (DBC files).")
        f = ttk.Frame(self, padding=12)
        f.pack(fill="both")
        row = row or zonal.NodeRow("", zonal.ZONAL, "", "", None)
        self.v = {k: tk.StringVar(value=x) for k, x in (("name", row.name), ("role", row.role), ("mac", row.mac),
                                                        ("ip", row.ip), ("port", "" if row.port is None else row.port))}
        ttk.Label(f, text="Name:").grid(row=0, column=0, sticky="w", pady=3)
        e = ttk.Entry(f, textvariable=self.v["name"], width=24)
        e.grid(row=0, column=1, sticky="w")
        ttk.Label(f, text="Role:").grid(row=1, column=0, sticky="w", pady=3)
        rf = ttk.Frame(f)
        rf.grid(row=1, column=1, sticky="w")
        for text in (zonal.ZONAL, zonal.HPC):
            ttk.Radiobutton(rf, text=text + (" (central computer)" if text == zonal.HPC else ""), value=text,
                            variable=self.v["role"]).pack(side="left", padx=(0, 12))
        for i, (k, label, hint) in enumerate((("mac", "MAC address:", "02:00:00:00:00:01"),
                                              ("ip", "IPv4 address:", "10.0.0.1"),
                                              ("port", "Port:", "UDP port of its gateway socket")), start=2):
            ttk.Label(f, text=label).grid(row=i, column=0, sticky="w", pady=3)
            ttk.Entry(f, textvariable=self.v[k], width=24).grid(row=i, column=1, sticky="w")
            ttk.Label(f, text=hint, foreground="#666666").grid(row=i, column=2, sticky="w", padx=8)
        b = ttk.Frame(self, padding=(12, 0, 12, 12))
        b.pack(fill="x")
        ttk.Button(b, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(b, text="OK", command=self.ok).pack(side="right", padx=6)
        self.bind("<Return>", lambda _e: self.ok())
        e.focus_set()
        self.grab_set()
        self.geometry("+%d+%d" % (master.winfo_rootx() + 120, master.winfo_rooty() + 120))

    def ok(self):
        port = _int(self.v["port"].get()) if self.v["port"].get().strip() else None
        if self.v["port"].get().strip() and (port is None or not 0 < port < 65536):
            messagebox.showwarning(TITLE, "The port must be a number 1 .. 65535.", parent=self)
            return
        self.result = zonal.NodeRow(self.v["name"].get().strip(), self.v["role"].get(), self.v["mac"].get().strip(),
                                    self.v["ip"].get().strip(), port)
        self.destroy()


class BusEditDialog(tk.Toplevel):
    """Gateway node, ECU and routing table column of one DBC file."""

    def __init__(self, master, path: str, db, bus, ecu: str, ecus: list[str], networks: list[str], auto_net: str):
        super().__init__(master)
        self.title("CAN bus")
        self.transient(master)
        self.resizable(False, False)
        self.result = None
        dialog_header(self, os.path.basename(path), "Choose the node that is the gateway ECU in this DBC file, the "
                                                    "zonal ECU it belongs to, and the column of the routing table "
                                                    "that is this bus.")
        f = ttk.Frame(self, padding=12)
        f.pack(fill="both")
        counts = start.node_counts(db) if db is not None else {}
        self.node_values = [n for n in (db.nodes if db is not None else []) if sum(counts.get(n, (0, 0)))]
        labels = [f"{n}   (receives {counts[n][0]}, sends {counts[n][1]})" for n in self.node_values]
        self.v_node = tk.StringVar()
        ttk.Label(f, text="Gateway node:").grid(row=0, column=0, sticky="w", pady=3)
        self.c_node = ttk.Combobox(f, textvariable=self.v_node, values=labels, width=44, state="readonly")
        self.c_node.grid(row=0, column=1, sticky="w")
        if bus.node in self.node_values:
            self.c_node.current(self.node_values.index(bus.node))
        ttk.Label(f, text="ECU:").grid(row=1, column=0, sticky="w", pady=3)
        self.v_ecu = tk.StringVar(value=ecu)
        ttk.Combobox(f, textvariable=self.v_ecu, values=[""] + ecus, width=24, state="readonly").grid(
            row=1, column=1, sticky="w")
        ttk.Label(f, text="Routing table column:").grid(row=2, column=0, sticky="w", pady=3)
        self.v_net = tk.StringVar(value=bus.table_network)
        ttk.Combobox(f, textvariable=self.v_net, values=[""] + networks, width=24, state="readonly").grid(
            row=2, column=1, sticky="w")
        ttk.Label(f, text=f"empty = {auto_net}" if auto_net else "empty = no column has this bus name",
                  foreground="#666666").grid(row=3, column=1, sticky="w")
        b = ttk.Frame(self, padding=(12, 0, 12, 12))
        b.pack(fill="x")
        ttk.Button(b, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(b, text="OK", command=self.ok).pack(side="right", padx=6)
        self.grab_set()
        self.geometry("+%d+%d" % (master.winfo_rootx() + 120, master.winfo_rooty() + 120))

    def ok(self):
        i = self.c_node.current()
        node = self.node_values[i] if i >= 0 else ""
        self.result = (node, self.v_ecu.get(), self.v_net.get())
        self.destroy()


# ---------------------------------------------------------------------------- main window
class GatewayApp:
    def __init__(self, win: tk.Misc, path: str | None = None):
        self.win = win
        self.cfg: TopologyConfig = zonal.new_network()
        self.path = ""
        self.dbc_cache: dict = {}
        self.bases: dict = {}
        self.table = None
        self._table_key = None
        self.tplan = None
        self._q: queue.Queue = queue.Queue()
        self._busy = False
        self._route_by_iid: dict = {}
        self._shown_target = ""        # ECU whose instance / output fields page 4 shows
        self._build()
        last = path or next((p for p in _settings().get(RECENT_KEY, []) if os.path.isfile(p)), "")
        if last:
            self.open(last)
        else:
            self.new()
        self.win.after(100, self._poll)

    # ------------------------------------------------------------------ layout
    def _build(self):
        w = self.win
        tb = ttk.Frame(w, padding=(8, 6))
        tb.pack(fill="x")
        for text, cmd in (("New", self.new_dialog), ("Open…", self.open_dialog), ("Save", self.save),
                          ("Save As…", self.save_as)):
            ttk.Button(tb, text=text, command=cmd).pack(side="left", padx=(0, 4))
        self.file_label = ttk.Label(tb, text="", foreground="#555555")
        self.file_label.pack(side="left", padx=12)
        adv = ttk.Menubutton(tb, text="Advanced")
        m = tk.Menu(adv, tearoff=False)
        m.add_command(label="Topology window (this network, every setting)…", command=self.open_topology_window)
        m.add_command(label="Generator for one ECU (classic)…", command=self.open_classic)
        m.add_command(label="Gateway Editor (edit a gateway file)…", command=self.open_editor)
        adv["menu"] = m
        adv.pack(side="right")
        self.hint = tk.Frame(w, background=_HINT_BG)
        self.hint.pack(fill="x", padx=8)
        tk.Label(self.hint, text="Next step:", background=_HINT_BG, font=("Segoe UI", 9, "bold")).pack(
            side="left", padx=(8, 4), pady=5)
        self.hint_text = tk.Label(self.hint, text="", background=_HINT_BG, anchor="w")
        self.hint_text.pack(side="left", fill="x", expand=True)
        self.hint_btn = ttk.Button(self.hint, text="Show", command=self._hint_go)
        self.hint_btn.pack(side="right", padx=6, pady=3)
        self.nb = ttk.Notebook(w)
        self.nb.pack(fill="both", expand=True, padx=8, pady=6)
        self._page_network()
        self._page_buses()
        self._page_table()
        self._page_generate()
        self.nb.bind("<<NotebookTabChanged>>", lambda _e: self._tab_changed())
        self.status = ttk.Label(w, text="", anchor="w", padding=(8, 2))
        self.status.pack(fill="x")

    def _tree(self, parent, cols, widths, height=10, stretch=None):
        holder = ttk.Frame(parent)
        t = ttk.Treeview(holder, columns=[c for c, _ in cols], show="headings", height=height, selectmode="extended")
        for (c, text), wd in zip(cols, widths):
            t.heading(c, text=text, anchor="w")
            t.column(c, width=wd, anchor="w", stretch=c == stretch)
        ys = ttk.Scrollbar(holder, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=ys.set)
        t.pack(side="left", fill="both", expand=True)
        ys.pack(side="left", fill="y")
        t.tag_configure("todo", foreground=COLORS["warning"])
        t.tag_configure("off", foreground="#9e9e9e")
        t.tag_configure("bad", foreground=COLORS["error"])
        t.tag_configure("hpc", font=("Segoe UI", 9, "bold"))
        return holder, t

    # ------------------------------------------------------------------ page 1: network
    def _page_network(self):
        f = ttk.Frame(self.nb, padding=10)
        self.nb.add(f, text="  1  Network  ")
        lf = ttk.LabelFrame(f, text="Ethernet nodes", padding=8)
        lf.pack(fill="both", expand=True)
        holder, self.t_nodes = self._tree(lf, (("name", "Name"), ("role", "Role"), ("mac", "MAC address"),
                                               ("ip", "IPv4 address"), ("port", "Port"), ("buses", "CAN buses")),
                                          (160, 200, 170, 140, 80, 400), height=8, stretch="buses")
        holder.pack(side="left", fill="both", expand=True)
        self.t_nodes.bind("<Double-1>", lambda _e: self.edit_node())
        bb = ttk.Frame(lf)
        bb.pack(side="left", fill="y", padx=(8, 0))
        for text, cmd in (("Add…", self.add_node), ("Edit…", self.edit_node), ("Remove", self.remove_node),
                          ("Set as HPC", self.set_hpc)):
            ttk.Button(bb, text=text, command=cmd).pack(fill="x", pady=(0, 3))
        ttk.Separator(bb).pack(fill="x", pady=6)
        b1 = ttk.Button(bb, text="Load my default nodes", command=self.load_default_nodes)
        b1.pack(fill="x", pady=(0, 3))
        Tooltip(b1, "Take the nodes (name, role, MAC, IPv4, port) saved with 'Save as my default nodes'. The DBC "
                    "files of ECUs that keep their name stay with them.")
        b2 = ttk.Button(bb, text="Save as my default nodes", command=self.save_default_nodes)
        b2.pack(fill="x")
        Tooltip(b2, "A new network starts with these nodes. Saved in your user settings, not in the network file.")
        nf = ttk.LabelFrame(f, text="Network settings", padding=8)
        nf.pack(fill="x", pady=(8, 0))
        self.v_vlan, self.v_mask = tk.StringVar(), tk.StringVar()
        self.v_one = tk.BooleanVar()
        ttk.Label(nf, text="VLAN id:").pack(side="left")
        e = ttk.Entry(nf, textvariable=self.v_vlan, width=6)
        e.pack(side="left", padx=(4, 16))
        Tooltip(e, "VLAN of the Ethernet channel (used when the ECU's project has no Ethernet channel yet); empty = "
                   "untagged")
        ttk.Label(nf, text="Netmask:").pack(side="left")
        ttk.Entry(nf, textvariable=self.v_mask, width=16).pack(side="left", padx=(4, 16))
        ttk.Checkbutton(nf, text="One socket per node for both directions", variable=self.v_one).pack(side="left")
        rules = ttk.LabelFrame(f, text="How the routes are made", padding=8)
        rules.pack(fill="x", pady=(8, 0))
        ttk.Label(rules, justify="left", foreground="#333333", text=(
            "•  CAN -> Ethernet: every message a zonal ECU receives on CAN is sent to the HPC. When the routing table "
            "routes it from a bus of this ECU to a bus of another ECU, it is also sent directly to that ECU.\n"
            "•  Ethernet -> CAN: a message a zonal ECU sends on CAN comes from the HPC, or from the other ECU when the "
            "routing table routes it from a bus of that ECU.\n"
            "•  CAN -> CAN: routing table rows between two buses of the same ECU (message: PduR, signal: Com signal "
            "gateway), written to the .vsde file next to the gateway file.")).pack(anchor="w")

    def fill_nodes(self):
        t = self.t_nodes
        sel = t.selection()
        t.delete(*t.get_children())
        for r in zonal.node_rows(self.cfg):
            e = self.cfg.ecu(r.name)
            buses = ", ".join(zonal.bus_name(b, self._db(b)) for b in e.gateway.buses) if e is not None else ""
            role = "HPC (central computer)" if r.role == zonal.HPC else r.role
            t.insert("", "end", iid=r.name, values=(r.name, role, r.mac, r.ip, "" if r.port is None else r.port,
                                                    buses or ("-" if r.role == zonal.HPC else "no DBC file yet")),
                     tags=("hpc",) if r.role == zonal.HPC else (("todo",) if not r.ip or r.port is None else ()))
        keep = [i for i in sel if t.exists(i)]
        if keep:
            t.selection_set(keep)

    def _selected_node(self) -> str:
        sel = self.t_nodes.selection()
        return sel[0] if sel else ""

    def add_node(self):
        d = NodeDialog(self.win, None)
        self.win.wait_window(d)
        if d.result is not None:
            err = zonal.set_node(self.cfg, "", d.result)
            if err:
                messagebox.showwarning(TITLE, err, parent=self.win)
            self.changed()

    def edit_node(self):
        name = self._selected_node()
        row = next((r for r in zonal.node_rows(self.cfg) if r.name == name), None)
        if row is None:
            return
        d = NodeDialog(self.win, row)
        self.win.wait_window(d)
        if d.result is not None:
            err = zonal.set_node(self.cfg, name, d.result)
            if err:
                messagebox.showwarning(TITLE, err, parent=self.win)
            self.changed()

    def remove_node(self):
        name = self._selected_node()
        if not name:
            return
        e = self.cfg.ecu(name)
        if e is not None and e.gateway.buses and not messagebox.askyesno(
                TITLE, f"Remove {name}? Its {len(e.gateway.buses)} DBC file(s) stay in the list without an ECU.",
                parent=self.win):
            return
        zonal.remove_node(self.cfg, name)
        self.changed()

    def set_hpc(self):
        name = self._selected_node()
        if not name:
            return
        e = self.cfg.ecu(name)
        if e is not None and e.gateway.buses and not messagebox.askyesno(
                TITLE, f"{name} has DBC files. The HPC has no CAN bus here: they stay in the list without an ECU. "
                       f"Continue?", parent=self.win):
            return
        zonal.make_hpc(self.cfg, name)
        self.changed()

    def load_default_nodes(self):
        table = nodes.load()
        if not table:
            messagebox.showinfo(TITLE, "No default nodes saved yet: enter the nodes, then 'Save as my default nodes'.",
                                parent=self.win)
            return
        new = zonal.new_network(table)
        for e in new.ecus:                              # DBC files of ECUs that keep their name stay with them
            old = self.cfg.ecu(e.name)
            if old is not None:
                e.gateway = old.gateway
        kept = {e.name for e in new.ecus}
        self.cfg.unassigned += [b for e in self.cfg.ecus if e.name not in kept for b in e.gateway.buses]
        self.cfg.ecus, self.cfg.peers, self.cfg.default_peer = new.ecus, new.peers, new.default_peer
        if self.cfg.target not in kept:
            self.cfg.target = ""
        self.changed()

    def save_default_nodes(self):
        nodes.save(zonal.default_nodes(self.cfg))
        self.status.config(text=f"Saved {len(zonal.node_rows(self.cfg))} node(s) as your default nodes.")

    # ------------------------------------------------------------------ page 2: CAN buses
    def _page_buses(self):
        f = ttk.Frame(self.nb, padding=10)
        self.nb.add(f, text="  2  CAN buses (DBC)  ")
        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Button(top, text="Add DBC files…", command=self.add_dbcs).pack(side="left")
        ttk.Button(top, text="Edit…", command=self.edit_dbc).pack(side="left", padx=4)
        ttk.Button(top, text="Remove", command=self.remove_dbcs).pack(side="left")
        ttk.Label(top, text="   Several files at once: hold Ctrl in the file dialog. Double-click a row to change its "
                            "gateway node, ECU or routing table column.", foreground="#666666").pack(side="left")
        holder, self.t_bus = self._tree(f, (("dbc", "DBC file"), ("bus", "Bus"), ("node", "Gateway node"),
                                            ("ecu", "ECU"), ("net", "Routing table column"), ("rx", "Receives"),
                                            ("tx", "Sends")), (320, 140, 180, 120, 170, 80, 80), height=14,
                                       stretch="dbc")
        holder.pack(fill="both", expand=True, pady=(8, 0))
        self.t_bus.bind("<Double-1>", lambda _e: self.edit_dbc())
        self.bus_summary = ttk.Label(f, text="", justify="left", wraplength=1200)
        self.bus_summary.pack(fill="x", pady=(8, 0))

    def _db(self, b):
        return self.dbc_cache.get(os.path.abspath(b.dbc)) if b.dbc else None

    def fill_buses(self):
        t = self.t_bus
        t.delete(*t.get_children())
        self._bus_by_iid = {}
        nets = self.table.networks if self.table is not None else []
        per_ecu = {}
        for i, (ecu, b) in enumerate(zonal.dbc_rows(self.cfg)):
            db = self._db(b)
            net, chosen = zonal.table_network(b, db, nets)
            rx, tx = start.node_counts(db).get(b.node, ("", "")) if db is not None and b.node else ("", "")
            todo = not ecu or not b.node or (self.table is not None and not net)
            net_text = net if chosen else (f"{net} (auto)" if net else ("-" if self.table is None else "none"))
            t.insert("", "end", iid=str(i), values=(os.path.basename(b.dbc), zonal.bus_name(b, db), b.node or "?",
                                                    ecu or "? (choose)", net_text, rx, tx),
                     tags=("todo",) if todo else ())
            self._bus_by_iid[str(i)] = (ecu, b)
            per_ecu.setdefault(ecu or "no ECU", []).append(zonal.bus_name(b, db))
        self.bus_summary.config(text="   ".join(f"{e}: {', '.join(v)}" for e, v in per_ecu.items()) or
                                "No DBC file yet.")

    def add_dbcs(self):
        paths = filedialog.askopenfilenames(parent=self.win, title="DBC files of the network",
                                            filetypes=[("CAN database", "*.dbc"), ("All files", "*.*")])
        if not paths:
            return
        have = {os.path.normcase(os.path.abspath(b.dbc)) for _e, b in zonal.dbc_rows(self.cfg)}
        todo = [os.path.abspath(p) for p in paths if os.path.normcase(os.path.abspath(p)) not in have]

        def work():
            out, errors = [], []
            for p in todo:
                try:
                    out.append((p, self.dbc_cache.get(p) or dbcread.load(p)))
                except Exception as exc:  # noqa: BLE001 - shown to the user
                    errors.append(f"{os.path.basename(p)}: {exc}")
            return out, errors

        def done(res):
            loaded, errors = res
            for p, db in loaded:
                self.dbc_cache[p] = db
            guesses = start.guess_nodes([db for _p, db in loaded])
            for (p, db), g in zip(loaded, guesses):
                node = zonal.guess_node(self.cfg, db) or g.node
                zonal.add_dbc(self.cfg, p, db, node, zonal.guess_ecu(self.cfg, db, node))
            if errors:
                messagebox.showerror(TITLE, "\n".join(errors), parent=self.win)
            self.changed()
        self._run(f"Reading {len(todo)} DBC file(s)", work, done)

    def _selected_buses(self):
        return [self._bus_by_iid[i] for i in self.t_bus.selection() if i in getattr(self, "_bus_by_iid", {})]

    def edit_dbc(self):
        sel = self._selected_buses()
        if not sel:
            return
        ecu, b = sel[0]
        db = self._db(b)
        nets = self.table.networks if self.table is not None else []
        auto = zonal.table_network(BusLike(b), db, nets)[0]
        d = BusEditDialog(self.win, b.dbc, db, b, ecu, [e.name for e in self.cfg.ecus], nets, auto)
        self.win.wait_window(d)
        if d.result is not None:
            b.node, new_ecu, b.table_network = d.result
            zonal.place_dbc(self.cfg, b, new_ecu)
            more = zonal.assign_node(self.cfg, b.node, new_ecu) if new_ecu else 0
            self.changed()
            if more:
                self.status.config(text=f"{more} other DBC file(s) with gateway node {b.node} also given to {new_ecu}.")

    def remove_dbcs(self):
        for _ecu, b in self._selected_buses():
            zonal.remove_dbc(self.cfg, b)
        self.changed()

    # ------------------------------------------------------------------ page 3: routing table
    def _page_table(self):
        f = ttk.Frame(self.nb, padding=10)
        self.nb.add(f, text="  3  Routing table  ")
        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Label(top, text="Routing table (Excel / CSV):").pack(side="left")
        self.v_table = tk.StringVar()
        e = ttk.Entry(top, textvariable=self.v_table)
        e.pack(side="left", fill="x", expand=True, padx=4)
        e.bind("<FocusOut>", lambda _e: self.changed())
        e.bind("<Return>", lambda _e: self.changed())
        ttk.Button(top, text="Browse…", command=self.browse_table).pack(side="left")
        self.v_hw = tk.BooleanVar()
        cb = ttk.Checkbutton(f, text="Route the rows with HW-Accelerator = 1 too (by PduR / Com)", variable=self.v_hw,
                             command=self.changed)
        cb.pack(anchor="w", pady=(6, 0))
        Tooltip(cb, "Off: rows with HW-Accelerator = 1 are left to the LLCE / PFE and get no route")
        self.table_summary = ttk.Label(f, text="", justify="left", wraplength=1300)
        self.table_summary.pack(fill="x", pady=(6, 6))
        pw = ttk.PanedWindow(f, orient="horizontal")
        pw.pack(fill="both", expand=True)
        left = ttk.LabelFrame(pw, text="Network columns", padding=6)
        right = ttk.LabelFrame(pw, text="Rows", padding=6)
        pw.add(left, weight=2)
        pw.add(right, weight=5)
        holder, self.t_nets = self._tree(left, (("net", "Column"), ("kind", "Kind"), ("ecu", "ECU"), ("bus", "Bus"),
                                                ("s", "S"), ("d", "D")), (80, 55, 75, 75, 30, 30), height=16,
                                         stretch="bus")
        holder.pack(fill="both", expand=True)
        ff = ttk.Frame(right)
        ff.pack(fill="x")
        ttk.Label(ff, text="Filter:").pack(side="left")
        self.v_filter = tk.StringVar()
        self.v_filter.trace_add("write", lambda *_a: self.fill_table_rows())
        ttk.Entry(ff, textvariable=self.v_filter, width=40).pack(side="left", padx=4)
        ttk.Label(ff, text="message, signal, column, ECU, problem …", foreground="#666666").pack(side="left")
        holder, self.t_rows = self._tree(right, (("row", "Row"), ("type", "Type"), ("from", "From"),
                                                 ("what", "Message / signal"), ("to", "To"), ("path", "Route")),
                                         (80, 90, 80, 300, 120, 420), height=16, stretch="path")
        holder.pack(fill="both", expand=True, pady=(6, 0))

    def browse_table(self):
        p = filedialog.askopenfilename(parent=self.win, title="Routing table",
                                       filetypes=[("Routing table", "*.xlsx *.xlsm *.csv *.tsv *.txt"),
                                                  ("All files", "*.*")])
        if p:
            self.v_table.set(os.path.normpath(p))
            self.changed()

    def read_table(self):
        """(Re)read the routing table when its file changed; the problem as text ('' = read)."""
        path = self.cfg.routing_table
        if not path or not os.path.isfile(path):
            self.table, self._table_key = None, None
            return "" if not path else f"{path} does not exist"
        key = (os.path.abspath(path), os.path.getmtime(path))
        if key == self._table_key:
            return ""
        try:
            self.table = routing_table.read(path)
            self._table_key = key
            return ""
        except Exception as exc:  # noqa: BLE001 - shown to the user
            self.table, self._table_key = None, None
            return str(exc)

    def fill_table(self, problem: str = ""):
        for t in (self.t_nets, self.t_rows):
            t.delete(*t.get_children())
        if self.table is None:
            self.table_summary.config(text=problem or "Select the routing table of the network.",
                                      foreground=COLORS["error"] if problem else "#333333")
            self._overview = None
            return
        ov = zonal.overview(self.cfg, self.table, self.dbc_cache)
        self._overview = ov
        kinds = ", ".join(f"{k}: {n}" for k, n in sorted(ov.counts.items(), key=lambda x: -x[1]))
        self.table_summary.config(foreground="#333333", text=(
            f"{ov.rows} rows: {ov.messages} message, {ov.signals} signal; {ov.hw} with HW-Accelerator = 1"
            f"{' (routed)' if self.cfg.table_hw else ' (not routed)'}; {ov.problems} with a problem.\n"
            f"Rows by route: {kinds}."))
        for u in ov.networks:
            todo = u.kind == "CAN" and not u.ecu and (u.as_source or u.as_dest)
            self.t_nets.insert("", "end", values=(u.network, u.kind, u.ecu or ("-" if u.kind != "CAN" else "?"),
                                                  u.bus or "", u.as_source, u.as_dest),
                               tags=("todo",) if todo else (("off",) if u.kind != "CAN" else ()))
        self.fill_table_rows()

    def fill_table_rows(self):
        t = self.t_rows
        t.delete(*t.get_children())
        ov = getattr(self, "_overview", None)
        if ov is None:
            return
        q = self.v_filter.get().strip().lower()
        for i, row in enumerate(ov.paths):
            if q and q not in " ".join(str(x) for x in row).lower():
                continue
            path = row[5]
            tag = ("bad",) if path.startswith(("problem", "no DBC")) else \
                (("off",) if "not routed" in path else ())
            t.insert("", "end", iid=str(i), values=row, tags=tag)

    # ------------------------------------------------------------------ page 4: generate
    def _page_generate(self):
        f = ttk.Frame(self.nb, padding=10)
        self.nb.add(f, text="  4  Generate  ")
        top = ttk.LabelFrame(f, text="Gateway file", padding=8)
        top.pack(fill="x")
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="ECU to generate:").grid(row=0, column=0, sticky="w", pady=2)
        self.v_target = tk.StringVar()
        self.c_target = ttk.Combobox(top, textvariable=self.v_target, width=24, state="readonly")
        self.c_target.grid(row=0, column=1, sticky="w")
        self.c_target.bind("<<ComboboxSelected>>", lambda _e: self.target_changed())
        ttk.Label(top, text="ECU instance in DaVinci:").grid(row=1, column=0, sticky="w", pady=2)
        inst = ttk.Frame(top)
        inst.grid(row=1, column=1, sticky="w")
        self.v_inst = tk.StringVar()
        e = ttk.Entry(inst, textvariable=self.v_inst, width=26)
        e.pack(side="left")
        self.inst_hint = ttk.Label(inst, text="", foreground="#666666")
        self.inst_hint.pack(side="left", padx=8)
        Tooltip(e, "Name of the ECU instance in the DaVinci project, as the DBC converter names it (the ECU "
                   "attribute of the gateway node in the DBC, else the node)")
        ttk.Label(top, text="Output file:").grid(row=2, column=0, sticky="w", pady=2)
        of = ttk.Frame(top)
        of.grid(row=2, column=1, sticky="we")
        self.v_out = tk.StringVar()
        ttk.Entry(of, textvariable=self.v_out).pack(side="left", fill="x", expand=True)
        ttk.Button(of, text="Browse…", command=self.browse_out).pack(side="left", padx=4)
        self.out_hint = ttk.Label(top, text="", foreground="#666666")
        self.out_hint.grid(row=3, column=1, sticky="w")
        bf = ttk.Frame(f)
        bf.pack(fill="x", pady=8)
        ttk.Button(bf, text="Analyze", command=self.analyze).pack(side="left")
        self.b_gen = ttk.Button(bf, text="Generate", command=self.generate)
        self.b_gen.pack(side="left", padx=6)
        ttk.Button(bf, text="Message report", command=self.message_report).pack(side="left")
        ttk.Button(bf, text="Open folder", command=self.open_folder).pack(side="left", padx=6)
        self.cards = ttk.Frame(f)
        self.cards.pack(fill="x")
        pw = ttk.PanedWindow(f, orient="vertical")
        pw.pack(fill="both", expand=True, pady=(6, 0))
        rt = ttk.Frame(pw)
        holder, self.t_routes = self._tree(rt, [(c, c) for c in report.COLUMNS],
                                           (60, 65, 75, 120, 200, 90, 70, 55, 65, 200, 220, 120, 90, 95, 200, 380),
                                           height=12, stretch="Remark")
        holder.pack(fill="both", expand=True)
        self.t_routes.bind("<Button-3>", self.route_menu)
        msg = ttk.Frame(pw)
        self.t_msg = tk.Text(msg, height=7, wrap="word", font=("Segoe UI", 9), background="#ffffff", relief="flat")
        ys = ttk.Scrollbar(msg, orient="vertical", command=self.t_msg.yview)
        self.t_msg.configure(yscrollcommand=ys.set)
        self.t_msg.pack(side="left", fill="both", expand=True)
        ys.pack(side="left", fill="y")
        for tag in ("error", "warning", "info"):
            self.t_msg.tag_configure(tag, foreground=COLORS[tag])
        pw.add(rt, weight=3)
        pw.add(msg, weight=1)

    def browse_out(self):
        p = filedialog.asksaveasfilename(parent=self.win, title="Gateway file", defaultextension=".arxml",
                                         filetypes=[("ARXML", "*.arxml")], initialfile=os.path.basename(
                                             self.v_out.get() or "Gateway.arxml"))
        if p:
            self.v_out.set(os.path.normpath(p))
            self.store()

    def target_changed(self):
        self.store()
        self.show_target()
        self.tplan = None
        self.fill_results()

    def show_target(self):
        names = [e.name for e in self.cfg.ecus if e.gateway.buses]
        self.c_target.configure(values=names)
        if self.cfg.target not in names:
            self.cfg.target = names[0] if len(names) == 1 else ""
        self.v_target.set(self.cfg.target)
        self._shown_target = self.cfg.target
        e = self.cfg.ecu(self.cfg.target) if self.cfg.target else None
        if e is None:
            self.v_inst.set("")
            self.v_out.set("")
            self.inst_hint.config(text="")
            self.out_hint.config(text="")
            return
        self.v_inst.set(e.gateway.ecu)
        self.inst_hint.config(text=f"empty = {zonal.instance_name(e, self.dbc_cache)}")
        self.v_out.set(e.gateway.output)
        out = e.gateway.output or zonal.default_output(self.cfg, e.name)
        self.out_hint.config(text=(f"empty = {out}. " if not e.gateway.output else "") + (
            "The file exists: it is written again, DaVinci keeps the configuration of what did not change."
            if os.path.isfile(out) else "A new file: add it and its .vsde file to the Input Files of the DaVinci "
                                        "project (next to the DBC files)."))

    def _cards(self, items):
        for w in self.cards.winfo_children():
            w.destroy()
        for value, label, color in items:
            c = tk.Frame(self.cards, background="#ffffff", highlightbackground="#c9d3e0", highlightthickness=1)
            c.pack(side="left", padx=(0, 8))
            tk.Label(c, text=str(value), background="#ffffff", foreground=color, font=("Segoe UI", 14, "bold")).pack(
                anchor="w", padx=10, pady=(4, 0))
            tk.Label(c, text=label, background="#ffffff", foreground="#444444").pack(anchor="w", padx=10, pady=(0, 4))

    def fill_results(self):
        t = self.t_routes
        t.delete(*t.get_children())
        self._route_by_iid = {}
        tp = self.tplan
        plan = tp.plans.get(self.cfg.target) if tp is not None else None
        if plan is None:
            self._cards([])
            return
        hpc = zonal.hpc(self.cfg)
        hname = hpc.name if hpc is not None else ""
        ecus = {e.name for e in self.cfg.ecus}
        on = plan.enabled_routes
        to_hpc = sum(1 for r in on if r.direction == CAN_TO_ETH and hname in r.peers)
        to_ecu = sum(1 for r in on if r.direction == CAN_TO_ETH and ecus & set(r.peers))
        from_hpc = sum(1 for r in on if r.direction != CAN_TO_ETH and r.peers[:1] == [hname])
        from_ecu = sum(1 for r in on if r.direction != CAN_TO_ETH and set(r.peers[:1]) & ecus)
        rows = [s for s in plan.table_rows if s.status != "not this ECU"]
        bad = sum(1 for s in rows if "problem" in s.status or "off" in s.status)
        self._cards([(to_hpc, f"CAN -> {hname or 'HPC'}", "#1f4e8c"), (to_ecu, "CAN -> other ECU", "#1f4e8c"),
                     (from_hpc, f"{hname or 'HPC'} -> CAN", "#0b7a5c"), (from_ecu, "other ECU -> CAN", "#0b7a5c"),
                     (len(plan.enabled_can_routes), "CAN -> CAN (message)", "#6a1b9a"),
                     (len(plan.enabled_signal_routes), "CAN -> CAN (signal)", "#6a1b9a"),
                     (bad, "table rows to check", COLORS["warning"] if bad else "#444444")])
        for i, (r, row) in enumerate(report.route_items(plan)):
            off = isinstance(r, (Route, CanRoute, SignalRoute)) and not r.enabled
            t.insert("", "end", iid=str(i), values=row, tags=("off",) if off else ())
            self._route_by_iid[str(i)] = r

    def show_messages(self, errors=(), warnings=(), infos=()):
        t = self.t_msg
        t.configure(state="normal")
        t.delete("1.0", "end")
        for tag, items in (("error", errors), ("warning", warnings), ("info", infos)):
            for m in items:
                t.insert("end", f"[{tag.upper()}] {m}\n", tag)
        t.configure(state="disabled")

    def _messages_of(self, tp):
        """Errors of every ECU (they stop the generation), warnings / infos of the network and the target ECU."""
        mine = f"[{self.cfg.target}] "
        keep = lambda m: not m.startswith("[") or m.startswith(mine)
        return tp.errors, [m for m in tp.warnings if keep(m)], [m for m in tp.infos if keep(m)]

    def route_menu(self, e):
        iid = self.t_routes.identify_row(e.y)
        if iid and iid not in self.t_routes.selection():
            self.t_routes.selection_set(iid)
        m = tk.Menu(self.win, tearoff=False)
        m.add_command(label="Enable", command=lambda: self.set_routes(True))
        m.add_command(label="Disable", command=lambda: self.set_routes(False))
        m.add_command(label="Automatic (remove my setting)", command=lambda: self.set_routes(None))
        m.tk_popup(e.x_root, e.y_root)

    def set_routes(self, value):
        """Switch the selected routes of the target ECU on / off (kept in the network file)."""
        e = self.cfg.ecu(self.cfg.target)
        if e is None:
            return
        for iid in self.t_routes.selection():
            r = self._route_by_iid.get(iid)
            if isinstance(r, (CanRoute, SignalRoute)):
                if value is None:
                    e.gateway.can_gateway.pop(r.key, None)
                else:
                    e.gateway.can_gateway[r.key] = {"enabled": value}
            elif isinstance(r, Route):
                bus = next((b for b in e.gateway.buses
                            if os.path.normcase(os.path.abspath(b.dbc)) == os.path.normcase(
                                os.path.abspath(r.bus.cfg.dbc))), None)
                if bus is None:
                    continue
                over = dict(bus.messages.get(r.message.name, {}))
                over.pop("enabled", None)
                over.pop("reason", None)
                if value is not None:
                    over["enabled"] = value
                if over:
                    bus.messages[r.message.name] = over
                else:
                    bus.messages.pop(r.message.name, None)
        self.analyze()

    # ------------------------------------------------------------------ analyze / generate
    def analyze(self, then=None):
        self.store()
        miss = zonal.missing(self.cfg)
        if miss:
            self.show_messages(errors=[miss])
            self.update_hint()
            return
        cfg = zonal.planning_config(self.cfg, self.dbc_cache)

        def done(tp):
            self.tplan = tp
            self.fill_results()
            self.show_messages(*self._messages_of(tp))
            plan = tp.plans.get(self.cfg.target)
            n = report.count_text(len(plan.enabled_routes), len(plan.enabled_can_routes),
                                  len(plan.enabled_signal_routes)) if plan is not None else "-"
            self.status.config(text=f"{self.cfg.target}: {n}; {len(tp.errors)} error(s), "
                                    f"{len(tp.warnings)} warning(s) in the network")
            if then is not None and tp.ok:
                then(tp)
        self._run("Analyzing the network", lambda: make_topology_plan(cfg, self.bases, self.dbc_cache), done)

    def generate(self):
        if not self.path and not self.save_as():
            return

        def write(tp):
            def work():
                return generate_topology(tp)

            def done(results):
                name, res = results[0]
                files = [res.output] + ([res.extension] if res.extension else [])
                stem = os.path.splitext(self.path)[0]
                self._report = stem + "_message_paths.html"
                self.show_messages(warnings=res.warnings, infos=[f"Written {f}" for f in files] + [
                    f"Message report: {self._report}", f"Contract (every Ethernet PDU): {stem}_contract.csv",
                    f"Header ids of the network: {stem}.lock.json (keep it with the network file: the other ECUs "
                    f"get the same values)"])
                self.status.config(text=f"Written {os.path.basename(res.output)}")
                messagebox.showinfo(TITLE, f"Written for {name}:\n\n" + "\n".join(files) + (
                    "\n\nIn DaVinci: add both files to the Input Files of the project (next to the DBC files) once, "
                    "then run Update." if res.extension else "\n\nIn DaVinci: add the file to the Input Files of "
                                                              "the project once, then run Update."),
                    parent=self.win)
            self._run("Generating", work, done)
        self.analyze(then=write)

    def message_report(self):
        if not self.path and not self.save_as():
            return

        def write(tp):
            from . import paths
            stem = os.path.splitext(self.path)[0]
            html_path, _csv = paths.write_report(paths.report_of_topology(tp), stem)
            try:
                os.startfile(html_path)  # noqa: S606 - opens the report in the browser
            except OSError:
                pass
        self.analyze(then=write)

    def open_folder(self):
        folder = os.path.dirname(self.path) if self.path else ""
        if folder and os.path.isdir(folder):
            os.startfile(folder)  # noqa: S606

    # ------------------------------------------------------------------ model <-> widgets
    def store(self):
        c = self.cfg
        c.ethernet.vlan_id = _int(self.v_vlan.get()) if self.v_vlan.get().strip() else None
        c.ethernet.netmask = self.v_mask.get().strip() or "255.255.255.0"
        c.one_socket = self.v_one.get()
        c.routing_table = self.v_table.get().strip()
        c.table_hw = self.v_hw.get()
        e = c.ecu(self._shown_target) if self._shown_target else None
        if e is not None:                   # the fields show this ECU (the target may just have changed)
            e.gateway.ecu = self.v_inst.get().strip()
            e.gateway.output = self.v_out.get().strip()
        if self.v_target.get():
            c.target = self.v_target.get()

    def show(self):
        c = self.cfg
        self.v_vlan.set("" if c.ethernet.vlan_id is None else str(c.ethernet.vlan_id))
        self.v_mask.set(c.ethernet.netmask or "255.255.255.0")
        self.v_one.set(c.one_socket)
        self.v_table.set(c.routing_table)
        self.v_hw.set(c.table_hw)
        self.changed(store=False)

    def changed(self, store: bool = True):
        """Something changed: refresh every page."""
        if store:
            self.store()
        problem = self.read_table()
        self.fill_nodes()
        self.fill_buses()
        self.fill_table(problem)
        self.show_target()
        self.tplan = None
        self.fill_results()
        self.update_hint()

    def update_hint(self):
        miss = zonal.missing(self.cfg)
        self.hint_text.config(text=miss or "Ready: press Generate on page 4 (or Analyze to look at the routes first).")
        self._hint_tab = int(miss[0]) - 1 if miss[:1].isdigit() else 3

    def _hint_go(self):
        self.nb.select(getattr(self, "_hint_tab", 0))

    def _tab_changed(self):
        self.store()
        self.update_hint()

    # ------------------------------------------------------------------ file
    def _title(self):
        name = os.path.basename(self.path) if self.path else "new network"
        self.win.title(f"{TITLE} - {name}")
        self.file_label.config(text=self.path or "(not saved yet)")

    def new(self):
        self.cfg = zonal.new_network(nodes.load())
        self.path, self.tplan, self._shown_target = "", None, ""
        self._title()
        self.show()

    def new_dialog(self):
        if messagebox.askyesno(TITLE, "Start a new network? Unsaved changes are lost.", parent=self.win):
            self.new()

    def open_dialog(self):
        p = filedialog.askopenfilename(parent=self.win, title="Network file",
                                       filetypes=[("Network file", "*.json"), ("All files", "*.*")])
        if p:
            self.open(p)

    def open(self, path):
        try:
            cfg = TopologyConfig.load(path)
        except (OSError, ValueError) as exc:
            messagebox.showerror(TITLE, f"Cannot read {path}:\n{exc}", parent=self.win)
            return
        self.cfg, self.path, self.tplan, self._shown_target = cfg, os.path.abspath(path), None, ""
        self._remember()
        self._title()
        self.show()
        todo = [os.path.abspath(b.dbc) for _e, b in zonal.dbc_rows(cfg)
                if b.dbc and os.path.abspath(b.dbc) not in self.dbc_cache and os.path.isfile(b.dbc)]
        if todo:
            def work():
                out = {}
                for p in todo:
                    try:
                        out[p] = dbcread.load(p)
                    except Exception:  # noqa: BLE001 - shown when the network is analyzed
                        pass
                return out
            self._run(f"Reading {len(todo)} DBC file(s)", work, lambda res: (self.dbc_cache.update(res),
                                                                              self.changed(store=False)))

    def save(self) -> bool:
        if not self.path:
            return self.save_as()
        self.store()
        try:
            self.cfg.save(self.path)
        except OSError as exc:
            messagebox.showerror(TITLE, f"Cannot write {self.path}:\n{exc}", parent=self.win)
            return False
        self._remember()
        self.status.config(text=f"Saved {self.path}")
        return True

    def save_as(self) -> bool:
        p = filedialog.asksaveasfilename(parent=self.win, title="Save the network file", defaultextension=".json",
                                         filetypes=[("Network file", "*.json")], initialfile="gateway_network.json")
        if not p:
            return False
        self.path = os.path.abspath(p)
        self.cfg.path = self.path
        self._title()
        ok = self.save()
        self.show_target()
        return ok

    def _remember(self):
        st = _settings()
        rec = [x for x in st.get(RECENT_KEY, []) if os.path.normcase(x) != os.path.normcase(self.path)]
        st[RECENT_KEY] = [self.path] + rec[:9]
        try:
            st.save()
        except OSError:
            pass

    # ------------------------------------------------------------------ other windows
    def open_topology_window(self):
        if not self.save():
            return
        from .topology.gui import open_topology
        open_topology(self.win, self.path)

    def open_classic(self):
        from .gui import open_window
        open_window(self.win)

    def open_editor(self):
        from .editor_gui import open_editor
        open_editor(self.win)

    # ------------------------------------------------------------------ background work
    def _run(self, label, fn, done):
        if self._busy:
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
                    self.show_messages(errors=[str(err[0])] + err[1].splitlines()[-3:])
                    self.nb.select(3)
                else:
                    done(res)
        except queue.Empty:
            pass
        self.win.after(100, self._poll)


class BusLike:
    """A bus as zonal.table_network sees it without the user's choice (for the 'empty = …' hint)."""

    def __init__(self, b):
        self.dbc, self.table_network = b.dbc, ""


def open_app(master: tk.Misc | None = None, path: str | None = None):
    """Open the main window in a Toplevel of *master* (EcucStudio) or in its own root window."""
    from ..gui.startup import bring_to_front, show_main_window
    if master is None:
        root = tk.Tk()
        init_style(root)
        root.app = GatewayApp(root, path)
        show_main_window(root, TITLE, None, "1450x900")
        return root
    top = tk.Toplevel(master)
    top.geometry("1450x900")
    top.app = GatewayApp(top, path)
    bring_to_front(top)
    return top


def main(path: str | None = None):
    root = open_app(None, path)
    root.mainloop()


__all__ = ["GatewayApp", "open_app", "main"]
