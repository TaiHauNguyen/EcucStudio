"""Gateway Editor: open a network file that already contains a gateway and edit routes, sockets, endpoints."""
from __future__ import annotations

import os
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, simpledialog, ttk

from ..gui.theme import COLORS, init_style
from ..gui.widgets import Tooltip, dialog_header
from .existing import ROUTE_COLUMNS, EditError, GatewayModel, parse_number, route_rows

TITLE = "CAN-Ethernet Gateway Editor"
ALL = "All"
DIRECTIONS = (ALL, "CAN->ETH", "ETH->CAN", "CAN->CAN", "ETH->ETH", "other")


def _last(path: str) -> str:
    return (path or "").rsplit("/", 1)[-1]


class RouteDialog(tk.Toplevel):
    """Header ids and socket connections of one route."""

    def __init__(self, master, model: GatewayModel, route):
        super().__init__(master)
        self.title("Gateway Route")
        self.transient(master)
        self.model, self.route, self.applied = model, route, False
        can, eth = route.can, route.eth
        text = [f"{route.src.name}  ->  {route.dst.name}"]
        if can is not None:
            text.append(f"CAN: {can.channel_name}, frame {can.frame or '?'}, id {can.can_id_text or '?'}"
                        + (", CAN FD" if can.fd else ""))
        if eth is not None:
            text.append(f"Ethernet: {eth.channel_name}, {eth.pdu_type or 'PDU'} {eth.name}, length "
                        f"{eth.length if eth.length is not None else '?'}")
        if route.notes:
            text.append("; ".join(route.notes))
        dialog_header(self, f"{route.direction} route", "\n".join(text))
        f = ttk.Frame(self, padding=10)
        f.pack(fill="both", expand=True)
        self.rows = []
        if eth is None or not eth.ids:
            ttk.Label(f, text="This route has no SoAd header id in this file.").grid(row=0, column=0, sticky="w")
        for i, h in enumerate(eth.ids if eth else []):
            ttk.Label(f, text=f"{h.name}:").grid(row=i * 2, column=0, sticky="w", pady=(4, 0))
            v_id = tk.StringVar(value=h.text if h.header_id is not None else "")
            ttk.Label(f, text="Header id").grid(row=i * 2, column=1, sticky="e", padx=(8, 4))
            ttk.Entry(f, textvariable=v_id, width=14).grid(row=i * 2, column=2, sticky="w")
            v_conn = None
            ttk.Label(f, text="Socket connection").grid(row=i * 2 + 1, column=1, sticky="e", padx=(8, 4))
            if len(h.connections) == 1:
                choices = model.connection_choices(h.path) or h.connections
                v_conn = tk.StringVar(value=h.connections[0])
                cb = ttk.Combobox(f, textvariable=v_conn, values=choices, width=90, state="readonly")
                cb.grid(row=i * 2 + 1, column=2, sticky="w")
            else:
                ttk.Label(f, text=", ".join(_last(c) for c in h.connections) or "(none)",
                          foreground="#666666").grid(row=i * 2 + 1, column=2, sticky="w")
            self.rows.append((h, v_id, v_conn))
        bb = ttk.Frame(self, padding=(10, 0, 10, 10))
        bb.pack(fill="x")
        ttk.Button(bb, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(bb, text="OK", command=self.ok).pack(side="right", padx=6)
        self.grab_set()

    def ok(self):
        try:
            for h, v_id, v_conn in self.rows:
                text = v_id.get().strip()
                if text and parse_number(text) != h.header_id:
                    self.model.set_header_id(h.path, text)
                    self.applied = True
                if v_conn is not None and v_conn.get() != h.connections[0]:
                    self.model.move_header_id(h.path, h.connections[0], v_conn.get())
                    self.applied = True
        except EditError as exc:
            messagebox.showerror(TITLE, str(exc), parent=self)
            return
        self.destroy()


class EndpointDialog(tk.Toplevel):
    def __init__(self, master, ep):
        super().__init__(master)
        self.title("Network Endpoint")
        self.transient(master)
        self.result = None
        dialog_header(self, ep.name, f"Channel {_last(ep.channel)}" + (f", ECU {ep.owner}" if ep.owner else
                                                                       ", other node"))
        f = ttk.Frame(self, padding=10)
        f.pack(fill="both")
        self.v_ip, self.v_mask = tk.StringVar(value=ep.ip or ""), tk.StringVar(value=ep.mask or "")
        ttk.Label(f, text="IPv4 address:").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Entry(f, textvariable=self.v_ip, width=20).grid(row=0, column=1, sticky="w")
        ttk.Label(f, text="Netmask:").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Entry(f, textvariable=self.v_mask, width=20).grid(row=1, column=1, sticky="w")
        bb = ttk.Frame(self, padding=(10, 0, 10, 10))
        bb.pack(fill="x")
        ttk.Button(bb, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(bb, text="OK", command=self.ok).pack(side="right", padx=6)
        self.ep = ep
        self.grab_set()

    def ok(self):
        ip, mask = self.v_ip.get().strip(), self.v_mask.get().strip()
        self.result = (ip if ip != (self.ep.ip or "") else None, mask if mask != (self.ep.mask or "") else None)
        self.destroy()


class DeleteDialog(tk.Toplevel):
    def __init__(self, master, routes):
        super().__init__(master)
        self.title("Delete Routes")
        self.transient(master)
        self.result = None
        names = "\n".join(f"  {r.direction}  {r.src.name} -> {r.dst.name}" for r in routes[:12])
        more = f"\n  … and {len(routes) - 12} more" if len(routes) > 12 else ""
        dialog_header(self, f"Delete {len(routes)} route(s)?", names + more)
        f = ttk.Frame(self, padding=10)
        f.pack(fill="both")
        self.v_clean = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="Also remove the Ethernet PDUs, triggerings, ports and header ids that only these "
                                "routes use (the CAN frames are kept)", variable=self.v_clean).pack(anchor="w")
        bb = ttk.Frame(self, padding=(10, 0, 10, 10))
        bb.pack(fill="x")
        ttk.Button(bb, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(bb, text="Delete", command=self.ok).pack(side="right", padx=6)
        self.grab_set()

    def ok(self):
        self.result = self.v_clean.get()
        self.destroy()


class EditorWindow:
    def __init__(self, master: tk.Misc, path: str | None = None):
        self.win = master
        self.model: GatewayModel | None = None
        self._shown = []
        self._build()
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self.win.bind("<FocusIn>", self._on_focus)
        self._update_title()
        if path:
            self.win.after(50, lambda: self.open(path))

    # ------------------------------------------------------------------ layout
    def _build(self):
        w = self.win
        tb = ttk.Frame(w, padding=(6, 4))
        tb.pack(fill="x")
        for text, cmd in (("Open…", self.open_dialog), ("Save", self.save), ("Save As…", self.save_as),
                          ("Reload", self.reload)):
            ttk.Button(tb, text=text, command=cmd).pack(side="left", padx=(0, 4))
        ttk.Separator(tb, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(tb, text="Edit…", command=self.edit_selected).pack(side="left", padx=(0, 4))
        ttk.Button(tb, text="Delete Routes…", command=self.delete_selected).pack(side="left", padx=(0, 4))
        b_add = ttk.Button(tb, text="Add Routes…", command=self.add_routes)
        b_add.pack(side="left", padx=(0, 4))
        Tooltip(b_add, "Open the generator with this file as the base to add routes from DBC files or a DaVinci "
                       "project (existing routes are kept)")
        self.status = ttk.Label(tb, text="Open a network file that contains a gateway.")
        self.status.pack(side="left", padx=12)
        self.info = ttk.Label(w, text="", foreground="#666666", padding=(8, 0))
        self.info.pack(fill="x")
        pw = ttk.PanedWindow(w, orient="vertical")
        pw.pack(fill="both", expand=True)
        nb = ttk.Notebook(pw)
        pw.add(nb, weight=1)
        self._tab_routes(nb)
        self.t_sock = self._table(nb, "  Sockets  ", ("Channel", "Socket", "Owner", "IP", "Port", "Protocol",
                                                     "Connections", "Header ids"),
                                  (140, 280, 90, 120, 70, 70, 280, 80), self.edit_socket)
        self.t_ep = self._table(nb, "  Endpoints  ", ("Channel", "Endpoint", "Owner", "IP", "Netmask"),
                                (160, 320, 120, 140, 140), self.edit_endpoint)
        lf = ttk.Frame(pw)
        pw.add(lf, weight=0)
        self.log = tk.Text(lf, height=8, wrap="word", font=("Segoe UI", 9), background="#ffffff", relief="flat")
        self.log.pack(fill="both", expand=True, padx=2, pady=2)
        for tag in ("error", "warning", "info", "change"):
            self.log.tag_configure(tag, foreground={"error": COLORS["error"], "warning": COLORS["warning"],
                                                    "info": COLORS["info"], "change": "#1b1b1b"}[tag])

    def _tab_routes(self, nb):
        f = ttk.Frame(nb, padding=(4, 4))
        nb.add(f, text="  Routes  ")
        flt = ttk.Frame(f)
        flt.pack(fill="x", pady=(0, 4))
        ttk.Label(flt, text="Direction:").pack(side="left")
        self.v_dir = tk.StringVar(value=ALL)
        cb = ttk.Combobox(flt, textvariable=self.v_dir, values=DIRECTIONS, width=10, state="readonly")
        cb.pack(side="left", padx=(4, 12))
        cb.bind("<<ComboboxSelected>>", lambda _e: self.fill_routes())
        ttk.Label(flt, text="Gateway:").pack(side="left")
        self.v_gw = tk.StringVar(value=ALL)
        self.c_gw = ttk.Combobox(flt, textvariable=self.v_gw, values=(ALL,), width=40, state="readonly")
        self.c_gw.pack(side="left", padx=(4, 12))
        self.c_gw.bind("<<ComboboxSelected>>", lambda _e: self.fill_routes())
        ttk.Label(flt, text="Filter:").pack(side="left")
        self.v_filter = tk.StringVar()
        e = ttk.Entry(flt, textvariable=self.v_filter, width=40)
        e.pack(side="left", padx=4)
        self.v_filter.trace_add("write", lambda *_a: self.fill_routes())
        self.count = ttk.Label(flt, text="", foreground="#666666")
        self.count.pack(side="left", padx=12)
        body = ttk.Frame(f)
        body.pack(fill="both", expand=True)
        self.t_routes = ttk.Treeview(body, columns=ROUTE_COLUMNS, show="headings", selectmode="extended")
        for c, wd in zip(ROUTE_COLUMNS, (72, 70, 230, 90, 140, 240, 50, 100, 170, 360)):
            self.t_routes.heading(c, text=c, anchor="w", command=lambda c=c: self.sort_routes(c))
            self.t_routes.column(c, width=wd, anchor="w", stretch=c == "Remark")
        ys = ttk.Scrollbar(body, orient="vertical", command=self.t_routes.yview)
        self.t_routes.configure(yscrollcommand=ys.set)
        self.t_routes.pack(side="left", fill="both", expand=True)
        ys.pack(side="left", fill="y")
        self.t_routes.tag_configure("ext", foreground="#8a8a8a")
        self.t_routes.bind("<Double-1>", lambda _e: self.edit_selected())
        self.t_routes.bind("<Delete>", lambda _e: self.delete_selected())
        self.t_routes.bind("<Button-3>", self._route_menu)
        self._sort = (None, False)

    def _table(self, nb, title, cols, widths, on_edit):
        f = ttk.Frame(nb, padding=(4, 4))
        nb.add(f, text=title)
        t = ttk.Treeview(f, columns=cols, show="headings", selectmode="browse")
        for c, wd in zip(cols, widths):
            t.heading(c, text=c, anchor="w")
            t.column(c, width=wd, anchor="w")
        ys = ttk.Scrollbar(f, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=ys.set)
        t.pack(side="left", fill="both", expand=True)
        ys.pack(side="left", fill="y")
        t.bind("<Double-1>", lambda _e: on_edit())
        return t

    # ------------------------------------------------------------------ messages
    def say(self, text: str, tag: str = "info"):
        self.log.insert("end", f"[{tag.upper()}] {text}\n", tag)
        self.log.see("end")

    def _update_title(self):
        name = os.path.basename(self.model.path) if self.model else ""
        star = "*" if self.model and self.model.dirty else ""
        self.win.title(f"{TITLE} - {star}{name}" if name else TITLE)

    def _after_edit(self, n_before: int):
        for c in self.model.changes[n_before:]:
            self.say(c, "warning" if c.startswith("warning") else "change")
        self.fill_all()
        self.status.config(text="Changed - Save to write the file.")

    # ------------------------------------------------------------------ file
    def open_dialog(self):
        if not self._confirm_discard():
            return
        p = filedialog.askopenfilename(parent=self.win, title="Network file with a gateway",
                                       filetypes=[("AUTOSAR XML", "*.arxml"), ("All files", "*.*")])
        if p:
            self.open(p)

    def open(self, path: str):
        self.win.config(cursor="watch")
        self.win.update_idletasks()
        try:
            self.model = GatewayModel(path)
        except Exception as exc:  # noqa: BLE001 - shown to the user
            messagebox.showerror(TITLE, f"Cannot open {path}:\n{exc}", parent=self.win)
            self.say(traceback.format_exc().splitlines()[-1], "error")
            return
        finally:
            self.win.config(cursor="")
        self.log.delete("1.0", "end")
        m = self.model
        gws = m.base.gateways()
        self.c_gw["values"] = (ALL,) + tuple(gws)
        self.v_gw.set(ALL)
        self.say(f"Opened {m.path}: {len(m.routes)} route(s) in {len(gws)} gateway(s), "
                 f"{len(m.sockets())} socket(s), {len(m.endpoints())} endpoint(s).")
        problems = m.check()
        for p in problems:
            self.say(p, "warning")
        if not gws:
            self.say("The file has no GATEWAY: use Add Routes… to create routes.", "warning")
        self.fill_all()
        self.status.config(text=f"{len(problems)} finding(s)" if problems else "No inconsistency found.")

    def reload(self):
        if self.model and self._confirm_discard():
            self.open(self.model.path)

    def save(self):
        if not self.model:
            return
        try:
            p = self.model.save()
        except OSError as exc:
            messagebox.showerror(TITLE, f"Cannot write the file:\n{exc}", parent=self.win)
            return
        self.say(f"Saved {p} (previous version kept as .bak).")
        self.status.config(text="Saved.")
        self._update_title()

    def save_as(self):
        if not self.model:
            return
        p = filedialog.asksaveasfilename(parent=self.win, title="Save network file", defaultextension=".arxml",
                                         initialfile=os.path.basename(self.model.path),
                                         filetypes=[("AUTOSAR XML", "*.arxml")])
        if p:
            self.model.save(p)
            self.say(f"Saved {p}.")
            self._update_title()

    def _confirm_discard(self) -> bool:
        if not self.model or not self.model.dirty:
            return True
        ans = messagebox.askyesnocancel(TITLE, "Save the changes first?", parent=self.win)
        if ans is None:
            return False
        if ans:
            self.save()
        return True

    def close(self):
        if self._confirm_discard():
            self.win.destroy()

    def _on_focus(self, event):
        if event.widget is not self.win or not self.model or self.model.dirty:
            return
        xf = self.model.base.xf
        try:
            changed = xf.mtime is not None and abs(os.path.getmtime(xf.path) - xf.mtime) > 1e-6
        except OSError:
            return
        if changed:
            self.open(xf.path)
            self.say("The file was changed by another program (e.g. the generator) and has been reloaded.")

    # ------------------------------------------------------------------ tables
    def fill_all(self):
        self.fill_routes()
        m = self.model
        self.t_sock.delete(*self.t_sock.get_children())
        self._sockets = m.sockets()
        for i, s in enumerate(self._sockets):
            self.t_sock.insert("", "end", iid=str(i), values=(
                _last(s.channel), s.name, s.owner or "(remote)", s.ip or "", s.port if s.port is not None else "",
                s.protocol, ", ".join(_last(c) for c in s.connections), s.header_ids or ""))
        self.t_ep.delete(*self.t_ep.get_children())
        self._endpoints = m.endpoints()
        for i, e in enumerate(self._endpoints):
            self.t_ep.insert("", "end", iid=str(i), values=(_last(e.channel), e.name, e.owner or "(other node)",
                                                           e.ip or "", e.mask or ""))
        self._update_title()

    def _visible_routes(self):
        m = self.model
        if not m:
            return []
        d, g, flt = self.v_dir.get(), self.v_gw.get(), self.v_filter.get().strip().lower()
        out = []
        for r in m.routes:
            if g != ALL and r.gateway != g:
                continue
            if d == "other":
                if r.direction in DIRECTIONS:
                    continue
            elif d != ALL and r.direction != d:
                continue
            if flt and not any(flt in n.lower() for n in r.names()):
                continue
            out.append(r)
        return out

    def fill_routes(self):
        t = self.t_routes
        t.delete(*t.get_children())
        self._shown = self._visible_routes()
        rows = route_rows(self.model, self._shown) if self.model else []
        col, rev = self._sort
        order = list(range(len(rows)))
        if col:
            k = ROUTE_COLUMNS.index(col)
            order.sort(key=lambda i: str(rows[i][k]).lower(), reverse=rev)
        for i in order:
            t.insert("", "end", iid=str(i), values=rows[i], tags=("ext",) if "?" in self._shown[i].direction else ())
        total = len(self.model.routes) if self.model else 0
        self.count.config(text=f"{len(rows)} of {total} route(s)")

    def sort_routes(self, col):
        c, rev = self._sort
        self._sort = (col, not rev if c == col else False)
        self.fill_routes()

    def _selected_routes(self):
        return [self._shown[int(i)] for i in self.t_routes.selection()]

    def _route_menu(self, e):
        iid = self.t_routes.identify_row(e.y)
        if iid and iid not in self.t_routes.selection():
            self.t_routes.selection_set(iid)
        m = tk.Menu(self.win, tearoff=False)
        m.add_command(label="Edit…", command=self.edit_selected)
        m.add_command(label="Delete Routes…", command=self.delete_selected)
        m.add_separator()
        m.add_command(label="Copy Ethernet PDU Name", command=self._copy_names)
        m.tk_popup(e.x_root, e.y_root)

    def _copy_names(self):
        names = [(r.eth or r.dst).name for r in self._selected_routes()]
        self.win.clipboard_clear()
        self.win.clipboard_append("\n".join(names))

    # ------------------------------------------------------------------ edits
    def edit_selected(self):
        routes = self._selected_routes()
        if not routes:
            return
        n = len(self.model.changes)
        d = RouteDialog(self.win, self.model, routes[0])
        self.win.wait_window(d)
        if d.applied:
            self._after_edit(n)

    def delete_selected(self):
        routes = self._selected_routes()
        if not routes:
            return
        d = DeleteDialog(self.win, routes)
        self.win.wait_window(d)
        if d.result is None:
            return
        n = len(self.model.changes)
        self.win.config(cursor="watch")
        self.win.update_idletasks()
        try:
            self.model.delete_routes(routes, cleanup=d.result)
        finally:
            self.win.config(cursor="")
        self._after_edit(n)

    def edit_socket(self):
        sel = self.t_sock.selection()
        if not sel:
            return
        s = self._sockets[int(sel[0])]
        port = simpledialog.askinteger(TITLE, f"{s.protocol} port of {s.name}:", parent=self.win,
                                       initialvalue=s.port, minvalue=0, maxvalue=65535)
        if port is None or port == s.port:
            return
        n = len(self.model.changes)
        try:
            self.model.set_port(s.path, port)
        except EditError as exc:
            messagebox.showerror(TITLE, str(exc), parent=self.win)
            return
        self._after_edit(n)

    def edit_endpoint(self):
        sel = self.t_ep.selection()
        if not sel:
            return
        ep = self._endpoints[int(sel[0])]
        d = EndpointDialog(self.win, ep)
        self.win.wait_window(d)
        if not d.result or d.result == (None, None):
            return
        n = len(self.model.changes)
        try:
            self.model.set_endpoint(ep.path, *d.result)
        except EditError as exc:
            messagebox.showerror(TITLE, str(exc), parent=self.win)
            return
        self._after_edit(n)

    def add_routes(self):
        if not self.model:
            messagebox.showinfo(TITLE, "Open a network file first.", parent=self.win)
            return
        if self.model.dirty:
            if not messagebox.askyesno(TITLE, "Save the changes before adding routes?", parent=self.win):
                return
            self.save()
        from .gui import open_window
        top = open_window(self.win)
        gw = top.gateway
        gw.cfg.base = gw.cfg.output = self.model.path
        gw.show_config()
        self.say("Generator opened with this file as base and output; after Generate, come back here (the file is "
                 "reloaded automatically).")


def open_editor(master: tk.Misc | None = None, path: str | None = None):
    """Open the editor in a Toplevel of *master* or in its own root window."""
    from ..gui.startup import bring_to_front, show_main_window
    if master is None:
        root = tk.Tk()
        init_style(root)
        root.editor = EditorWindow(root, path)
        show_main_window(root, TITLE, None, "1450x880")
        return root
    top = tk.Toplevel(master)
    top.geometry("1450x880")
    top.editor = EditorWindow(top, path)
    bring_to_front(top)
    return top


def main(path: str | None = None):
    open_editor(None, path).mainloop()
