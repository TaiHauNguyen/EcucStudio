"""Start wizard of the gateway generator: asks what the user has and fills in a first configuration.

1. only DBC files            -> DBC files + gateway node per DBC, new network file, routing / Ethernet
2. DaVinci project with DBCs -> project, its CAN channels, routing / Ethernet (additional input file)
3. update a gateway file     -> the generated file (or its project), buses to add / remove
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
from .config import BusInput, GatewayConfig
from .suggest import apply as apply_suggestions
from .suggest import suggest as suggest_settings

TITLE = "Start - CAN Gateway Generator"
CASES = (
    ("dbc", "I only have DBC files",
     "There is no DaVinci project yet. The tool finds the gateway ECUs from the node names in the DBC files (DBC "
     "files with the same gateway node belong to one ECU) and creates a complete network file per ECU. In DaVinci "
     "you import this file instead of the DBC files."),
    ("project", "I have a DaVinci project with the DBC files imported",
     "The CAN databases are already in the project. The tool reads the messages from the project and writes an "
     "additional input file with only the Ethernet part and the gateway. The DBC files stay imported."),
    ("update", "I want to change a gateway file that is already in the project",
     "Add or remove CAN buses or messages, change Ethernet settings, then write the same file again. In DaVinci "
     "you only run Update: what did not change keeps its configuration, nothing is imported again."),
)


_FIELD_LABELS = {
    "ethernet.channel": "Ethernet channel", "ethernet.new_channel": "new Ethernet channel",
    "ethernet.vlan_id": "VLAN id", "ethernet.ecu_ip": "IP address of the ECU", "ethernet.mac": "MAC address of the ECU",
    "ethernet.can_to_eth.local_port": "ECU sending port", "ethernet.eth_to_can.local_port": "ECU receiving port",
    "ethernet.can_to_eth.remote_ip": "other node (receives)", "ethernet.eth_to_can.remote_ip": "other node (sends)",
    "ethernet.can_to_eth.remote_port": "port the other node receives on",
    "ethernet.eth_to_can.remote_port": "port the other node sends from",
    "ethernet.can_to_eth.local_socket": "ECU sending socket", "ethernet.eth_to_can.local_socket": "ECU receiving socket",
    "ethernet.can_to_eth.remote_socket": "socket of the other node (receives)",
    "ethernet.eth_to_can.remote_socket": "socket of the other node (sends)",
}


def _int(text):
    try:
        return int(str(text).strip(), 0)
    except (TypeError, ValueError):
        return None


class StartWizard(tk.Toplevel):
    def __init__(self, master, on_finish, on_topology=None, on_open=None, dbc_cache=None, on_topology_file=None,
                 initial_case=None):
        super().__init__(master)
        self.title(TITLE)
        self.transient(master)
        height = min(640, max(480, self.winfo_screenheight() - 120))
        self.geometry("900x%d+%d+%d" % (height, master.winfo_rootx() + 60, max(0, master.winfo_rooty() + 20)))
        self.minsize(820, 460)
        self.on_finish, self.on_topology, self.on_open = on_finish, on_topology, on_open
        self.on_topology_file = on_topology_file      # several ECUs: called with the saved topology file
        self.cache = dbc_cache if dbc_cache is not None else {}
        self.case = tk.StringVar(value="")
        self.flow, self.step, self.leave = [], 0, None
        self.s = {}                      # values collected by the pages
        self._q, self._busy = queue.Queue(), False
        head = tk.Frame(self, background="#ffffff")
        head.pack(fill="x")
        self.h_title = tk.Label(head, text="", font=("Segoe UI", 12, "bold"), background="#ffffff", anchor="w")
        self.h_title.pack(fill="x", padx=14, pady=(10, 0))
        self.h_text = tk.Label(head, text="", background="#ffffff", anchor="w", justify="left", wraplength=850,
                               foreground="#444444")
        self.h_text.pack(fill="x", padx=14, pady=(2, 10))
        ttk.Separator(self).pack(fill="x")
        # the buttons are packed before the page so that a long page never pushes them out of the window
        foot = ttk.Frame(self, padding=(14, 8))
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
        if initial_case:
            self.case.set(initial_case)
            self.flow += self._case_pages(initial_case)
            self.step = 1
        self._show()

    def _case_pages(self, case):
        return {"dbc": [self._page_dbcs, self._page_newfile, self._page_routing],
                "project": [self._page_project, self._page_routing],
                "update": [self._page_update, self._page_changes]}[case]

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
        self.h_title.config(text=title)
        self.h_text.config(text=text)

    def next(self):
        if self._busy:
            return
        if self.leave is not None and not self.leave():
            return
        if self.step == 0:
            case = self.case.get()
            if not case:
                messagebox.showinfo(TITLE, "Choose what you have.", parent=self)
                return
            self.flow = [self._page_start] + self._case_pages(case)
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

    # ------------------------------------------------------------------ page: what do you have
    def _page_start(self):
        self.header("What do you have?", "Choose your situation; the next pages ask only for what is needed and "
                                         "fill in the rest with suggested values. Everything can be changed later "
                                         "in the main window.")
        for key, title, text in CASES:
            card = ttk.Frame(self.body, padding=(4, 6))
            card.pack(fill="x", pady=4)
            rb = ttk.Radiobutton(card, text=title, value=key, variable=self.case)
            rb.pack(anchor="w")
            lbl = ttk.Label(card, text=text, foreground="#555555", wraplength=780, justify="left")
            lbl.pack(anchor="w", padx=(22, 0))
            lbl.bind("<Button-1>", lambda _e, k=key: self.case.set(k))
            lbl.bind("<Double-1>", lambda _e, k=key: (self.case.set(k), self.next()))
            rb.bind("<Double-1>", lambda _e: self.next())
        more = ttk.Frame(self.body)
        more.pack(fill="x", pady=(16, 0))
        if self.on_open:
            ttk.Button(more, text="Open a saved configuration (.json)…",
                       command=lambda: (self.destroy(), self.on_open())).pack(side="left")
        if self.on_topology:
            ttk.Button(more, text="Several ECUs on one Ethernet network (topology)…",
                       command=lambda: (self.destroy(), self.on_topology())).pack(side="left", padx=8)

    # ------------------------------------------------------------------ case 1: DBC files
    def _page_dbcs(self):
        self.header("1  DBC files and gateway ECUs",
                    "Add one DBC file per CAN bus and check its gateway node (suggested from the file name, a "
                    "gateway / zone-like name, or a node that is in every DBC). DBC files with the same ECU are one "
                    "ECU: messages between its buses stay inside it (CAN -> CAN); messages between ECUs go over "
                    "Ethernet. Type the same ECU name to merge nodes that are named after their bus.")
        dbcs = self.s.setdefault("dbcs", [])
        top = ttk.Frame(self.body)
        top.pack(fill="x")
        ttk.Button(top, text="Add DBC files…", command=lambda: self._add_dbcs(rows)).pack(side="left")
        ttk.Label(top, text="   (several at once: hold Ctrl in the file dialog)", foreground="#666666").pack(
            side="left")
        rows = ttk.Frame(self.body)
        rows.pack(fill="both", expand=True, pady=(10, 0))
        self._fill_dbc_rows(rows)

        def leave():
            if not dbcs:
                messagebox.showinfo(TITLE, "Add at least one DBC file.", parent=self)
                return False
            if any(not d["node"].get() for d in dbcs):
                messagebox.showinfo(TITLE, "Select the gateway node of every DBC.", parent=self)
                return False
            groups = self._groups()
            self.s["groups"] = groups
            # one ECU: one network file as before; several ECUs: a topology (ECU -> ECU over Ethernet)
            self.flow = [self._page_start, self._page_dbcs] + (
                [self._page_newfile, self._page_routing] if len(groups) == 1 else [self._page_network])
            return True
        self.leave = leave

    def _groups(self):
        return start.group_ecus([(d["path"], d["node"].get(), d["ecu"].get().strip() or d["node"].get())
                                 for d in self.s.get("dbcs", [])])

    def _add_dbcs(self, rows):
        paths = filedialog.askopenfilenames(parent=self, title="DBC files",
                                            filetypes=[("CAN database", "*.dbc"), ("All files", "*.*")])
        if not paths:
            return
        dbcs = self.s["dbcs"]
        have = {os.path.normcase(d["path"]) for d in dbcs}
        for p in paths:
            p = os.path.abspath(p)
            if os.path.normcase(p) in have:
                continue
            try:
                key = p
                if key not in self.cache:
                    self.cache[key] = dbcread.load(p)
                dbcs.append({"path": p, "db": self.cache[key], "node": tk.StringVar(), "ecu": tk.StringVar(),
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
        summary = ttk.Label(rows, text="", justify="left", wraplength=820)
        summary.grid(row=2 * len(dbcs) + 1, column=0, columnspan=5, sticky="w", padx=4, pady=(8, 0))

        def show_groups(*_a):
            groups = self._groups()
            parts = [f"{e} ({', '.join(self._bus_of(p) for p, _n in v)})" for e, v in groups.items()]
            what = ("one ECU: one network file" if len(groups) == 1 else
                    f"{len(groups)} ECUs: one network file each, ECU -> ECU over Ethernet")
            summary.config(text=f"\u2192 {what}:  " + ",  ".join(parts))
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
            lbl = ttk.Label(rows, text="", foreground="#666666")
            lbl.grid(row=2 * i, column=2, columnspan=3, sticky="w", padx=4, pady=(0, 6))
            d["info"] = lbl
            if not d.get("traced"):
                d["ecu"].trace_add("write", lambda *_a: self._groups_changed())
                d["traced"] = True

            def info_of(d):
                rx, tx = start.node_counts(d["db"]).get(d["node"].get(), (0, 0))
                why = "" if d.get("chosen") else f" - {d['reason']}"
                d["info"].config(text=f"receives {rx}, sends {tx} message(s){why}")
            info_of(d)
        self._groups_changed = show_groups
        show_groups()

    def _page_newfile(self):
        self.header("2/3  New network file", "The ECU-INSTANCE of the new file, the DaVinci version that will read "
                                             "it, and where to write it.")
        dbcs = self.s["dbcs"]
        v_ecu = self.s.setdefault("ecu", tk.StringVar())
        single = next(iter(self.s.get("groups") or {}), "") or start.ecu_name_for([d["node"].get() for d in dbcs])
        if self.s.get("ecu_from") != single:            # the ECU of the first page, unless typed here
            v_ecu.set(single)
            self.s["ecu_from"] = single
            self.s.setdefault("out", tk.StringVar()).set("")
        v_dv = self.s.setdefault("davinci", tk.StringVar(value="5.31 or newer"))
        v_out = self.s.setdefault("out", tk.StringVar())
        if not v_out.get():
            v_out.set(start.default_output(os.path.dirname(dbcs[0]["path"]), v_ecu.get()))
        f = self.body
        ttk.Label(f, text="ECU name:").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(f, textvariable=v_ecu, width=30).grid(row=0, column=1, sticky="w")
        ttk.Label(f, text="DaVinci Configurator:").grid(row=1, column=0, sticky="w", pady=4)
        rf = ttk.Frame(f)
        rf.grid(row=1, column=1, sticky="w")
        for label, schema in start.DAVINCI_SCHEMAS.items():
            ttk.Radiobutton(rf, text=f"{label}  ({schema})", value=label, variable=v_dv).pack(side="left", padx=(0, 16))
        ttk.Label(f, text="Output file:").grid(row=2, column=0, sticky="w", pady=4)
        of = ttk.Frame(f)
        of.grid(row=2, column=1, sticky="we")
        f.columnconfigure(1, weight=1)
        ttk.Entry(of, textvariable=v_out, width=80).pack(side="left", fill="x", expand=True)
        ttk.Button(of, text="Browse…", command=lambda: self._save_as(v_out)).pack(side="left", padx=4)
        ttk.Label(f, text="In DaVinci: create the project for this ECU and import this file (Input Files) instead "
                          "of the DBC files.", foreground="#666666").grid(row=3, column=1, sticky="w", pady=(10, 0))

        def leave():
            if not v_ecu.get().strip() or not v_out.get().strip():
                messagebox.showinfo(TITLE, "Enter the ECU name and the output file.", parent=self)
                return False
            return True
        self.leave = leave

    def _bus_of(self, path):
        return next((d["db"].name for d in self.s.get("dbcs", []) if d["path"] == path), os.path.basename(path))

    def _page_network(self):
        groups = self.s["groups"]
        self.header(f"2/2  {len(groups)} ECUs and Ethernet",
                    "Each ECU gets its own network file. A message one ECU receives and another ECU sends goes "
                    "directly between them over Ethernet (CAN -> ETH on the sender, ETH -> CAN on the receiver); a "
                    "message between the buses of one ECU stays inside it (CAN -> CAN).")
        folder = os.path.dirname(self.s["dbcs"][0]["path"])
        st = self.s.setdefault("net", {})
        v = {k: st.setdefault(k, tk.StringVar(value=x)) for k, x in (
            ("vlan", ""), ("tx", "50000"), ("rx", "50001"), ("pname", "Central"), ("pip", ""),
            ("topo", os.path.join(folder, "topology.json")))}
        v_peer = st.setdefault("peer", tk.BooleanVar(value=False))
        v_dv = self.s.setdefault("davinci", tk.StringVar(value="5.31 or newer"))
        ecus = st.setdefault("ecus", {})
        for e in list(ecus):
            if e not in groups:
                del ecus[e]
        ips = start.suggest_ips(len(groups), _int(v["vlan"].get()))
        for k, e in enumerate(groups):
            ecus.setdefault(e, {"gen": tk.BooleanVar(value=True), "ip": tk.StringVar(value=ips[k]),
                                "out": tk.StringVar(value=start.default_output(folder, e))})

        def vlan_changed(*_a):
            # suggested addresses follow the VLAN (192.168.<VLAN>.x) unless they were typed
            new = start.suggest_ips(len(groups), _int(v["vlan"].get()))
            old = st.get("suggested", ips)
            for k, e in enumerate(groups):
                if ecus[e]["ip"].get() in old:
                    ecus[e]["ip"].set(new[k])
            st["suggested"] = new
        if not st.get("vlan_traced"):
            v["vlan"].trace_add("write", lambda *_a: st["vlan_cb"]())
            st["vlan_traced"] = True
        st["vlan_cb"] = vlan_changed
        f = self.body
        box = ttk.LabelFrame(f, text="ECUs (found from the gateway nodes of the DBC files)", padding=6)
        box.pack(fill="x")
        for c, text in enumerate(("Generate", "ECU", "CAN buses", "IP address", "Output file")):
            ttk.Label(box, text=text, font=("Segoe UI", 9, "bold")).grid(row=0, column=c, sticky="w", padx=4)
        box.columnconfigure(4, weight=1)
        for r, (e, dbcs) in enumerate(groups.items(), start=1):
            x = ecus[e]
            ttk.Checkbutton(box, variable=x["gen"]).grid(row=r, column=0, padx=4)
            ttk.Label(box, text=e).grid(row=r, column=1, sticky="w", padx=4)
            ttk.Label(box, text=", ".join(self._bus_of(p) for p, _n in dbcs)).grid(row=r, column=2, sticky="w",
                                                                                   padx=4)
            ttk.Entry(box, textvariable=x["ip"], width=15).grid(row=r, column=3, sticky="w", padx=4, pady=1)
            of = ttk.Frame(box)
            of.grid(row=r, column=4, sticky="we", padx=4)
            ttk.Entry(of, textvariable=x["out"], width=40).pack(side="left", fill="x", expand=True)
            ttk.Button(of, text="…", width=3, command=lambda var=x["out"]: self._save_as(var)).pack(side="left")
        net = ttk.LabelFrame(f, text="Network", padding=6)
        net.pack(fill="x", pady=(10, 0))
        rf = ttk.Frame(net)
        rf.grid(row=0, column=1, sticky="w")
        ttk.Label(net, text="DaVinci Configurator:").grid(row=0, column=0, sticky="w", pady=2)
        for label, schema in start.DAVINCI_SCHEMAS.items():
            ttk.Radiobutton(rf, text=f"{label}  ({schema})", value=label, variable=v_dv).pack(side="left", padx=(0, 16))
        pf = ttk.Frame(net)
        pf.grid(row=1, column=1, sticky="w")
        ttk.Label(net, text="VLAN id / ports:").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Entry(pf, textvariable=v["vlan"], width=6).pack(side="left")
        ttk.Label(pf, text="  (empty = untagged)   every ECU sends from").pack(side="left")
        ttk.Entry(pf, textvariable=v["tx"], width=7).pack(side="left", padx=4)
        ttk.Label(pf, text="and receives on").pack(side="left")
        ttk.Entry(pf, textvariable=v["rx"], width=7).pack(side="left", padx=4)
        cf = ttk.Frame(net)
        cf.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Checkbutton(cf, text="Also a central Ethernet node for the messages no ECU needs:", variable=v_peer).pack(
            side="left")
        ttk.Label(cf, text="  name").pack(side="left")
        ttk.Entry(cf, textvariable=v["pname"], width=12).pack(side="left", padx=4)
        ttk.Label(cf, text="IP").pack(side="left")
        ttk.Entry(cf, textvariable=v["pip"], width=15).pack(side="left", padx=4)
        tf = ttk.Frame(f)
        tf.pack(fill="x", pady=(10, 0))
        ttk.Label(tf, text="Topology file:").pack(side="left")
        ttk.Entry(tf, textvariable=v["topo"], width=70).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(tf, text="Browse…", command=lambda: self._save_json(v["topo"])).pack(side="left")
        ttk.Label(f, text="Finish saves the topology and opens it: the ECU -> ECU table shows what goes where, "
                          "Generate writes the network file of each ECU.", foreground="#666666").pack(
            anchor="w", pady=(8, 0))

        def leave():
            from .planner import _valid_ip
            seen = set()
            for e, x in ecus.items():
                ip = x["ip"].get().strip()
                if not _valid_ip(ip) or ip in seen:
                    messagebox.showinfo(TITLE, f"Enter a different IPv4 address for {e}.", parent=self)
                    return False
                seen.add(ip)
                if x["gen"].get() and not x["out"].get().strip():
                    messagebox.showinfo(TITLE, f"Enter the output file of {e}.", parent=self)
                    return False
            if v_peer.get() and (not v["pname"].get().strip() or not _valid_ip(v["pip"].get().strip())):
                messagebox.showinfo(TITLE, "Enter the name and the IP address of the central node.", parent=self)
                return False
            if not v["topo"].get().strip():
                messagebox.showinfo(TITLE, "Enter the topology file.", parent=self)
                return False
            return True
        self.leave = leave

    def _save_json(self, var):
        p = filedialog.asksaveasfilename(parent=self, title="Topology file", defaultextension=".json",
                                         initialdir=os.path.dirname(var.get()) or None,
                                         initialfile=os.path.basename(var.get()) or None,
                                         filetypes=[("Topology", "*.json")])
        if p:
            var.set(os.path.normpath(p))

    def _finish_topology(self):
        groups, st = self.s["groups"], self.s["net"]
        ecus = st["ecus"]
        path = os.path.abspath(st["topo"].get().strip())
        t = start.topology_for_dbcs(
            groups, os.path.dirname(path), start.DAVINCI_SCHEMAS[self.s["davinci"].get()],
            {e: x["ip"].get().strip() for e, x in ecus.items()}, {e: x["gen"].get() for e, x in ecus.items()},
            {e: x["out"].get().strip() for e, x in ecus.items()}, _int(st["vlan"].get()),
            (st["pname"].get().strip(), st["pip"].get().strip()) if st["peer"].get() else None,
            _int(st["tx"].get()) or 50000, _int(st["rx"].get()) or 50001, os.path.splitext(os.path.basename(path))[0])
        try:
            t.save(path)
        except OSError as exc:
            messagebox.showerror(TITLE, f"Cannot write {path}:\n{exc}", parent=self)
            return
        master = self.master
        self.destroy()
        if self.on_topology_file is not None:
            self.on_topology_file(path)
        else:
            from .topology.gui import open_topology
            open_topology(master, path)

    def _save_as(self, var, title="Output file"):
        p = filedialog.asksaveasfilename(parent=self, title=title, defaultextension=".arxml",
                                         initialdir=os.path.dirname(var.get()) or None,
                                         initialfile=os.path.basename(var.get()) or None,
                                         filetypes=[("AUTOSAR XML", "*.arxml")])
        if p:
            var.set(os.path.normpath(p))

    # ------------------------------------------------------------------ case 2: DaVinci project
    def _page_project(self):
        self.header("1/2  DaVinci project", "Select the project (.dpa) in which the CAN databases are imported and "
                                            "saved. The tool lists the CAN channels of its ECU; tick the buses that "
                                            "take part in the gateway.")
        v_dpa = self.s.setdefault("dpa", tk.StringVar())
        v_out = self.s.setdefault("pout", tk.StringVar())
        f = self.body
        f.columnconfigure(1, weight=1)
        ttk.Label(f, text="Project (.dpa):").grid(row=0, column=0, sticky="w", pady=4)
        pf = ttk.Frame(f)
        pf.grid(row=0, column=1, sticky="we")
        ttk.Entry(pf, textvariable=v_dpa, width=80).pack(side="left", fill="x", expand=True)
        ttk.Button(pf, text="Browse…", command=lambda: self._browse_dpa(v_dpa, box, banner)).pack(side="left", padx=4)
        banner = ttk.Frame(f)
        banner.grid(row=1, column=0, columnspan=2, sticky="we")
        box = ttk.LabelFrame(f, text="CAN channels of the ECU", padding=6)
        box.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        box.inner = self._scroll_area(box)
        f.rowconfigure(2, weight=1)
        ttk.Label(f, text="Output file:").grid(row=3, column=0, sticky="w", pady=(8, 4))
        of = ttk.Frame(f)
        of.grid(row=3, column=1, sticky="we", pady=(8, 4))
        ttk.Entry(of, textvariable=v_out, width=80).pack(side="left", fill="x", expand=True)
        ttk.Button(of, text="Browse…", command=lambda: self._save_as(v_out)).pack(side="left", padx=4)
        if self.s.get("pinfo"):
            self._show_project(box, banner)
        else:
            ttk.Label(box.inner, text="Select the project first.", foreground="#666666").pack(anchor="w")

        def leave():
            info = self.s.get("pinfo")
            if info is None:
                messagebox.showinfo(TITLE, "Select the DaVinci project.", parent=self)
                return False
            if not any(v.get() for v in self.s["chan_vars"].values()):
                messagebox.showinfo(TITLE, "Tick at least one CAN channel.", parent=self)
                return False
            if not v_out.get().strip():
                messagebox.showinfo(TITLE, "Enter the output file.", parent=self)
                return False
            return True
        self.leave = leave

    def _browse_dpa(self, var, box, banner):
        p = filedialog.askopenfilename(parent=self, title="DaVinci project",
                                       filetypes=[("DaVinci project", "*.dpa"), ("All files", "*.*")])
        if not p:
            return
        var.set(os.path.normpath(p))

        def done(info):
            self.s["pinfo"] = info
            self.s["chan_vars"] = {c[0]: tk.BooleanVar(value=bool(c[2] + c[3])) for c in info.channels}
            self.s["pout"].set(start.default_output(info.project.dir, info.project.ecu_name, project=True))
            self._show_project(box, banner)
        self._bg("Reading the project", lambda: start.read_project(p), done)

    def _show_project(self, box, banner):
        info = self.s["pinfo"]
        for w in list(box.inner.winfo_children()) + list(banner.winfo_children()):
            w.destroy()
        box.config(text=f"CAN channels of {info.project.ecu_name} (project {info.project.name})")
        for path, label, rx, tx in info.channels:
            ttk.Checkbutton(box.inner, text=f"{label}   -  ECU receives {rx}, sends {tx} message(s)",
                            variable=self.s["chan_vars"][path]).pack(anchor="w")
        if not info.channels:
            ttk.Label(box.inner, text="The ECU of the project has no CAN channel: import the DBC files in DaVinci (Input "
                                "Files), run Update and save the project.", foreground=COLORS["error"]).pack(anchor="w")
        if info.gateway_files:
            gw = info.gateway_files[0]
            tk.Label(banner, text=f"This project already contains the gateway file {os.path.basename(gw)} (made by "
                                  f"this tool). To change it, update that file instead of making a second one.",
                     background="#fff4ce", anchor="w", justify="left", wraplength=640).pack(side="left", fill="x",
                                                                                            expand=True, pady=4)
            ttk.Button(banner, text="Update it instead", command=lambda: self._switch_to_update(gw)).pack(
                side="left", padx=6)

    def _switch_to_update(self, path):
        self.case.set("update")
        self.s["gwfile"] = tk.StringVar(value=path)
        self.s.pop("uinfo", None)
        self.flow = [self._page_start, self._page_update, self._page_changes]
        self.step = 1
        self._show()
        self._read_update(path)

    # ------------------------------------------------------------------ routing + Ethernet (cases 1 and 2)
    def _base_config(self) -> tuple[GatewayConfig, object]:
        if self.case.get() == "dbc":
            cfg = start.config_for_dbcs([(d["path"], d["node"].get()) for d in self.s["dbcs"]],
                                        self.s["out"].get().strip(), self.s["ecu"].get().strip(),
                                        start.DAVINCI_SCHEMAS[self.s["davinci"].get()])
            return cfg, None
        info = self.s["pinfo"]
        chans = [p for p, v in self.s["chan_vars"].items() if v.get()]
        return start.config_for_project(info.project.path, chans, self.s["pout"].get().strip()), info.base

    def _page_routing(self):
        n = "3/3" if self.case.get() == "dbc" else "2/2"
        self.header(f"{n}  What to route", "CAN <-> Ethernet: messages the ECU receives go to Ethernet, messages it "
                                           "sends come from Ethernet. CAN -> CAN: a message the ECU receives on one "
                                           "bus and sends on another is routed between the buses. The Ethernet "
                                           "values below are suggestions: check at least the IP address of the other "
                                           "node.")
        nbus = len(self.s["dbcs"]) if self.case.get() == "dbc" else \
            sum(1 for v in self.s["chan_vars"].values() if v.get())
        v_eth = self.s.setdefault("eth", tk.BooleanVar(value=True))
        v_can = self.s.setdefault("can", tk.BooleanVar(value=nbus > 1))
        f = self.body
        ttk.Checkbutton(f, text="CAN <-> Ethernet", variable=v_eth, command=lambda: self._eth_state(grid)).pack(
            anchor="w")
        cb = ttk.Checkbutton(f, text="CAN -> CAN between the buses" + ("" if nbus > 1 else "  (needs two buses)"),
                             variable=v_can)
        cb.pack(anchor="w")
        if nbus < 2:
            v_can.set(False)
            cb.state(["disabled"])
        grid = ttk.LabelFrame(f, text="Ethernet", padding=8)
        grid.pack(fill="x", pady=(10, 0))
        reasons = ttk.Label(f, text="", foreground="#666666", justify="left", wraplength=840)
        reasons.pack(anchor="w", pady=(8, 0))
        cfg, base = self._base_config()

        def done(res):
            sug, cfg2 = res
            self.s["cfg"] = cfg2
            self._eth_fields(grid, cfg2)
            reasons.config(text="Why these values:\n" + "\n".join(
                f"- {_FIELD_LABELS.get(x.field, x.field.replace('ethernet.', ''))}: {x.reason}" for x in sug[:9]))
            self._eth_state(grid)

        def work():
            sug = suggest_settings(cfg, base)
            apply_suggestions(cfg, sug)
            return sug, cfg
        if self.s.get("cfg_key") == self._cfg_key(cfg) and self.s.get("cfg"):
            self._eth_fields(grid, self.s["cfg"])
            self._eth_state(grid)
        else:
            self.s["cfg_key"] = self._cfg_key(cfg)
            self._bg("Suggesting Ethernet values", work, done)
        self.leave = lambda: self._store_eth()

    @staticmethod
    def _cfg_key(cfg):
        return (cfg.base, cfg.output, cfg.ecu, cfg.schema, tuple((b.dbc, b.node, b.channel) for b in cfg.buses))

    def _eth_fields(self, grid, cfg: GatewayConfig):
        for w in grid.winfo_children():
            w.destroy()
        e = cfg.ethernet
        self.s["ev"] = ev = {k: tk.StringVar(value="" if v is None else str(v)) for k, v in (
            ("vlan", e.vlan_id), ("ecu_ip", e.ecu_ip), ("remote_ip", e.can_to_eth.remote_ip or e.eth_to_can.remote_ip),
            ("tx_local", e.can_to_eth.local_port), ("rx_local", e.eth_to_can.local_port),
            ("tx_remote", e.can_to_eth.remote_port), ("rx_remote", e.eth_to_can.remote_port))}
        new_channel = e.new_channel or not e.channel
        rows = [("Ethernet channel:", f"new channel, VLAN id" if new_channel else f"{e.channel} (existing)",
                 "vlan" if new_channel else None),
                ("IP address of the ECU:", "" if e.ecu_ip else "already in the project", "ecu_ip" if e.ecu_ip else None),
                ("IP address of the other node:", "" if not e.can_to_eth.remote_socket else
                 f"existing socket {e.can_to_eth.remote_socket.rsplit('/', 1)[-1]}",
                 None if e.can_to_eth.remote_socket else "remote_ip"),
                ("ECU ports (sends / receives):", "", ("tx_local", "rx_local")),
                ("Ports of the other node (receives / sends):", "", ("tx_remote", "rx_remote"))]
        self.s["eth_widgets"] = []
        for r, (label, text, keys) in enumerate(rows):
            ttk.Label(grid, text=label).grid(row=r, column=0, sticky="w", pady=2, padx=(0, 8))
            cell = ttk.Frame(grid)
            cell.grid(row=r, column=1, sticky="w")
            if text:
                ttk.Label(cell, text=text, foreground="#555555").pack(side="left", padx=(0, 6))
            for k in ([keys] if isinstance(keys, str) else (keys or ())):
                w = ttk.Entry(cell, textvariable=ev[k], width=16 if "ip" in k else 8)
                w.pack(side="left", padx=(0, 6))
                self.s["eth_widgets"].append(w)

    def _eth_state(self, grid):
        on = self.s["eth"].get()
        for w in self.s.get("eth_widgets", []):
            w.state(["!disabled"] if on else ["disabled"])

    def _store_eth(self) -> bool:
        cfg = self.s.get("cfg")
        if cfg is None:
            return False                 # suggestions still running
        cfg.options.eth_routes = self.s["eth"].get()
        cfg.options.can_routes = self.s["can"].get()
        if not cfg.options.eth_routes:
            return True
        ev, e = self.s["ev"], cfg.ethernet
        if e.new_channel or not e.channel:
            e.vlan_id = _int(ev["vlan"].get())
        if ev["ecu_ip"].get().strip():
            e.ecu_ip = ev["ecu_ip"].get().strip()
        ip = ev["remote_ip"].get().strip()
        for side in (e.can_to_eth, e.eth_to_can):
            if not side.remote_socket:
                side.remote_ip = ip
        e.can_to_eth.local_port, e.eth_to_can.local_port = _int(ev["tx_local"].get()), _int(ev["rx_local"].get())
        e.can_to_eth.remote_port, e.eth_to_can.remote_port = _int(ev["tx_remote"].get()), _int(ev["rx_remote"].get())
        if not e.can_to_eth.remote_socket and not ip:
            messagebox.showinfo(TITLE, "Enter the IP address of the other Ethernet node.", parent=self)
            return False
        return True

    # ------------------------------------------------------------------ case 3: update a gateway file
    def _page_update(self):
        self.header("1/2  Gateway file to change", "Select the gateway file generated before (it is in the Input "
                                                   "Files of the project), or select the project and pick its "
                                                   "gateway file. Its settings are read from the file itself.")
        v_file = self.s.setdefault("gwfile", tk.StringVar())
        f = self.body
        f.columnconfigure(1, weight=1)
        ttk.Label(f, text="Gateway file (.arxml):").grid(row=0, column=0, sticky="w", pady=4)
        gf = ttk.Frame(f)
        gf.grid(row=0, column=1, sticky="we")
        ttk.Entry(gf, textvariable=v_file, width=80).pack(side="left", fill="x", expand=True)
        ttk.Button(gf, text="Browse…", command=self._browse_gw).pack(side="left", padx=4)
        ttk.Label(f, text="or the project:").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Button(f, text="Find the gateway file of a DaVinci project (.dpa)…", command=self._find_gw).grid(
            row=1, column=1, sticky="w")
        self.u_summary = ttk.Label(f, text="", justify="left", wraplength=820)
        self.u_summary.grid(row=2, column=0, columnspan=2, sticky="w", pady=(14, 0))
        if self.s.get("uinfo"):
            self._show_update()

        def leave():
            if self.s.get("uinfo") is None:
                messagebox.showinfo(TITLE, "Select the gateway file.", parent=self)
                return False
            return True
        self.leave = leave

    def _browse_gw(self):
        p = filedialog.askopenfilename(parent=self, title="Generated gateway file",
                                       filetypes=[("AUTOSAR XML", "*.arxml"), ("All files", "*.*")])
        if p:
            self.s["gwfile"].set(os.path.normpath(p))
            self._read_update(p)

    def _find_gw(self):
        p = filedialog.askopenfilename(parent=self, title="DaVinci project",
                                       filetypes=[("DaVinci project", "*.dpa"), ("All files", "*.*")])
        if not p:
            return
        files = start.project_gateway_files(p)
        if not files:
            messagebox.showinfo(TITLE, "The project has no gateway file made by this tool in its Input Files. To make "
                                       "a new one, go Back and choose the DaVinci project case.", parent=self)
            return
        self.s["gwfile"].set(files[0])
        self._read_update(files[0])

    def _read_update(self, path):
        def done(info):
            self.s["uinfo"] = info
            self.s["bus_vars"] = [tk.BooleanVar(value=True) for _ in info.cfg.buses]
            self.s["new_vars"] = {c[0]: tk.BooleanVar(value=False) for c in info.unused_channels}
            self.s["onlyprev"] = tk.BooleanVar(value=True)       # the recommended choice
            if self.step == 1:
                self._show_update()
        if not start.is_gateway_file(path):
            messagebox.showwarning(TITLE, f"{os.path.basename(path)} was not made by this tool (no embedded "
                                          f"configuration). The settings are reconstructed from its content; check "
                                          f"them.", parent=self)
        self._bg("Reading the gateway file", lambda: start.read_update(path), done)

    def _show_update(self):
        info = self.s["uinfo"]
        cfg = info.cfg
        where = (f"DaVinci project {os.path.basename(cfg.base)}" if cfg.base.lower().endswith(".dpa") else
                 (f"network file {os.path.basename(cfg.base)}" if cfg.base else "DBC files (new network file)"))
        lines = [f"{os.path.basename(cfg.output)}: {info.routes} route(s), made from {where}.",
                 "CAN buses: " + ", ".join(b.bus or os.path.basename(b.dbc) or b.channel.rsplit("/", 1)[-1]
                                           for b in cfg.buses)]
        lines += info.notes
        self.u_summary.config(text="\n".join(lines))

    def _page_changes(self):
        info = self.s["uinfo"]
        cfg = info.cfg
        self.header("2/2  What to change", "Untick a bus to remove its routes, tick a new channel of the project to "
                                           "add it. Messages, header ids and Ethernet settings can be changed in "
                                           "the main window after Finish (the route table shows kept / new / "
                                           "removed routes).")
        f = self.body
        note = ttk.Label(f, text="Then click Finish: the main window shows the routes (kept / new / removed). "
                                 "Generate writes the same file again; in DaVinci run Update (the file is already in "
                                 "Input Files).", foreground="#666666", wraplength=840, justify="left")
        note.pack(side="bottom", anchor="w", pady=(10, 0))
        opt = ttk.LabelFrame(f, text="Messages of the buses that stay", padding=6)
        opt.pack(side="bottom", fill="x", pady=(10, 0))
        box = ttk.LabelFrame(f, text="CAN buses", padding=6)
        box.pack(fill="both", expand=True)
        inner = self._scroll_area(box)
        for b, v in zip(cfg.buses, self.s["bus_vars"]):
            label = b.bus or (os.path.basename(b.dbc) if b.dbc else b.channel.rsplit("/", 1)[-1])
            ttk.Checkbutton(inner, text=f"{label}   (in the file)", variable=v).pack(anchor="w")
        for path, label, rx, tx in info.unused_channels:
            ttk.Checkbutton(inner, text=f"{label}   -  new: ECU receives {rx}, sends {tx} message(s)",
                            variable=self.s["new_vars"][path]).pack(anchor="w")
        if not cfg.base:
            ttk.Button(inner, text="Add DBC file…", command=self._add_update_dbc).pack(anchor="w", pady=(6, 0))
        ttk.Radiobutton(opt, text="Keep exactly the messages of the file; new messages in the databases stay off "
                                  "until you enable them (recommended)", value=True,
                        variable=self.s["onlyprev"]).pack(anchor="w")
        ttk.Radiobutton(opt, text="Route every message of the buses again (also new ones)", value=False,
                        variable=self.s["onlyprev"]).pack(anchor="w")
        self.leave = lambda: True

    def _add_update_dbc(self):
        p = filedialog.askopenfilename(parent=self, title="DBC file",
                                       filetypes=[("CAN database", "*.dbc"), ("All files", "*.*")])
        if not p:
            return
        try:
            db = self.cache.setdefault(os.path.abspath(p), dbcread.load(p))
        except Exception as exc:  # noqa: BLE001 - shown to the user
            messagebox.showerror(TITLE, f"Cannot read {p}:\n{exc}", parent=self)
            return
        node = start.guess_nodes([db])[0].node
        info = self.s["uinfo"]
        info.cfg.buses.append(BusInput(dbc=os.path.abspath(p), node=node))
        self.s["bus_vars"].append(tk.BooleanVar(value=True))
        self._show()

    # ------------------------------------------------------------------ finish
    def finish(self):
        case = self.case.get()
        notes = []
        if case == "dbc" and len(self.s.get("groups") or {}) > 1:
            self._finish_topology()
            return
        if case == "update":
            info = self.s["uinfo"]
            cfg = info.cfg
            cfg.buses = [b for b, v in zip(cfg.buses, self.s["bus_vars"]) if v.get()]
            cfg.buses += [BusInput(channel=p) for p, v in self.s["new_vars"].items() if v.get()]
            cfg.options.only_previous = bool(self.s["onlyprev"].get())
            notes = info.notes
        else:
            if not self._store_eth():
                return
            cfg = self.s["cfg"]
        self.destroy()
        self.on_finish(cfg, case, notes)
