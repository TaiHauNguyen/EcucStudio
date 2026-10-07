"""Start wizard: the gateway file of one ECU of the network.

Inputs:

1. the DBC files of the whole network (mandatory). The gateway ECUs are found from the node names (DBC files with the
   same gateway node belong to one ECU); the other ECUs tell where messages go (ECU -> ECU over Ethernet);
2. the DaVinci project of the target ECU (optional): its DBC files are imported there; the tool reads the CAN part
   from it. Without project the CAN part DaVinci creates from the DBC files is predicted (gateway/imported.py);
3. the gateway file generated before for the target ECU (optional): it is updated (DaVinci keeps what does not change).

The output is always one gateway file (Ethernet + gateway only) for the target ECU. The network (DBC files, ECUs, IP
addresses, ports) is saved in a network file shared by all ECUs, with a lock file that keeps the header ids.
"""
from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, ttk

from ..gui.theme import COLORS
from . import dbcread, start

TITLE = "Start - CAN Gateway Generator"
STARTS = (
    ("new", "Start from the DBC files of the network",
     "Add the DBC files of all CAN buses of the network. The tool finds the gateway ECUs from their node names, you "
     "enter the IP addresses once (saved in a network file), then choose the ECU to generate the gateway file for."),
    ("open", "Open the network file saved before",
     "The DBC files, ECUs and IP addresses of the network are already saved: choose the ECU (for example the next "
     "one) and generate its gateway file."),
    ("update", "Update a gateway file generated before",
     "Change the gateway file of an ECU (DBC files changed, messages added or removed): the same file is written "
     "again; in DaVinci run Update, what did not change keeps its configuration."),
)


def _int(text):
    try:
        return int(str(text).strip(), 0)
    except (TypeError, ValueError):
        return None


