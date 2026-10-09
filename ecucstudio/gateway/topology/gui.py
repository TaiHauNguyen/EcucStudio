"""Window of the multi-ECU mode: the ECUs and peers of one Ethernet network, ECU -> ECU routes, contract."""
from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, ttk

from ...gui.theme import COLORS, init_style
from ...gui.widgets import Tooltip, dialog_header
from .. import dvproject, report
from ..base import DEFAULT_SCHEMA, SCHEMAS
from ..config import BusInput, GatewayConfig
from ..gui import BusDialog, _int_or_none
from ..planner import CAN_TO_ETH, load_base
from . import report as treport
from .config import EcuNode, PeerNode, TopologyConfig
from .planner import make_topology_plan
from .writer import CONTRACT_COLUMNS, contract_rows, generate_topology

TITLE = "CAN Gateway Topology"


def _port_text(v):
    return "" if v is None else str(v)


class CrossDialog(tk.Toplevel):
    """One ECU -> ECU route: on/off, header id, also to the default peer."""

    def __init__(self, master, cr, over: dict, default_peer: str, default_also: bool):
        super().__init__(master)
        self.title("ECU -> ECU route")
        self.transient(master)
        self.result = None
        dialog_header(self, f"{cr.src_ecu}/{cr.src.bus.name}/{cr.src.message.name}  ->  "
                            f"{cr.dst_ecu}/{cr.dst.bus.name}/{cr.dst.message.name}",
                      f"Pairing: {cr.match}." + (f" {cr.reason}." if cr.reason else "") +
                      " The header id is used by both ECUs; leave it empty for the automatic value.")
        f = ttk.Frame(self, padding=10)
        f.pack(fill="both")
        self.v_on = tk.BooleanVar(value=cr.enabled)
        ttk.Checkbutton(f, text="Route this message directly between the ECUs", variable=self.v_on).grid(
            row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(f, text="Header id:").grid(row=1, column=0, sticky="w", pady=4)
        self.v_hid = tk.StringVar(value=over.get("header_id", ""))
        ttk.Entry(f, textvariable=self.v_hid, width=14).grid(row=1, column=1, sticky="w")
        ttk.Label(f, text=f"(planned 0x{cr.header_id:08X})" if cr.header_id >= 0 else "",
                  foreground="#666666").grid(row=1, column=2, sticky="w", padx=6)
        self.v_also = tk.BooleanVar(value=over.get("also_to_default_peer", default_also))
        cb = ttk.Checkbutton(f, text=f"Also send it to {default_peer}" if default_peer else "Also send it to the "
                             "default peer", variable=self.v_also)
        cb.grid(row=2, column=0, columnspan=3, sticky="w")
        if not default_peer:
            cb.state(["disabled"])
        bb = ttk.Frame(self, padding=(10, 0, 10, 10))
        bb.pack(fill="x")
        ttk.Button(bb, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(bb, text="OK", command=self.ok).pack(side="right", padx=6)
        self.default_also = default_also
        self.grab_set()

    def ok(self):
        h = self.v_hid.get().strip()
        if h and _int_or_none(h) is None:
            messagebox.showwarning(TITLE, "The header id must be a number (e.g. 0x123).", parent=self)
            return
        self.result = {"enabled": self.v_on.get()}
        if h:
            self.result["header_id"] = h
        if self.v_also.get() != self.default_also:
            self.result["also_to_default_peer"] = self.v_also.get()
        self.destroy()


class LinkDialog(tk.Toplevel):
    """Pair a message one ECU receives with a message another ECU sends."""

    def __init__(self, master, tplan):
        super().__init__(master)
        self.title("ECU -> ECU link")
        self.transient(master)
        self.result = None
        dialog_header(self, "Add an ECU -> ECU link",
                      "The PDU received by the first ECU is sent unchanged by the second one (same length). Use it "
                      "for renamed messages or to choose the source of a message several ECUs receive.")
        f = ttk.Frame(self, padding=10)
        f.pack(fill="both")
        self._src, self._dst = {}, {}
        for ecu, plan in tplan.plans.items():
            for r in plan.routes:
                if r.can_problem:
                    continue
                label = f"{ecu}/{r.bus.name}/{r.message.name}  ({r.message.id_text}, {r.length} byte)"
                (self._src if r.direction == CAN_TO_ETH else self._dst)[label] = f"{ecu}/{r.key}"
        ttk.Label(f, text="Received by (source):").grid(row=0, column=0, sticky="w", pady=3)
        self.c_src = ttk.Combobox(f, values=sorted(self._src), width=70, state="readonly")
        self.c_src.grid(row=0, column=1, sticky="we")
        ttk.Label(f, text="Sent by (destination):").grid(row=1, column=0, sticky="w", pady=3)
        self.c_dst = ttk.Combobox(f, values=sorted(self._dst), width=70, state="readonly")
        self.c_dst.grid(row=1, column=1, sticky="we")
        bb = ttk.Frame(self, padding=(10, 0, 10, 10))
        bb.pack(fill="x")
        ttk.Button(bb, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(bb, text="OK", command=self.ok).pack(side="right", padx=6)
        self.grab_set()

    def ok(self):
        s, d = self._src.get(self.c_src.get()), self._dst.get(self.c_dst.get())
        if not s or not d:
            messagebox.showwarning(TITLE, "Select the source and the destination message.", parent=self)
            return
        if s.split("/", 1)[0] == d.split("/", 1)[0]:
            messagebox.showwarning(TITLE, "Source and destination must be on different ECUs (inside one ECU it is "
                                          "a CAN -> CAN route).", parent=self)
            return
        self.result = {"src": s, "dst": d}
        self.destroy()


class TopologyWindow:
    def __init__(self, master: tk.Misc, path: str | None = None, select: str | None = None):
        self.win = master
        self.cfg = TopologyConfig()
        self.tplan = None
        self.bases: dict = {}           # kept between analyses (the planner's cache)
        self.dbc_cache: dict = {}
        self._gui_bases: dict = {}      # bases for the bus dialog
        self._store = None              # writes the visible editor panel back into the configuration
        self._q = queue.Queue()
        self._busy = False
        self._build()
        self.win.after(100, self._poll)
        if path:
            self.open(path, select=select)
        else:
            self.new()

    # ------------------------------------------------------------------ layout
    def _build(self):
        w = self.win
        tb = ttk.Frame(w, padding=(6, 4))
        tb.pack(fill="x")
        b_dbc = ttk.Button(tb, text="From DBC files…", command=self.from_dbcs)
        b_dbc.pack(side="left", padx=(0, 4))
        Tooltip(b_dbc, "New topology from the DBC files of the network: the gateway ECUs are found from the node "
                       "names (DBC files with the same gateway node belong to one ECU)")
        for text, cmd in (("New", self.new), ("Open…", self.open_dialog), ("Save", self.save),
                          ("Save As…", self.save_as)):
            ttk.Button(tb, text=text, command=cmd).pack(side="left", padx=(0, 4))
        b_nodes = ttk.Button(tb, text="Ethernet nodes…", command=self.edit_nodes)
        b_nodes.pack(side="left", padx=(0, 4))
        Tooltip(b_nodes, "MAC, IPv4 and port base of the Ethernet nodes: the empty IP addresses, ports and MACs of the "
                         "ECUs and peers are filled from this table by node name (also on Analyze)")
        ttk.Separator(tb, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(tb, text="Analyze", command=self.analyze).pack(side="left", padx=(0, 4))
        b_gen = ttk.Button(tb, text="Generate", command=self.generate)
        b_gen.pack(side="left")
        b_rep = ttk.Button(tb, text="Message Report", command=self.message_report)
        b_rep.pack(side="left", padx=(4, 0))
        Tooltip(b_rep, "Write and open the message path report of the whole network: for every message (by CAN id) "
                       "where it comes from, which gateway ECUs it passes and where it goes")
        Tooltip(b_gen, "Write the gateway file of every ECU marked 'generate', the lock file (header ids) and the "
                       "contract (all Ethernet PDUs) next to the topology file")
        self.status = ttk.Label(tb, text="Add the ECUs (DaVinci project or DBC files) and the peers of the network.")
        self.status.pack(side="left", padx=12)
        pw = ttk.PanedWindow(w, orient="vertical")
        pw.pack(fill="both", expand=True)
        top = ttk.Frame(pw)
        pw.add(top, weight=0)
        left = ttk.LabelFrame(top, text="Network", padding=6)
        left.pack(side="left", fill="y")
        cols = ("Node", "Kind", "IP", "Source")
        self.t_nodes = ttk.Treeview(left, columns=cols, show="headings", height=12, selectmode="browse")
        for c, wd in zip(cols, (120, 110, 105, 110)):
            self.t_nodes.heading(c, text=c, anchor="w")
            self.t_nodes.column(c, width=wd, anchor="w")
        self.t_nodes.pack(fill="y", expand=True)
        self.t_nodes.bind("<<TreeviewSelect>>", lambda _e: self.node_selected())
        nb = ttk.Frame(left)
        nb.pack(fill="x", pady=(6, 0))
        for text, cmd in (("Add ECU", self.add_ecu), ("Add peer", self.add_peer), ("Remove", self.remove_node),
                          ("Up", lambda: self.move_node(-1)), ("Down", lambda: self.move_node(1))):
            ttk.Button(nb, text=text, command=cmd, width=9).pack(side="left", padx=(0, 3))
        self.panel = ttk.LabelFrame(top, text="", padding=8)
        self.panel.pack(side="left", fill="both", expand=True, padx=(8, 0))
        bottom = ttk.Frame(pw)
        pw.add(bottom, weight=1)
        self.tabs = ttk.Notebook(bottom)
        self.tabs.pack(fill="both", expand=True)
        self.t_cross = self._table(self.tabs, "  ECU -> ECU  ", treport.CROSS_COLUMNS,
                                   (55, 60, 260, 260, 110, 55, 230, 95, 420))
        self.t_routes = self._table(self.tabs, "  Routes of the selected ECU  ", report.COLUMNS,
                                    (60, 65, 75, 120, 200, 90, 70, 55, 65, 200, 220, 110, 130, 95, 220, 380))
        self.t_contract = self._table(self.tabs, "  Contract (Ethernet PDUs)  ", CONTRACT_COLUMNS,
                                      (80, 80, 160, 90, 55, 230, 95, 130, 80, 130, 160, 300))
        from ..paths import COLUMNS as PATH_COLUMNS
        self.t_paths = self._table(self.tabs, "  Message paths  ", PATH_COLUMNS,
                                   (95, 200, 200, 330, 220, 150, 110, 130, 55, 65))
        self.t_cross.bind("<Double-1>", lambda _e: self.edit_cross())
        self.t_cross.bind("<space>", lambda _e: self.toggle_cross())
        self.t_cross.bind("<Button-3>", self.cross_menu)
        self.t_routes.bind("<space>", lambda _e: self.toggle_routes())
        self.t_routes.bind("<Button-3>", self.routes_menu)
        self.t_msg = tk.Text(bottom, height=6, wrap="word", font=("Segoe UI", 9), background="#ffffff",
                             relief="flat", borderwidth=1)
        self.t_msg.pack(fill="x", padx=2, pady=2)
        for tag in ("error", "warning", "info"):
            self.t_msg.tag_configure(tag, foreground=COLORS[tag])
        self.win.after(150, lambda: pw.sashpos(0, max(top.winfo_reqheight(), 330)))

    def _table(self, nb, title, cols, widths):
        f = ttk.Frame(nb)
        nb.add(f, text=title)
        t = ttk.Treeview(f, columns=cols, show="headings", selectmode="extended")
        for c, wd in zip(cols, widths):
            t.heading(c, text=c, anchor="w")
            t.column(c, width=wd, anchor="w", stretch=c in ("Remark", "Header note"))
        ys = ttk.Scrollbar(f, orient="vertical", command=t.yview)
        xs = ttk.Scrollbar(f, orient="horizontal", command=t.xview)
        t.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        t.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        f.rowconfigure(0, weight=1)
        f.columnconfigure(0, weight=1)
        t.tag_configure("off", foreground="#9e9e9e")
        t.tag_configure("conflict", foreground=COLORS["error"])
        t.tag_configure("new", foreground=COLORS["info"])
        t.tag_configure("removed", foreground=COLORS["error"])
        t.tag_configure("band", background="#f3f6f9")
        return t

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
                    self.show_messages(errors=[str(err[0])], infos=err[1].splitlines()[-3:])
                else:
                    done(res)
        except queue.Empty:
            pass
        self.win.after(100, self._poll)

    # ------------------------------------------------------------------ file
    def _title(self):
        name = os.path.basename(self.cfg.path) if self.cfg.path else "new topology"
        self.win.title(f"{TITLE} - {name}")

    def new(self):
        self.cfg = TopologyConfig(peers=[PeerNode(name="Central", ip="")], default_peer="Central", one_socket=True)
        self._reset()

    def fill_from_nodes(self) -> list[str]:
        """Fill the empty addresses / ports / MACs from the Ethernet node table; returns info lines."""
        from .. import nodes as nodemod
        try:
            done = nodemod.fill_topology(self.cfg, nodemod.load())
        except (OSError, ValueError):
            return []
        return [f"Filled from Ethernet nodes: {x}" for x in done]

    def edit_nodes(self):
        from ..gui import NodesDialog
        self.store()
        d = NodesDialog(self.win)
        self.win.wait_window(d)
        if d.result is not None:
            filled = self.fill_from_nodes()
            self.refresh_nodes("net")
            self.show_messages(infos=filled or ["Ethernet nodes saved (no empty field to fill)."])

    def _reset(self):
        self.tplan, self.bases, self._gui_bases, self._store = None, {}, {}, None
        self._title()
        self.refresh_nodes("net")
        self.fill_tables()
        self.show_messages()

    def from_dbcs(self):
        from ..wizard import StartWizard
        StartWizard(self.win, dbc_cache=self.dbc_cache, initial_case="new",
                    on_topology_file=lambda path, target=None: self.open(path, select=target))

    def open_dialog(self):
        p = filedialog.askopenfilename(parent=self.win, title="Topology file",
                                       filetypes=[("Topology", "*.json"), ("All files", "*.*")])
        if p:
            self.open(p)

    def open(self, path, select: str | None = None):
        try:
            self.cfg = TopologyConfig.load(path)
        except (OSError, ValueError) as exc:
            messagebox.showerror(TITLE, f"Cannot read {path}:\n{exc}", parent=self.win)
            return
        self._reset()
        i = next((k for k, e in enumerate(self.cfg.ecus) if e.name == select), None)
        if i is not None:
            self.refresh_nodes(f"ecu:{i}")
            self.tabs.select(1)                      # routes of the ECU the gateway file is made for
        self.analyze()

    def save(self):
        if not self.cfg.path:
            return self.save_as()
        self.store()
        self.cfg.save()
        self.status.config(text=f"Saved {self.cfg.path}")
        return True

    def save_as(self):
        p = filedialog.asksaveasfilename(parent=self.win, title="Save topology", defaultextension=".json",
                                         initialfile="topology.json",
                                         filetypes=[("Topology", "*.json"), ("All files", "*.*")])
        if not p:
            return False
        self.cfg.path = os.path.abspath(p)
        self._title()
        return self.save()

    def store(self):
        if self._store is not None:
            self._store()

    # ------------------------------------------------------------------ nodes
    def _row(self, iid):
        cfg = self.cfg
        if iid == "net":
            return ("(network settings)", "", "", "")
        kind, i = iid.split(":")
        if kind == "ecu":
            e = cfg.ecus[int(i)]
            g = e.gateway
            src = "project" if dvproject.is_project(g.base) else ("network file" if g.base else "DBC files")
            return (e.name, "ECU" + ("" if e.generate else " (reference)"), e.ip, src)
        p = cfg.peers[int(i)]
        return (p.name, "peer" + (" (default)" if p.name == cfg.default_peer else ""), p.ip, "")

    def refresh_nodes(self, select=None):
        """Rebuild the node list; selecting *select* shows its editor (through the selection event)."""
        t, cfg = self.t_nodes, self.cfg
        self._shown = None
        t.delete(*t.get_children())
        for iid in ["net"] + [f"ecu:{i}" for i in range(len(cfg.ecus))] + [f"peer:{i}" for i in range(len(cfg.peers))]:
            t.insert("", "end", iid=iid, values=self._row(iid))
        if select and t.exists(select):
            t.selection_set(select)
            t.see(select)

    def _selected_node(self):
        sel = self.t_nodes.selection()
        if not sel or sel[0] == "net":
            return "net", None
        kind, i = sel[0].split(":")
        return kind, int(i)

    def node_selected(self):
        sel = self.t_nodes.selection()
        iid = sel[0] if sel else "net"
        if iid == getattr(self, "_shown", None):
            return
        self.store()
        self._shown = iid
        kind, i = self._selected_node()
        for w in self.panel.winfo_children():
            w.destroy()
        if kind == "net":
            self._panel_network()
        elif kind == "ecu":
            self._panel_ecu(self.cfg.ecus[i])
            self.fill_routes()
        else:
            self._panel_peer(self.cfg.peers[i])

    def add_ecu(self):
        self.store()
        n = len(self.cfg.ecus) + 1
        self.cfg.ecus.append(EcuNode(name=f"Ecu{n}", gateway=GatewayConfig()))
        self.refresh_nodes(f"ecu:{len(self.cfg.ecus) - 1}")

    def add_peer(self):
        self.store()
        self.cfg.peers.append(PeerNode(name=f"Peer{len(self.cfg.peers) + 1}"))
        self.refresh_nodes(f"peer:{len(self.cfg.peers) - 1}")

    def remove_node(self):
        kind, i = self._selected_node()
        if kind == "net":
            return
        self._store = None
        lst = self.cfg.ecus if kind == "ecu" else self.cfg.peers
        name = lst[i].name
        if not messagebox.askyesno(TITLE, f"Remove {name} from the topology?", parent=self.win):
            return
        del lst[i]
        if self.cfg.default_peer == name:
            self.cfg.default_peer = ""
        self.refresh_nodes("net")

    def move_node(self, step):
        """ECU order decides the order of automatic header ids (first ECU first)."""
        kind, i = self._selected_node()
        if kind == "net":
            return
        self.store()
        lst = self.cfg.ecus if kind == "ecu" else self.cfg.peers
        j = i + step
        if 0 <= j < len(lst):
            lst[i], lst[j] = lst[j], lst[i]
            self.refresh_nodes(f"{kind}:{j}")

    def _rename(self, old, new):
        if not old or old == new:
            return
        cfg = self.cfg
        if cfg.default_peer == old:
            cfg.default_peer = new
        fix = lambda text: "/".join([new if k == 0 and p == old else p for k, p in enumerate(text.split("/"))])
        cfg.routes = {" -> ".join(fix(x) for x in k.split(" -> ")): v for k, v in cfg.routes.items()}
        for link in cfg.links:
            link["src"], link["dst"] = fix(link.get("src", "")), fix(link.get("dst", ""))

    # ------------------------------------------------------------------ editor panels
    def _grid(self, parent, rows):
        for r, (label, widget) in enumerate(rows):
            ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", pady=2, padx=(0, 6))
            if widget is not None:
                widget.grid(row=r, column=1, sticky="w", pady=2)

    def _panel_network(self):
        cfg, te = self.cfg, self.cfg.ethernet
        self.panel.config(text="Network settings")
        f = ttk.Frame(self.panel)
        f.pack(side="left", fill="y", anchor="n")
        v = {k: tk.StringVar(value=x) for k, x in (
            ("name", cfg.name), ("vlan", _port_text(te.vlan_id)), ("channel", te.channel), ("mask", te.netmask),
            ("tx", str(cfg.tx_port)), ("rx", str(cfg.rx_port)), ("proto", te.protocol or "UDP"),
            ("dp", cfg.default_peer))}
        v_one = tk.BooleanVar(value=cfg.one_socket)
        e_rx = ttk.Entry(f, textvariable=v["rx"], width=8, state="disabled" if cfg.one_socket else "normal")
        one_cb = ttk.Checkbutton(f, text="One socket per node for both directions", variable=v_one,
                                 command=lambda: e_rx.configure(state="disabled" if v_one.get() else "normal"))
        Tooltip(one_cb, "Every node sends and receives on one port (SA_<node>_CanGw); one socket connection per "
                        "pair of nodes carries both directions. Off: sends from the port, receives on the receive "
                        "port (two sockets).")
        e_ch = ttk.Entry(f, textvariable=v["channel"], width=36)
        Tooltip(e_ch, "Ethernet channel to use in bases that already have Ethernet (path, short name or VLANnn). "
                      "Empty = the channel the ECU is connected to.")
        v_table, v_hw = tk.StringVar(value=cfg.routing_table), tk.BooleanVar(value=cfg.table_hw)
        tf = ttk.Frame(f)
        e_table = ttk.Entry(tf, textvariable=v_table, width=36)
        e_table.pack(side="left")
        ttk.Button(tf, text="…", width=3, command=lambda: self._browse_table(v_table)).pack(side="left", padx=(2, 0))
        Tooltip(e_table, "Routing table of the customer (.xlsx / .csv) given with the DBC files. When set, the CAN -> "
                         "CAN routes of every ECU come only from it (message rows: PduR, signal rows: Com signal "
                         "gateway in the .vsde file). Empty = messages paired by name / CAN id")
        hw_cb = ttk.Checkbutton(f, text="Route HW accelerator rows too", variable=v_hw)
        Tooltip(hw_cb, "Off: rows with HW-Accelerator = 1 are left to the LLCE / PFE")
        self._grid(f, [("Name:", ttk.Entry(f, textvariable=v["name"], width=36)),
                       ("VLAN id (new channels):", ttk.Entry(f, textvariable=v["vlan"], width=8)),
                       ("Existing channel:", e_ch),
                       ("Netmask:", ttk.Entry(f, textvariable=v["mask"], width=16)),
                       ("Protocol:", ttk.Combobox(f, textvariable=v["proto"], values=("UDP", "TCP"), width=6,
                                                  state="readonly")),
                       ("", one_cb),
                       ("Port of every node:", ttk.Entry(f, textvariable=v["tx"], width=8)),
                       ("Receive port (two sockets):", e_rx),
                       ("Default peer:", ttk.Combobox(f, textvariable=v["dp"], values=[""] + [p.name for p in cfg.peers],
                                                      width=20, state="readonly")),
                       ("Routing table (CAN -> CAN):", tf),
                       ("", hw_cb)])
        o = ttk.LabelFrame(self.panel, text="ECU -> ECU routes", padding=6)
        o.pack(side="left", fill="y", anchor="n", padx=(16, 0))
        b = {k: tk.BooleanVar(value=x) for k, x in (("also", cfg.cross.also_to_default_peer),
                                                     ("from", cfg.cross.from_default_peer),
                                                     ("match", cfg.cross.match_id))}
        ttk.Checkbutton(o, text="A message routed ECU -> ECU is also sent to the default peer",
                        variable=b["also"]).pack(anchor="w")
        ttk.Checkbutton(o, text="A message an ECU sends without a source in another ECU comes from the default "
                                "peer", variable=b["from"]).pack(anchor="w")
        ttk.Checkbutton(o, text="Also pair renamed messages (same CAN id, id type and length)",
                        variable=b["match"]).pack(anchor="w")
        ttk.Label(o, text="Messages one ECU receives and another ECU sends are routed directly between them (same\n"
                          "rules as CAN -> CAN: name, name without gateway prefix, CAN id). Both ECUs get the same\n"
                          "Ethernet PDU, header id, IP addresses and ports. Header ids are kept in <topology>.lock.json\n"
                          "so that ECUs generated later (or by another team) get the same values.",
                  foreground="#666666").pack(anchor="w", pady=(8, 0))

        def store():
            cfg.name = v["name"].get().strip()
            te.vlan_id = _int_or_none(v["vlan"].get())
            te.channel = v["channel"].get().strip()
            te.netmask = v["mask"].get().strip() or "255.255.255.0"
            te.protocol = v["proto"].get() or "UDP"
            cfg.tx_port = _int_or_none(v["tx"].get()) or 50000
            cfg.rx_port = _int_or_none(v["rx"].get()) or 50001
            cfg.one_socket = v_one.get()
            cfg.default_peer = v["dp"].get()
            cfg.cross.also_to_default_peer = b["also"].get()
            cfg.cross.from_default_peer = b["from"].get()
            cfg.cross.match_id = b["match"].get()
            cfg.routing_table = v_table.get().strip()
            cfg.table_hw = v_hw.get()
        self._store = store

    def _browse_table(self, var):
        p = filedialog.askopenfilename(parent=self.win, title="Routing table",
                                       filetypes=[("Routing table", "*.xlsx *.xlsm *.csv *.tsv *.txt"),
                                                  ("All files", "*.*")])
        if p:
            var.set(os.path.normpath(p))

    def _table_networks(self) -> list[str]:
        path = self.cfg.routing_table
        if not path or not os.path.isfile(path):
            return []
        from .. import routing_table
        try:
            return routing_table.read(path).networks
        except Exception:  # noqa: BLE001 - the analysis reports the problem
            return []

    def _ports_row(self, f, node):
        pf = ttk.Frame(f)
        v_tx, v_rx = tk.StringVar(value=_port_text(node.tx_port)), tk.StringVar(value=_port_text(node.rx_port))
        ttk.Entry(pf, textvariable=v_tx, width=8).pack(side="left")
        ttk.Label(pf, text=" / ").pack(side="left")
        ttk.Entry(pf, textvariable=v_rx, width=8, state="disabled" if self.cfg.one_socket else "normal").pack(
            side="left")
        ttk.Label(pf, text=f"  (empty = {self.cfg.tx_port}" + ("; one socket: the first port only)" if
                           self.cfg.one_socket else f" / {self.cfg.rx_port})"), foreground="#666666").pack(side="left")
        return pf, v_tx, v_rx

    def _panel_peer(self, p: PeerNode):
        self.panel.config(text=f"Peer {p.name}")
        f = ttk.Frame(self.panel)
        f.pack(side="left", fill="y", anchor="n")
        v_name, v_ip = tk.StringVar(value=p.name), tk.StringVar(value=p.ip)
        v_dp = tk.BooleanVar(value=self.cfg.default_peer == p.name)
        pf, v_tx, v_rx = self._ports_row(f, p)
        self._grid(f, [("Name:", ttk.Entry(f, textvariable=v_name, width=30)),
                       ("IP address:", ttk.Entry(f, textvariable=v_ip, width=16)),
                       ("Ports (sends / receives):", pf),
                       ("", ttk.Checkbutton(f, text="Default peer (gets the messages no other ECU needs)",
                                            variable=v_dp))])
        ttk.Label(self.panel, text="An Ethernet-only node, for example a central computer that is also the switch.\n"
                                   "No file is generated for it; the contract lists what it sends and receives.",
                  foreground="#666666").pack(side="left", anchor="n", padx=16)

        def store():
            old = p.name
            p.name, p.ip = v_name.get().strip(), v_ip.get().strip()
            p.tx_port, p.rx_port = _int_or_none(v_tx.get()), _int_or_none(v_rx.get())
            self._rename(old, p.name)
            if v_dp.get():
                self.cfg.default_peer = p.name
            elif self.cfg.default_peer == p.name:
                self.cfg.default_peer = ""
            self._refresh_row()
        self._store = store

    def _panel_ecu(self, e: EcuNode):
        g = e.gateway
        self.panel.config(text=f"ECU {e.name}")
        f = ttk.Frame(self.panel)
        f.pack(side="left", fill="both", expand=True, anchor="n")
        f.columnconfigure(1, weight=1)
        v = {k: tk.StringVar(value=x) for k, x in (("name", e.name), ("ip", e.ip), ("base", g.base),
                                                   ("out", g.output), ("schema", g.schema or DEFAULT_SCHEMA),
                                                   ("ecu", g.ecu), ("prev", g.previous))}
        v_only = tk.BooleanVar(value=g.options.dbc_imported)
        v_gen = tk.BooleanVar(value=e.generate)
        pf, v_tx, v_rx = self._ports_row(f, e)
        bf = ttk.Frame(f)
        ttk.Entry(bf, textvariable=v["base"], width=70).pack(side="left", fill="x", expand=True)
        ttk.Button(bf, text="Browse…", command=lambda: self._browse_base(v["base"])).pack(side="left", padx=4)
        of = ttk.Frame(f)
        ttk.Entry(of, textvariable=v["out"], width=70).pack(side="left", fill="x", expand=True)
        ttk.Button(of, text="Browse…", command=lambda: self._browse_out(v["out"], e)).pack(side="left", padx=4)
        pf2 = ttk.Frame(f)
        ttk.Entry(pf2, textvariable=v["prev"], width=70).pack(side="left", fill="x", expand=True)
        ttk.Button(pf2, text="Browse…", command=lambda: self._browse_prev(v["prev"], e)).pack(side="left", padx=4)
        sf = ttk.Frame(f)
        ttk.Combobox(sf, textvariable=v["schema"], values=SCHEMAS, width=16, state="readonly").pack(side="left")
        ttk.Label(sf, text="  ECU instance:").pack(side="left")
        ttk.Entry(sf, textvariable=v["ecu"], width=24).pack(side="left")
        ttk.Label(sf, text="  (new file: empty = ECU name; network file: empty = auto)",
                  foreground="#666666").pack(side="left")
        self._grid(f, [("Name:", ttk.Entry(f, textvariable=v["name"], width=30)),
                       ("IP address:", ttk.Entry(f, textvariable=v["ip"], width=16)),
                       ("Ports (sends / receives):", pf),
                       ("DaVinci project (optional):", bf),
                       ("Gateway file made before:", pf2),
                       ("Gateway file to write:", of),
                       ("Schema:", sf),
                       ("", ttk.Checkbutton(f, text="Generate the gateway file of this ECU (otherwise it is only "
                                                    "referenced: its DBC / project tells what it needs and sends)",
                                            variable=v_gen)),
                       ("", ttk.Checkbutton(f, text="Gateway only: the DBC files are imported in the ECU's DaVinci "
                                                    "project (without project: the CAN part is referenced by the names "
                                                    "DaVinci gives)", variable=v_only))])
        for w in (bf, pf2, of, sf):
            w.grid_configure(sticky="we")
        lf = ttk.LabelFrame(f, text="CAN buses (one DBC or project channel per bus)", padding=4)
        lf.grid(row=9, column=0, columnspan=2, sticky="nsew", pady=(6, 0))
        cols = ("DBC file / channel", "Node", "Bus name")
        tb = ttk.Treeview(lf, columns=cols, show="headings", height=3)
        for c, wd in zip(cols, (420, 140, 140)):
            tb.heading(c, text=c, anchor="w")
            tb.column(c, width=wd, anchor="w")
        tb.pack(side="left", fill="both", expand=True)
        bb = ttk.Frame(lf)
        bb.pack(side="left", fill="y", padx=(6, 0))

        def fill_buses():
            tb.delete(*tb.get_children())
            for i, b in enumerate(g.buses):
                tb.insert("", "end", iid=str(i), values=(os.path.basename(b.dbc) if b.dbc else b.channel,
                                                         b.node or "(project ECU)", b.bus or "(auto)"))

        def edit_bus(i=None):
            store()
            base, proj_ecu = self._ecu_base(g)
            bus = BusInput() if i is None else g.buses[i]
            d = BusDialog(self.win, bus, base, self.dbc_cache, proj_ecu, networks=self._table_networks())
            self.win.wait_window(d)
            if d.result is not None and i is None:
                g.buses.append(d.result)
            fill_buses()

        def remove_bus():
            sel = tb.selection()
            if sel:
                del g.buses[int(sel[0])]
                fill_buses()
        ttk.Button(bb, text="Add bus…", command=edit_bus).pack(fill="x")
        ttk.Button(bb, text="Edit…", command=lambda: tb.selection() and edit_bus(int(tb.selection()[0]))).pack(
            fill="x", pady=3)
        ttk.Button(bb, text="Remove", command=remove_bus).pack(fill="x")
        tb.bind("<Double-1>", lambda _e: tb.selection() and edit_bus(int(tb.selection()[0])))
        fill_buses()

        def store():
            old = e.name
            e.name, e.ip, e.generate = v["name"].get().strip(), v["ip"].get().strip(), v_gen.get()
            e.tx_port, e.rx_port = _int_or_none(v_tx.get()), _int_or_none(v_rx.get())
            g.base, g.output = v["base"].get().strip(), v["out"].get().strip()
            g.schema, g.ecu = v["schema"].get() or DEFAULT_SCHEMA, v["ecu"].get().strip()
            g.previous, g.options.dbc_imported = v["prev"].get().strip(), v_only.get()
            self._rename(old, e.name)
            self._refresh_row()
        self._store = store

    def _refresh_row(self):
        """Show edited names / addresses without rebuilding the list (that would change the selection)."""
        for iid in self.t_nodes.get_children():
            self.t_nodes.item(iid, values=self._row(iid))

    def _browse_base(self, var):
        p = filedialog.askopenfilename(parent=self.win, title="Network file or DaVinci project",
                                       filetypes=[("Network file / DaVinci project", "*.arxml *.dpa"),
                                                  ("All files", "*.*")])
        if p:
            var.set(os.path.normpath(p))

    def _browse_prev(self, var, e):
        p = filedialog.askopenfilename(parent=self.win, title=f"Gateway file of {e.name} made before",
                                       filetypes=[("AUTOSAR XML", "*.arxml"), ("All files", "*.*")])
        if p:
            var.set(os.path.normpath(p))

    def _browse_out(self, var, e):
        p = filedialog.asksaveasfilename(parent=self.win, title=f"Gateway file of {e.name}",
                                         defaultextension=".arxml", initialfile=f"{e.name}_Gateway.arxml",
                                         filetypes=[("AUTOSAR XML", "*.arxml"), ("All files", "*.*")])
        if p:
            var.set(os.path.normpath(p))

    def _ecu_base(self, g: GatewayConfig):
        """Base of an ECU for the bus dialog (channels of a network file / project)."""
        if not g.base:
            return None, ""
        key = os.path.abspath(g.base)
        if key not in self._gui_bases:
            self.win.config(cursor="watch")
            self.win.update_idletasks()
            try:
                plain = GatewayConfig(base=g.base, output=g.output)
                self._gui_bases[key] = load_base(plain)
            except (OSError, ValueError) as exc:
                messagebox.showerror(TITLE, f"Cannot read {g.base}:\n{exc}", parent=self.win)
                return None, ""
            finally:
                self.win.config(cursor="")
        base = self._gui_bases[key]
        proj = dvproject.read(g.base) if dvproject.is_project(g.base) else None
        return base, (proj.ecu_path if proj else "")

    # ------------------------------------------------------------------ analyze / generate
    def analyze(self, then=None):
        self.store()
        if not self.cfg.ecus:
            self.show_messages(infos=["Add the ECUs of the network (Add ECU) and the peers (Add peer)."])
            return
        cfg = self.cfg
        filled = self.fill_from_nodes()
        if filled:
            self.refresh_nodes("net")

        def done(tplan):
            self.tplan = tplan
            self.fill_tables()
            self.show_messages(tplan.errors, tplan.warnings, filled + tplan.infos)
            self.status.config(text=f"{len(tplan.enabled_cross)} ECU -> ECU route(s), {len(tplan.links)} Ethernet "
                                    f"PDU(s), {len(tplan.warnings)} warning(s), {len(tplan.errors)} error(s)")
            if then and tplan.ok:
                then(tplan)
        self._run("Analyzing", lambda: make_topology_plan(cfg, self.bases, self.dbc_cache), done)

    def generate(self):
        self.store()
        if not self.cfg.path:
            messagebox.showinfo(TITLE, "Save the topology first: the lock file and the contract are written next "
                                       "to it.", parent=self.win)
            if not self.save_as():
                return
        else:
            self.save()
        names = [e.name for e in self.cfg.ecus if e.generate]
        if not names:
            messagebox.showwarning(TITLE, "No ECU is marked 'generate'.", parent=self.win)
            return

        def write(tplan):
            def done(results):
                lines = [f"{name}: {os.path.basename(r.output)} "
                         f"({report.count_text(len(r.routes), len(r.can_routes))})" for name, r in results]
                exts = [r.extension for _n, r in results if r.extension]
                self.show_messages([], [], [f"Written {r.output}" for _n, r in results] +
                                   [f"Written {x} (CAN -> CAN, PduR only, no Com)" for x in exts] +
                                   [f"Lock file: {self.cfg.lock_path}", f"Contract: {self.cfg.contract_path}"])
                self.status.config(text=f"Written {len(results)} gateway file(s)")
                messagebox.showinfo(TITLE, "Written:\n" + "\n".join(lines) + "\n\nImport each file into the "
                                    "DaVinci project of its ECU (Input Files; a file of a project ECU is an additional "
                                    "input file next to its DBC files) and run Update. Keep the topology file and "
                                    "its lock file together: other ECUs generated later get the same header ids." +
                                    ("\n\nCAN -> CAN routes (PduR only, no Com): add " +
                                     ", ".join(os.path.basename(x) for x in exts) + " to the Input Files of the "
                                     "project next to its DBC files (once)." if exts else ""),
                                    parent=self.win)
                self.analyze()
            self._run("Generating", lambda: generate_topology(tplan), done)
        self.analyze(then=write)

    def message_report(self):
        from ..paths import report_of_topology, write_report

        def write(tplan):
            stem = os.path.splitext(self.cfg.path)[0] if self.cfg.path else os.path.join(os.getcwd(), "topology")
            try:
                files = write_report(report_of_topology(tplan), stem)
            except OSError as exc:
                messagebox.showerror(TITLE, f"Cannot write the report:\n{exc}", parent=self.win)
                return
            self.show_messages(infos=[f"Message paths: {files[0]}", f"Message paths (CSV): {files[1]}"])
            if hasattr(os, "startfile"):
                os.startfile(files[0])
        self.analyze(then=write)

    # ------------------------------------------------------------------ tables
    def fill_tables(self):
        for t in (self.t_cross, self.t_routes, self.t_contract, self.t_paths):
            t.delete(*t.get_children())
        tp = self.tplan
        if tp is None:
            return
        from ..paths import report_of_topology
        try:
            rep = report_of_topology(tp)
        except Exception:  # noqa: BLE001 - the tab is informative only
            rep = None
        last, band = None, 0
        for i, p in enumerate(rep.paths if rep else []):
            if (p.can_id, p.names) != last:
                band, last = 1 - band, (p.can_id, p.names)
            self.t_paths.insert("", "end", iid=str(i), values=p.row, tags=("band",) if band else ())
        self._cross_by_iid = {}
        for i, (c, row) in enumerate(zip(tp.cross, treport.cross_rows(tp))):
            tags = ("off",) if not c.enabled else (("new",) if c.change == "new" else ())
            self.t_cross.insert("", "end", iid=str(i), values=row, tags=tags)
            self._cross_by_iid[str(i)] = c
        for i, row in enumerate(contract_rows(tp)):
            tags = ("conflict",) if str(row[-1]).startswith("conflict") else ()
            self.t_contract.insert("", "end", iid=str(i), values=row, tags=tags)
        self.fill_routes()

    def fill_routes(self):
        t = self.t_routes
        t.delete(*t.get_children())
        self._route_by_iid = {}
        kind, i = self._selected_node()
        if self.tplan is None or kind != "ecu" or i >= len(self.cfg.ecus):
            return
        plan = self.tplan.plans.get(self.cfg.ecus[i].name)
        if plan is None:
            return
        for k, (r, row) in enumerate(report.route_items(plan)):
            off = getattr(r, "enabled", True) is False
            tags = ("removed",) if row[1] == "removed" else (("off",) if off else (
                ("conflict",) if getattr(r, "header_clash", "") else ()))
            t.insert("", "end", iid=str(k), values=row, tags=tags)
            self._route_by_iid[str(k)] = r

    def show_messages(self, errors=(), warnings=(), infos=()):
        t = self.t_msg
        t.configure(state="normal")
        t.delete("1.0", "end")
        for tag, items in (("error", errors), ("warning", warnings), ("info", infos)):
            for m in items:
                t.insert("end", f"[{tag.upper()}] {m}\n", tag)
        t.configure(state="disabled")

    # ------------------------------------------------------------------ ECU -> ECU table
    def _selected_cross(self):
        return [self._cross_by_iid[i] for i in self.t_cross.selection() if i in getattr(self, "_cross_by_iid", {})]

    def toggle_cross(self, value=None):
        sel = self._selected_cross()
        for c in sel:
            over = dict(self.cfg.routes.get(c.key, {}))
            over["enabled"] = (not c.enabled) if value is None else value
            self.cfg.routes[c.key] = over
        if sel:
            self.analyze()

    def edit_cross(self):
        sel = self._selected_cross()
        if not sel:
            return
        c = sel[0]
        d = CrossDialog(self.win, c, self.cfg.routes.get(c.key, {}), self.cfg.default_peer,
                        self.cfg.cross.also_to_default_peer)
        self.win.wait_window(d)
        if d.result is not None:
            self.cfg.routes[c.key] = d.result
            self.analyze()

    def cross_menu(self, ev):
        iid = self.t_cross.identify_row(ev.y)
        if iid and iid not in self.t_cross.selection():
            self.t_cross.selection_set(iid)
        m = tk.Menu(self.win, tearoff=False)
        m.add_command(label="Enable", command=lambda: self.toggle_cross(True))
        m.add_command(label="Disable", command=lambda: self.toggle_cross(False))
        m.add_command(label="Edit…", command=self.edit_cross)
        m.add_separator()
        m.add_command(label="Add link…", command=self.add_link)
        if any(c.match == "link" for c in self._selected_cross()):
            m.add_command(label="Remove link", command=self.remove_links)
        m.add_command(label="Reset overrides of selected", command=self.reset_cross)
        m.tk_popup(ev.x_root, ev.y_root)

    def reset_cross(self):
        for c in self._selected_cross():
            self.cfg.routes.pop(c.key, None)
        self.analyze()

    def add_link(self):
        if self.tplan is None:
            return
        d = LinkDialog(self.win, self.tplan)
        self.win.wait_window(d)
        if d.result is not None:
            self.cfg.links = [x for x in self.cfg.links if x.get("dst") != d.result["dst"]] + [d.result]
            self.analyze()

    def remove_links(self):
        dead = {f"{c.dst_ecu}/{c.dst.key}" for c in self._selected_cross() if c.match == "link"}
        self.cfg.links = [x for x in self.cfg.links if x.get("dst") not in dead]
        self.analyze()

    # ------------------------------------------------------------------ routes of one ECU
    def toggle_routes(self, value=None):
        kind, i = self._selected_node()
        if kind != "ecu" or self.tplan is None:
            return
        node = self.cfg.ecus[i]
        plan = self.tplan.plans.get(node.name)
        changed = False
        for iid in self.t_routes.selection():
            r = self._route_by_iid.get(iid)
            if r is None or not hasattr(r, "bus") or not hasattr(r, "message"):
                continue
            k = next((n for n, b in enumerate(plan.cfg.buses) if b is r.bus.cfg), None)
            if k is None or k >= len(node.gateway.buses):
                continue
            msgs = node.gateway.buses[k].messages
            over = dict(msgs.get(r.message.name, {}))
            over["enabled"] = (not r.enabled) if value is None else value
            msgs[r.message.name] = over
            changed = True
        if changed:
            self.analyze()

    def routes_menu(self, ev):
        iid = self.t_routes.identify_row(ev.y)
        if iid and iid not in self.t_routes.selection():
            self.t_routes.selection_set(iid)
        m = tk.Menu(self.win, tearoff=False)
        m.add_command(label="Enable message", command=lambda: self.toggle_routes(True))
        m.add_command(label="Disable message", command=lambda: self.toggle_routes(False))
        m.tk_popup(ev.x_root, ev.y_root)


def open_topology(master: tk.Misc | None = None, path: str | None = None, select: str | None = None):
    """Open the topology window in a Toplevel of *master* (EcucStudio) or in its own root window."""
    from ...gui.startup import bring_to_front, show_main_window
    if master is None:
        root = tk.Tk()
        init_style(root)
        root.topology = TopologyWindow(root, path, select)
        show_main_window(root, TITLE, None, "1450x920")
        return root
    top = tk.Toplevel(master)
    top.geometry("1450x920")
    top.topology = TopologyWindow(top, path, select)
    bring_to_front(top)
    return top


def main(path: str | None = None):
    root = open_topology(None, path)
    root.mainloop()


__all__ = ["TopologyWindow", "open_topology", "main"]