class StartWizard(tk.Toplevel):
    def __init__(self, master, on_finish=None, on_topology=None, on_open=None, dbc_cache=None,
                 on_topology_file=None, initial_case=None, on_open_gateway=None):
        super().__init__(master)
        self.title(TITLE)
        self.transient(master)
        height = min(660, max(480, self.winfo_screenheight() - 120))
        self.geometry("940x%d+%d+%d" % (height, master.winfo_rootx() + 60, max(0, master.winfo_rooty() + 20)))
        self.minsize(840, 460)
        self.on_topology_file = on_topology_file      # (network file, target ECU): open the result
        self.on_open, self.on_topology = on_open, on_topology
        self.on_open_gateway = on_open_gateway        # a gateway file without network file (single ECU, older)
        self.cache = dbc_cache if dbc_cache is not None else {}
        self.case = tk.StringVar(value="")
        self.flow, self.step, self.leave = [], 0, None
        self.s = {}
        self._q, self._busy = queue.Queue(), False
        head = tk.Frame(self, background="#ffffff")
        head.pack(fill="x")
        self.h_title = tk.Label(head, text="", font=("Segoe UI", 12, "bold"), background="#ffffff", anchor="w")
        self.h_title.pack(fill="x", padx=14, pady=(10, 0))
        self.h_text = tk.Label(head, text="", background="#ffffff", anchor="w", justify="left", wraplength=890,
                               foreground="#444444")
        self.h_text.pack(fill="x", padx=14, pady=(2, 10))
        ttk.Separator(self).pack(fill="x")
        foot = ttk.Frame(self, padding=(14, 8))         # packed before the page: never pushed out of the window
        foot.pack(side="bottom", fill="x")
        ttk.Separator(self).pack(side="bottom", fill="x")
        self.body = ttk.Frame(self, padding=14)
        self.body.pack(fill="both", expand=True)
        self.bind("<Return>", lambda _e: self.next())
        self.f_status = ttk.Label(foot, text="", foreground="#666666")
        self.f_status.pack(side="left")
        ttk.Button(foot, text="Cancel", command=self.destroy).pack(side="right")
        self.b_next = ttk.Button(foot, text="Next >", command=self.next)
        self.b_next.pack(side="right", padx=6)
        self.b_back = ttk.Button(foot, text="< Back", command=self.back)
        self.b_back.pack(side="right")
        self.after(100, self._poll)
        self.flow = [self._page_start]
        if initial_case in ("new", "dbc"):
            self.case.set("new")
            self.flow += self._pages("new")
            self.step = 1
        self._show()

    def _pages(self, case):
        return {"new": [self._page_dbcs, self._page_network, self._page_target],
                "open": [self._page_open, self._page_dbcs, self._page_network, self._page_target],
                "update": [self._page_update, self._page_dbcs, self._page_network, self._page_target]}[case]

    # ------------------------------------------------------------------ paging
    def _scroll_area(self, parent, height=120):
        """Frame inside *parent* that scrolls vertically when its content is taller than the space it gets."""
        outer = ttk.Frame(parent)
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, highlightthickness=0, height=height, background=COLORS.get("bg", "#f0f0f0"))
        bar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side="left", fill="both", expand=True)

        def resize(_e=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfigure(win, width=canvas.winfo_width())
            if inner.winfo_reqheight() > canvas.winfo_height():
                bar.pack(side="right", fill="y")
            else:
                bar.pack_forget()
        inner.bind("<Configure>", resize)
        canvas.bind("<Configure>", resize)
        wheel = lambda e: canvas.yview_scroll(int(-e.delta / 120) or (-1 if e.delta > 0 else 1), "units")
        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", wheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))
        return inner

    def _show(self):
        for w in self.body.winfo_children():
            w.destroy()
        self.leave = None
        self.flow[self.step]()
        last = self.step == len(self.flow) - 1 and self.step > 0
        self.b_next.config(text="Finish" if last else "Next >")
        self.b_back.state(["!disabled"] if self.step else ["disabled"])

    def header(self, title, text):
        n, total = self.step, len(self.flow) - 1
        self.h_title.config(text=f"{n}/{total}  {title}" if n else title)
        self.h_text.config(text=text)

    def next(self):
        if self._busy:
            return
        if self.leave is not None and not self.leave():
            return
        if self.step == 0:
            case = self.case.get()
            if not case:
                messagebox.showinfo(TITLE, "Choose how to start.", parent=self)
                return
            self.flow = [self._page_start] + self._pages(case)
        if self.step == len(self.flow) - 1:
            self.finish()
            return
        self.step += 1
        self._show()

    def back(self):
        if self.step and not self._busy:
            self.step -= 1
            self._show()

    # ------------------------------------------------------------------ background work
    def _bg(self, label, fn, done):
        self._busy = True
        self.f_status.config(text=label + " …")
        self.config(cursor="watch")

        def work():
            try:
                self._q.put((done, fn(), None))
            except Exception as exc:  # noqa: BLE001 - shown in the wizard
                self._q.put((done, None, (exc, traceback.format_exc())))
        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        if not self.winfo_exists():
            return
        try:
            while True:
                done, res, err = self._q.get_nowait()
                self._busy = False
                self.config(cursor="")
                self.f_status.config(text="")
                if err:
                    messagebox.showerror(TITLE, str(err[0]), parent=self)
                else:
                    done(res)
        except queue.Empty:
            pass
        self.after(100, self._poll)

    # ------------------------------------------------------------------ start
    def _page_start(self):
        self.header("CAN gateway file of an ECU",
                    "Inputs: the DBC files of the whole network (always), the DaVinci project of the ECU (optional: "
                    "its DBC files are imported there) and its gateway file made before (optional: to update it). "
                    "The output is the gateway file of that ECU: only Ethernet + gateway, the CAN part of the DBC "
                    "files is referenced, so nothing is imported twice in DaVinci.")
        for key, title, text in STARTS:
            card = ttk.Frame(self.body, padding=(4, 6))
            card.pack(fill="x", pady=4)
            rb = ttk.Radiobutton(card, text=title, value=key, variable=self.case)
            rb.pack(anchor="w")
            lbl = ttk.Label(card, text=text, foreground="#555555", wraplength=820, justify="left")
            lbl.pack(anchor="w", padx=(22, 0))
            lbl.bind("<Button-1>", lambda _e, k=key: self.case.set(k))
            lbl.bind("<Double-1>", lambda _e, k=key: (self.case.set(k), self.next()))
            rb.bind("<Double-1>", lambda _e: self.next())
        more = ttk.Frame(self.body)
        more.pack(fill="x", pady=(16, 0))
        ttk.Label(more, text="Other:", foreground="#666666").pack(side="left")
        if self.on_open:
            ttk.Button(more, text="Open a generator configuration (.json)…",
                       command=lambda: (self.destroy(), self.on_open())).pack(side="left", padx=6)
        ttk.Button(more, text="Manual settings (generator window)", command=self.destroy).pack(side="left")

    # ------------------------------------------------------------------ open a network file
    def _page_open(self):
        self.header("Network file", "Select the network file (.json) saved before; its DBC files, ECUs and IP "
                                    "addresses are shown on the next pages.")
        v = self.s.setdefault("netfile", tk.StringVar())
        f = self.body
        row = ttk.Frame(f)
        row.pack(fill="x")
        ttk.Label(row, text="Network file:").pack(side="left")
        ttk.Entry(row, textvariable=v, width=80).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(row, text="Browse…", command=self._browse_network).pack(side="left")
        self.o_summary = ttk.Label(f, text="", justify="left", wraplength=880, font=("Consolas", 9))
        self.o_summary.pack(anchor="w", pady=(14, 0))
        if self.s.get("topo"):
            self._show_network_summary()

        def leave():
            if self.s.get("topo") is None:
                messagebox.showinfo(TITLE, "Select the network file.", parent=self)
                return False
            return True
        self.leave = leave

    def _browse_network(self):
        p = filedialog.askopenfilename(parent=self, title="Network file",
                                       filetypes=[("Network file", "*.json"), ("All files", "*.*")])
        if p:
            self._load_network(p)

    def _load_network(self, path, target=None, previous=None):
        from .topology import TopologyConfig
        try:
            t = TopologyConfig.load(path)
        except (OSError, ValueError) as exc:
            messagebox.showerror(TITLE, f"Cannot read {path}:\n{exc}", parent=self)
            return False
        if not t.ecus:
            messagebox.showerror(TITLE, f"{os.path.basename(path)} has no ECU: is it a network file?", parent=self)
            return False
        self.s.setdefault("netfile", tk.StringVar()).set(path)
        self.s["topo"] = t
        self.s.pop("tvars", None)
        self._dbcs_from_topology(t)
        self._network_from_topology(t)
        if target:
            self.s["target_name"] = target
        if previous:
            self.s["target_prev"] = previous
        if getattr(self, "o_summary", None) is not None and self.o_summary.winfo_exists():
            self._show_network_summary()
        return True

    def _show_network_summary(self):
        t = self.s["topo"]
        lines = [f"{os.path.basename(t.path)}: {len(t.ecus)} ECU(s)"]
        for e in t.ecus:
            buses = ", ".join(os.path.basename(b.dbc) for b in e.gateway.buses if b.dbc) or "(project)"
            lines.append(f"   {e.name:<14} {e.ip:<16} {buses}")
        if self.s.get("target_name"):
            lines.append(f"\nGateway file of {self.s['target_name']}: {self.s.get('target_prev', '')}")
        self.o_summary.config(text="\n".join(lines))

    def _dbcs_from_topology(self, t):
        rows = []
        for e in t.ecus:
            for b in e.gateway.buses:
                if not b.dbc or not os.path.isfile(b.dbc):
                    continue
                key = os.path.abspath(b.dbc)
                try:
                    if key not in self.cache:
                        self.cache[key] = dbcread.load(b.dbc)
                except Exception:  # noqa: BLE001 - shown as missing on the DBC page
                    continue
                rows.append({"path": key, "db": self.cache[key], "node": tk.StringVar(value=b.node),
                             "ecu": tk.StringVar(value=e.name), "reason": "", "chosen": True})
        self.s["dbcs"] = rows

    def _network_from_topology(self, t):
        st = self.s.setdefault("net", {})
        for k, x in (("vlan", "" if t.ethernet.vlan_id is None else str(t.ethernet.vlan_id)), ("tx", str(t.tx_port)),
                     ("rx", str(t.rx_port))):
            st.setdefault(k, tk.StringVar()).set(x)
        peer = next((p for p in t.peers if p.name == t.default_peer), None)
        st.setdefault("peer", tk.BooleanVar()).set(peer is not None)
        st.setdefault("pname", tk.StringVar()).set(peer.name if peer else "Central")
        st.setdefault("pip", tk.StringVar()).set(peer.ip if peer else "")
        ecus = st.setdefault("ecus", {})
        for e in t.ecus:
            ecus.setdefault(e.name, {"ip": tk.StringVar()})["ip"].set(e.ip)
        self.s["ecu_cfg"] = {e.name: e.gateway for e in t.ecus}      # each ECU's project / file / instance

    # ------------------------------------------------------------------ update a gateway file
    def _page_update(self):
        self.header("Gateway file to update", "Select the gateway file generated before. Its network file and ECU "
                                              "are found from it; the next pages show the DBC files and addresses.")
        v = self.s.setdefault("gwfile", tk.StringVar())
        f = self.body
        row = ttk.Frame(f)
        row.pack(fill="x")
        ttk.Label(row, text="Gateway file (.arxml):").pack(side="left")
        ttk.Entry(row, textvariable=v, width=78).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(row, text="Browse…", command=self._browse_gateway).pack(side="left")
        self.o_summary = ttk.Label(f, text="", justify="left", wraplength=880, font=("Consolas", 9))
        self.o_summary.pack(anchor="w", pady=(14, 0))
        if self.s.get("topo"):
            self._show_network_summary()

        def leave():
            if self.s.get("topo") is None:
                messagebox.showinfo(TITLE, "Select a gateway file that belongs to a network file.", parent=self)
                return False
            return True
        self.leave = leave

    def _browse_gateway(self):
        p = filedialog.askopenfilename(parent=self, title="Gateway file generated before",
                                       filetypes=[("AUTOSAR XML", "*.arxml"), ("All files", "*.*")])
        if p:
            self._read_gateway(p)

    def _read_gateway(self, p):
        self.s.setdefault("gwfile", tk.StringVar()).set(os.path.normpath(p))
        from .regen import config_from_file
        try:
            cfg, _notes = config_from_file(p)
        except Exception as exc:  # noqa: BLE001 - shown to the user
            messagebox.showerror(TITLE, f"Cannot read {p}:\n{exc}", parent=self)
            return
        net = cfg.topology if cfg.topology and os.path.isfile(cfg.topology) else ""
        if not net:
            if self.on_open_gateway and messagebox.askyesno(
                    TITLE, f"{os.path.basename(p)} does not belong to a network file (made for one ECU with manual "
                           f"Ethernet settings). Open it in the generator window to update it there?", parent=self):
                self.destroy()
                self.on_open_gateway(p)
            return
        from .topology import TopologyConfig
        t = TopologyConfig.load(net)
        same = lambda a: a and os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(p))
        target = next((e.name for e in t.ecus if same(e.gateway.output)), "")
        self._load_network(net, target=target, previous=os.path.abspath(p))

    # ------------------------------------------------------------------ DBC files of the network
    def _page_dbcs(self):
        self.header("DBC files of the network",
                    "Add the DBC files of all CAN buses of the network and check the gateway node of each (suggested "
                    "from the file name, a gateway / zone-like name, or a node that is in every DBC). DBC files with "
                    "the same ECU are one ECU: messages between its buses stay inside it (CAN -> CAN), messages "
                    "between ECUs go over Ethernet. Type the same ECU name to merge nodes named after their bus.")
        self.s.setdefault("dbcs", [])
        top = ttk.Frame(self.body)
        top.pack(fill="x")
        ttk.Button(top, text="Add DBC files…", command=lambda: self._add_dbcs(rows)).pack(side="left")
        ttk.Label(top, text="   (several at once: hold Ctrl in the file dialog)", foreground="#666666").pack(
            side="left")
        holder = ttk.Frame(self.body)
        holder.pack(fill="both", expand=True, pady=(10, 0))
        rows = self._scroll_area(holder, height=220)
        self._fill_dbc_rows(rows)

        def leave():
            dbcs = self.s.get("dbcs", [])
            if not dbcs:
                messagebox.showinfo(TITLE, "Add the DBC files of the network.", parent=self)
                return False
            if any(not d["node"].get() for d in dbcs):
                messagebox.showinfo(TITLE, "Select the gateway node of every DBC.", parent=self)
                return False
            self.s["groups"] = self._groups()
            return True
        self.leave = leave

    def _groups(self):
        return start.group_ecus([(d["path"], d["node"].get(), d["ecu"].get().strip() or d["node"].get())
                                 for d in self.s.get("dbcs", [])])

    def _bus_of(self, path):
        return next((d["db"].name for d in self.s.get("dbcs", []) if d["path"] == path), os.path.basename(path))

    def _add_dbcs(self, rows):
        paths = filedialog.askopenfilenames(parent=self, title="DBC files",
                                            filetypes=[("CAN database", "*.dbc"), ("All files", "*.*")])
        if not paths:
            return
        dbcs = self.s.setdefault("dbcs", [])
        have = {os.path.normcase(d["path"]) for d in dbcs}
        for p in paths:
            p = os.path.abspath(p)
            if os.path.normcase(p) in have:
                continue
            try:
                if p not in self.cache:
                    self.cache[p] = dbcread.load(p)
                dbcs.append({"path": p, "db": self.cache[p], "node": tk.StringVar(), "ecu": tk.StringVar(),
                             "reason": ""})
            except Exception as exc:  # noqa: BLE001 - shown to the user
                messagebox.showerror(TITLE, f"Cannot read {p}:\n{exc}", parent=self)
        for d, g in zip(dbcs, start.guess_nodes([d["db"] for d in dbcs])):
            if not d.get("chosen"):
                d["node"].set(g.node)
                d["reason"] = ("suggested: " if g.sure else "please check: ") + g.reason
                d["ecu"].set(start.ecu_for_node(g.node, d["db"].name))
        self._fill_dbc_rows(rows)

    def _fill_dbc_rows(self, rows):
        for w in rows.winfo_children():
            w.destroy()
        dbcs = self.s.get("dbcs", [])
        if not dbcs:
            ttk.Label(rows, text="No DBC file yet.", foreground="#666666").pack(anchor="w")
            return
        for c, text in enumerate(("DBC file", "Bus", "Gateway node", "ECU", "")):
            ttk.Label(rows, text=text, font=("Segoe UI", 9, "bold")).grid(row=0, column=c, sticky="w", padx=4)
        summary = ttk.Label(rows, text="", justify="left", wraplength=860)
        summary.grid(row=2 * len(dbcs) + 1, column=0, columnspan=5, sticky="w", padx=4, pady=(8, 0))

        def show_groups(*_a):
            try:
                groups = self._groups()
                parts = [f"{e} ({', '.join(self._bus_of(p) for p, _n in v)})" for e, v in groups.items()]
                summary.config(text=f"→ {len(groups)} ECU(s):  " + ",  ".join(parts))
            except tk.TclError:
                pass

        def info_of(d):
            rx, tx = start.node_counts(d["db"]).get(d["node"].get(), (0, 0))
            why = "" if d.get("chosen") else f" - {d['reason']}"
            d["info"].config(text=f"receives {rx}, sends {tx} message(s){why}")
        for i, d in enumerate(dbcs, start=1):
            db = d["db"]
            ttk.Label(rows, text=os.path.basename(d["path"])).grid(row=2 * i - 1, column=0, sticky="w", padx=4)
            ttk.Label(rows, text=db.name).grid(row=2 * i - 1, column=1, sticky="w", padx=4)
            counts = start.node_counts(db)
            values = [n for n in db.nodes if sum(counts.get(n, (0, 0)))]
            cb = ttk.Combobox(rows, textvariable=d["node"], values=values, width=24, state="readonly")
            cb.grid(row=2 * i - 1, column=2, sticky="w", padx=4)
            cb.bind("<<ComboboxSelected>>", lambda _e, d=d: (
                d.update(chosen=True), d["ecu"].set(start.ecu_for_node(d["node"].get(), d["db"].name)), info_of(d)))
            ttk.Entry(rows, textvariable=d["ecu"], width=16).grid(row=2 * i - 1, column=3, sticky="w", padx=4)
            ttk.Button(rows, text="Remove", command=lambda d=d: (dbcs.remove(d), self._fill_dbc_rows(rows))).grid(
                row=2 * i - 1, column=4, padx=4)
            d["info"] = ttk.Label(rows, text="", foreground="#666666")
            d["info"].grid(row=2 * i, column=2, columnspan=3, sticky="w", padx=4, pady=(0, 6))
            if not d.get("traced"):
                d["ecu"].trace_add("write", lambda *_a: self._groups_changed())
                d["traced"] = True
            info_of(d)
        self._groups_changed = show_groups
        show_groups()

    # ------------------------------------------------------------------ ECUs and Ethernet
    def _page_network(self):
        groups = self.s["groups"]
        self.header("ECUs, IP addresses and network file",
                    "A message one ECU receives and another ECU sends goes directly between them over Ethernet (CAN "
                    "-> ETH on the sender, ETH -> CAN on the receiver). Enter each ECU's IP address once: everything "
                    "is saved in the network file and used for every ECU, so their gateway files match.")
        st = self.s.setdefault("net", {})
        first_dbc = self.s["dbcs"][0]["path"]
        from . import nodes as nodemod
        table = self._eth_nodes()
        v = {k: st.setdefault(k, tk.StringVar(value=x)) for k, x in (
            ("vlan", ""), ("tx", "50000"), ("rx", "50001"), ("pname", "Central"), ("pip", ""))}
        v_one = st.setdefault("one", tk.BooleanVar(value=True))
        if not self.s.setdefault("netfile", tk.StringVar()).get():
            self.s["netfile"].set(os.path.join(os.path.dirname(first_dbc), "gateway_network.json"))
        # central node: a node of the Ethernet node table that is not one of the ECUs (only proposed once)
        central = next((n for n in table if n.ip and not any(nodemod.find([n], e) for e in groups)), None)
        v_peer = st.setdefault("peer", tk.BooleanVar(value=central is not None))
        if central is not None and not st.get("central_done"):
            v["pname"].set(central.name)
            v["pip"].set(central.ip)
            st["central_done"] = True
        ecus = st.setdefault("ecus", {})
        known = [x["ip"].get() for x in ecus.values() if x["ip"].get()]
        known += [n.ip for n in table if n.ip]
        free = iter(start.suggest_ips(len(groups), _int(v["vlan"].get()), known[0] if known else "", known))
        suggested = st.setdefault("suggested", set())
        for e in groups:
            node = nodemod.find(table, e)
            if e not in ecus:
                ecus[e] = {"ip": tk.StringVar(value=node.ip if node is not None and node.ip else next(free, ""))}
                if node is None or not node.ip:
                    suggested.add(ecus[e]["ip"].get())
            elif not ecus[e]["ip"].get():
                ecus[e]["ip"].set(node.ip if node is not None and node.ip else next(free, ""))
                if node is None or not node.ip:
                    suggested.add(ecus[e]["ip"].get())

        def vlan_changed(*_a):
            # suggested addresses follow the VLAN (192.168.<VLAN>.x); typed ones stay
            if not all(ecus[e]["ip"].get() in suggested for e in groups):
                return
            new = start.suggest_ips(len(groups), _int(v["vlan"].get()))
            suggested.clear()
            for e, ip in zip(groups, new):
                ecus[e]["ip"].set(ip)
                suggested.add(ip)
        if not st.get("vlan_traced"):
            v["vlan"].trace_add("write", lambda *_a: st["vlan_cb"]())
            st["vlan_traced"] = True
        st["vlan_cb"] = vlan_changed
        f = self.body
        box = ttk.LabelFrame(f, text="ECUs (from the gateway nodes of the DBC files)", padding=6)
        box.pack(fill="both", expand=True)
        inner = self._scroll_area(box, height=140)
        for c, text in enumerate(("ECU", "CAN buses", "IP address", "Ethernet node table")):
            ttk.Label(inner, text=text, font=("Segoe UI", 9, "bold")).grid(row=0, column=c, sticky="w", padx=4)
        for r, (e, dbcs) in enumerate(groups.items(), start=1):
            ttk.Label(inner, text=e).grid(row=r, column=0, sticky="w", padx=4)
            ttk.Label(inner, text=", ".join(self._bus_of(p) for p, _n in dbcs), wraplength=460,
                      justify="left").grid(row=r, column=1, sticky="w", padx=4)
            ttk.Entry(inner, textvariable=ecus[e]["ip"], width=16).grid(row=r, column=2, sticky="w", padx=4, pady=1)
            node = nodemod.find(table, e)
            ttk.Label(inner, text=(f"{node.name}: MAC {node.mac or '-'}, port base {node.port or '-'}"
                                   if node is not None else "-"), foreground="#555555").grid(
                row=r, column=3, sticky="w", padx=4)
        net = ttk.LabelFrame(f, text="Ethernet", padding=6)
        net.pack(fill="x", pady=(8, 0))
        pf = ttk.Frame(net)
        pf.pack(fill="x")
        ttk.Label(pf, text="VLAN id:").pack(side="left")
        ttk.Entry(pf, textvariable=v["vlan"], width=6).pack(side="left", padx=4)
        ttk.Label(pf, text="(empty = untagged)").pack(side="left")
        ttk.Button(pf, text="Ethernet nodes…", command=self._edit_eth_nodes).pack(side="right")
        sf = ttk.Frame(net)
        sf.pack(fill="x", pady=(4, 0))
        e_rx = None

        def one_changed():
            if e_rx is not None:
                e_rx.configure(state="disabled" if v_one.get() else "normal")
        ttk.Checkbutton(sf, text="One socket per ECU for both directions", variable=v_one,
                        command=one_changed).pack(side="left")
        ttk.Label(sf, text="    ECUs not in the node table: port").pack(side="left")
        ttk.Entry(sf, textvariable=v["tx"], width=7).pack(side="left", padx=4)
        ttk.Label(sf, text="(two sockets: sends from it, receives on").pack(side="left")
        e_rx = ttk.Entry(sf, textvariable=v["rx"], width=7)
        e_rx.pack(side="left", padx=4)
        ttk.Label(sf, text=")").pack(side="left")
        one_changed()
        cf = ttk.Frame(net)
        cf.pack(fill="x", pady=(4, 0))
        ttk.Checkbutton(cf, text="Also a central Ethernet node for the messages no ECU needs:", variable=v_peer).pack(
            side="left")
        ttk.Label(cf, text="  name").pack(side="left")
        ttk.Entry(cf, textvariable=v["pname"], width=12).pack(side="left", padx=4)
        ttk.Label(cf, text="IP").pack(side="left")
        ttk.Entry(cf, textvariable=v["pip"], width=15).pack(side="left", padx=4)
        tf = ttk.Frame(f)
        tf.pack(fill="x", pady=(8, 0))
        ttk.Label(tf, text="Network file:").pack(side="left")
        ttk.Entry(tf, textvariable=self.s["netfile"], width=72).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(tf, text="Browse…", command=lambda: self._save_json(self.s["netfile"])).pack(side="left")

        def leave():
            from .planner import _valid_ip
            seen = set()
            for e in groups:
                ip = ecus[e]["ip"].get().strip()
                if not _valid_ip(ip) or ip in seen:
                    messagebox.showinfo(TITLE, f"Enter a different IPv4 address for {e}.", parent=self)
                    return False
                seen.add(ip)
            if v_peer.get() and (not v["pname"].get().strip() or not _valid_ip(v["pip"].get().strip())):
                messagebox.showinfo(TITLE, "Enter the name and the IP address of the central node.", parent=self)
                return False
            if not self.s["netfile"].get().strip():
                messagebox.showinfo(TITLE, "Enter the network file.", parent=self)
                return False
            return True
        self.leave = leave

    def _eth_nodes(self):
        from . import nodes as nodemod
        try:
            return nodemod.load()
        except (OSError, ValueError):
            return []

    def _edit_eth_nodes(self):
        from .gui import NodesDialog
        d = NodesDialog(self)
        self.wait_window(d)
        if d.result is not None:
            st = self.s.get("net", {})
            for x in st.get("ecus", {}).values():      # suggested addresses give way to the table
                if x["ip"].get() in st.get("suggested", set()):
                    x["ip"].set("")
            st.pop("central_done", None)
            self._show()

    def _save_json(self, var):
        p = filedialog.asksaveasfilename(parent=self, title="Network file", defaultextension=".json",
                                         initialdir=os.path.dirname(var.get()) or None,
                                         initialfile=os.path.basename(var.get()) or None,
                                         filetypes=[("Network file", "*.json")])
        if p:
            var.set(os.path.normpath(p))

    # ------------------------------------------------------------------ target ECU
    def _page_target(self):
        groups = self.s["groups"]
        names = list(groups)
        self.header("ECU to generate the gateway file for",
                    "Choose the ECU. Its DaVinci project is optional: when its DBC files are imported there, the tool "
                    "reads the CAN part from it; without project it uses the names DaVinci gives when importing the "
                    "DBC files. Select the gateway file made before to update it.")
        v_t = self.s.setdefault("target", tk.StringVar())
        if self.s.get("target_name") in names and not v_t.get():
            v_t.set(self.s["target_name"])
        if v_t.get() not in names:
            v_t.set(names[0])
        tv = self.s.setdefault("tvars", {})
        f = self.body
        f.columnconfigure(1, weight=1)
        ttk.Label(f, text="ECU:").grid(row=0, column=0, sticky="w", pady=4)
        cb = ttk.Combobox(f, textvariable=v_t, values=names, state="readonly", width=24)
        cb.grid(row=0, column=1, sticky="w")
        detail = ttk.Frame(f)
        detail.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        f.rowconfigure(1, weight=1)

        def show(*_a):
            for w in detail.winfo_children():
                w.destroy()
            self._target_detail(detail, v_t.get(), tv)
        cb.bind("<<ComboboxSelected>>", show)
        show()

        def leave():
            x = tv.get(v_t.get())
            if x is None or not x["out"].get().strip():
                messagebox.showinfo(TITLE, "Enter the gateway file to write.", parent=self)
                return False
            if not x["inst"].get().strip():
                messagebox.showinfo(TITLE, "Enter the ECU instance name used in DaVinci.", parent=self)
                return False
            return True
        self.leave = leave

    def _target_detail(self, f, ecu, tv):
        old = (self.s.get("ecu_cfg") or {}).get(ecu)
        netdir = os.path.dirname(self.s["netfile"].get()) or os.getcwd()
        if ecu not in tv:
            proj = old.base if old is not None and old.base.lower().endswith(".dpa") else ""
            prev = self.s.get("target_prev") if self.s.get("target_name") == ecu else ""
            out = prev or (old.output if old is not None and old.output else "") or \
                start.default_output(os.path.dirname(proj) if proj else netdir, ecu, project=True)
            inst = (old.ecu if old is not None and old.ecu else "") or ecu
            dv = "5.24 or older" if old is not None and old.schema == "AUTOSAR_00049" else "5.31 or newer"
            tv[ecu] = {"proj": tk.StringVar(value=proj), "prev": tk.StringVar(value=prev or ""),
                       "out": tk.StringVar(value=out), "inst": tk.StringVar(value=inst),
                       "dv": tk.StringVar(value=dv), "info": None}
        x = tv[ecu]
        f.columnconfigure(1, weight=1)
        rows = [("DaVinci project (optional):", x["proj"], self._browse_target_project, True),
                ("Gateway file to update (optional):", x["prev"], self._browse_target_prev, True),
                ("Gateway file to write:", x["out"], self._browse_target_out, False)]
        for r, (label, var, fn, clear) in enumerate(rows):
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", pady=3)
            row = ttk.Frame(f)
            row.grid(row=r, column=1, sticky="we")
            ttk.Entry(row, textvariable=var, width=70).pack(side="left", fill="x", expand=True)
            ttk.Button(row, text="Browse…", command=lambda fn=fn, e=ecu: fn(e)).pack(side="left", padx=4)
            if clear:
                ttk.Button(row, text="Clear", command=lambda var=var, e=ecu: self._clear(e, var)).pack(side="left")
        ttk.Label(f, text="ECU instance in DaVinci:").grid(row=3, column=0, sticky="w", pady=3)
        e_inst = ttk.Entry(f, textvariable=x["inst"], width=24)
        e_inst.grid(row=3, column=1, sticky="w")
        ttk.Label(f, text="DaVinci Configurator:").grid(row=4, column=0, sticky="w", pady=3)
        rf = ttk.Frame(f)
        rf.grid(row=4, column=1, sticky="w")
        for label, schema in start.DAVINCI_SCHEMAS.items():
            ttk.Radiobutton(rf, text=f"{label}  ({schema})", value=label, variable=x["dv"]).pack(side="left",
                                                                                                padx=(0, 16))
        buses = ", ".join(self._bus_of(p) for p, _n in self.s["groups"][ecu])
        if x["proj"].get():
            e_inst.state(["disabled"])
            for w in rf.winfo_children():
                w.state(["disabled"])
            what = ("Read from the project: its ECU instance and CAN channels (and its schema). The gateway file "
                    "goes into its Input Files next to the DBC files, then Update.")
        else:
            what = (f"No project: the file references what DaVinci creates when it imports the DBC files of {ecu} "
                    f"({buses}) for ECU instance '{x['inst'].get()}'. Import the DBC files and this gateway file in "
                    f"that project (Input Files), then Update.")
        if x["prev"].get():
            what += " The gateway file made before is updated: what does not change keeps its configuration."
        ttk.Label(f, text=what, foreground="#555555", wraplength=860, justify="left").grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(12, 0))

    def _clear(self, ecu, var):
        var.set("")
        x = self.s["tvars"][ecu]
        if var is x["proj"]:
            x["info"] = None
        self._show()

    def _browse_target_project(self, ecu):
        p = filedialog.askopenfilename(parent=self, title=f"DaVinci project of {ecu}",
                                       filetypes=[("DaVinci project", "*.dpa"), ("All files", "*.*")])
        if not p:
            return
        x = self.s["tvars"][ecu]

        def done(info):
            x["proj"].set(os.path.abspath(p))
            x["info"] = info
            x["inst"].set(info.project.ecu_name)
            if not x["prev"].get():
                gws = info.gateway_files
                if gws and messagebox.askyesno(TITLE, f"The project already contains the gateway file "
                                                      f"{os.path.basename(gws[0])}. Update it?", parent=self):
                    x["prev"].set(gws[0])
                    x["out"].set(gws[0])
                else:
                    x["out"].set(start.default_output(info.project.dir, ecu, project=True))
            if not start.channels_in_use(info):
                messagebox.showwarning(TITLE, f"The ECU of {os.path.basename(p)} has no CAN channel with messages: "
                                              f"import its DBC files in DaVinci first.", parent=self)
            self._show()
        self._bg("Reading the project", lambda: start.read_project(p), done)

    def _browse_target_prev(self, ecu):
        p = filedialog.askopenfilename(parent=self, title=f"Gateway file of {ecu} made before",
                                       filetypes=[("AUTOSAR XML", "*.arxml"), ("All files", "*.*")])
        if p:
            x = self.s["tvars"][ecu]
            x["prev"].set(os.path.abspath(p))
            x["out"].set(os.path.abspath(p))
            self._show()

    def _browse_target_out(self, ecu):
        var = self.s["tvars"][ecu]["out"]
        p = filedialog.asksaveasfilename(parent=self, title="Gateway file", defaultextension=".arxml",
                                         initialdir=os.path.dirname(var.get()) or None,
                                         initialfile=os.path.basename(var.get()) or None,
                                         filetypes=[("AUTOSAR XML", "*.arxml")])
        if p:
            var.set(os.path.normpath(p))

    # ------------------------------------------------------------------ finish
    def finish(self):
        target = self.s["target"].get()
        x = self.s["tvars"][target]
        if x["proj"].get() and x["info"] is None:
            p = x["proj"].get()                       # project from the network file: its channels are needed

            def done(info):
                x["info"] = info
                self.finish()
            self._bg("Reading the project", lambda: start.read_project(p), done)
            return
        st = self.s["net"]
        path = os.path.abspath(self.s["netfile"].get().strip())
        groups = self.s["groups"]
        projects = {target: (x["proj"].get(), start.channels_in_use(x["info"]))} if x["proj"].get() else {}
        t = start.topology_for_dbcs(
            groups, os.path.dirname(path), start.DAVINCI_SCHEMAS[x["dv"].get()],
            {e: st["ecus"][e]["ip"].get().strip() for e in groups}, {e: e == target for e in groups},
            {target: x["out"].get().strip()}, _int(st["vlan"].get()),
            (st["pname"].get().strip(), st["pip"].get().strip()) if st["peer"].get() else None,
            _int(st["tx"].get()) or 50000, _int(st["rx"].get()) or 50001, os.path.splitext(os.path.basename(path))[0],
            projects=projects, gateway_only=True, one_socket=st["one"].get() if "one" in st else True,
            eth_nodes=self._eth_nodes())
        node = t.ecu(target)
        node.gateway.previous = x["prev"].get().strip()
        if not x["proj"].get():
            node.gateway.ecu = x["inst"].get().strip()
        # keep what the other ECUs had (project, gateway file, ECU instance) for their own generation later
        for e in t.ecus:
            old = (self.s.get("ecu_cfg") or {}).get(e.name)
            if e.name != target and old is not None:
                e.gateway.base, e.gateway.output, e.gateway.previous = old.base, old.output, old.previous
                e.gateway.ecu, e.gateway.schema = old.ecu or e.gateway.ecu, old.schema or e.gateway.schema
                if old.base:
                    e.gateway.buses = old.buses
        try:
            t.save(path)
        except OSError as exc:
            messagebox.showerror(TITLE, f"Cannot write {path}:\n{exc}", parent=self)
            return
        master = self.master
        self.destroy()
        if self.on_topology_file is not None:
            self.on_topology_file(path, target)
        else:
            from .topology.gui import open_topology
            open_topology(master, path, select=target)
