"""'Configuration Editors' view: domains with editor links + Basic Editor (DaVinci navigation view)."""
import tkinter as tk

from .. import arxml
from ..project import definition_ref
from .theme import COLORS
from .widgets import FONT, FilterEntry, LinkLabel, ScrollableFrame

# module short name -> DaVinci domain (as used by the Configuration Editors view)
DOMAINS = [
    ("Base Services", ["Det", "Dlt", "Crc", "RamTst", "EcuC", "vBaseEnv", "E2E", "E2EPW", "E2EXf", "Dbg", "vSet"]),
    ("Communication", ["Com", "PduR", "CanIf", "Can", "CanTp", "CanTrcv", "EthIf", "Eth", "EthSwt", "EthTrcv",
                       "TcpIp", "SoAd", "Sd", "DoIP", "SomeIpXf", "SomeIpTp", "ComXf", "LdCom", "IpduM", "Lin",
                       "LinIf", "LinTp", "LinTrcv", "Fr", "FrIf", "FrTp", "FrTrcv", "Xcp", "SecOC", "Cdd"]),
    ("Diagnostics", ["Dcm", "Dem", "FiM", "DiagXf", "vDem42"]),
    ("I/O", ["Adc", "Dio", "Port", "Pwm", "Icu", "Spi", "Dma", "Uart", "I2c", "Ocu", "Dsadc", "IoHwAb"]),
    ("Memory", ["NvM", "MemIf", "Fee", "Fls", "Ea", "Eep", "vMem", "vMemAccM", "vFeeAcc"]),
    ("Microcontroller", ["Mcu", "Irq", "Smu", "McalLib", "ResourceM", "Gpt", "Sbc", "Wdg"]),
    ("Mode Management", ["BswM", "EcuM", "ComM", "CanSM", "EthSM", "LinSM", "FrSM", "WdgM", "WdgIf"]),
    ("Network Management", ["Nm", "CanNm", "UdpNm", "LinNm", "FrNm"]),
    ("Runtime System", ["Os", "Rte", "MemMap", "vLinkGen", "vBRS", "Rtm"]),
    ("Security", ["Csm", "CryIf", "Crypto", "KeyM", "vSecPrim"]),
    ("Time Synchronization", ["StbM", "EthTSyn", "CanTSyn", "FrTSyn"]),
]


def domain_of(module_name):
    for d, mods in DOMAINS:
        if module_name in mods:
            return d
    return "Other"


class NavigatorView(tk.Frame):
    def __init__(self, master, app):
        super().__init__(master, background=COLORS["view_bg"])
        self.app = app
        self.open_domains = {"Communication", "Runtime System"}
        top = tk.Frame(self, background=COLORS["view_bg"])
        top.pack(fill="x", padx=3, pady=3)
        self.filter = FilterEntry(top, on_change=lambda t: self.rebuild())
        self.filter.pack(fill="x")
        self.scroll = ScrollableFrame(self)
        self.scroll.pack(fill="both", expand=True)
        bottom = tk.Frame(self, background=COLORS["view_bg"])
        bottom.pack(fill="x", side="bottom", pady=4)
        tk.Frame(bottom, height=1, background="#d0d7e2").pack(fill="x", pady=(0, 4))
        LinkLabel(bottom, "Basic Editor", lambda: app.open_editor(None), image=app.icons.basic).pack(
            anchor="w", padx=8)
        LinkLabel(bottom, "Modules", app.modules_dialog, image=app.icons.module).pack(anchor="w", padx=8, pady=(2, 0))
        LinkLabel(bottom, "Project Settings", app.settings_dialog, image=app.icons.settings).pack(
            anchor="w", padx=8, pady=(2, 0))

    def rebuild(self):
        self.scroll.clear()
        s = self.app.session
        if s.model is None:
            tk.Label(self.scroll.inner, text="No project loaded", background=COLORS["view_bg"],
                     foreground="#888", font=FONT).pack(anchor="w", padx=8, pady=8)
            return
        flt = self.filter.get_text().strip().lower()
        by_domain = {}
        for m in s.model.modules:
            name = arxml.short_name(m)
            if flt and flt not in name.lower():
                continue
            by_domain.setdefault(domain_of(name), []).append(m)
        order = [d for d, _ in DOMAINS] + ["Other"]
        for d in order:
            mods = by_domain.get(d)
            if not mods:
                continue
            self._domain(d, mods, bool(flt))

    def _domain(self, name, mods, force_open):
        s = self.app.session
        is_open = force_open or name in self.open_domains
        hdr = tk.Frame(self.scroll.inner, background=COLORS["domain_bg"], cursor="hand2")
        hdr.pack(fill="x", padx=2, pady=(2, 0))
        t = tk.Label(hdr, text=name, background=COLORS["domain_bg"], foreground=COLORS["domain_fg"],
                     font=("Segoe UI", 9, "bold"), anchor="w")
        t.pack(side="left", padx=4, pady=3)
        a = tk.Label(hdr, text="«" if is_open else "»", background=COLORS["domain_bg"], font=("Segoe UI", 9, "bold"))
        a.pack(side="right", padx=6)
        for w in (hdr, t, a):
            w.bind("<Button-1>", lambda e, n=name: self._toggle(n))
        if not is_open:
            return
        box = tk.Frame(self.scroll.inner, background=COLORS["view_bg"], highlightthickness=1,
                       highlightbackground="#d0d7e2")
        box.pack(fill="x", padx=2)
        mods = sorted(mods, key=lambda m: arxml.short_name(m).lower())
        if len(mods) > 1:
            LinkLabel(box, f"{name} (all modules)", lambda ms=mods, n=name: self.app.open_editor(ms, n),
                      image=self.app.icons.editors).pack(anchor="w", padx=6, pady=(4, 1))
        for m in mods:
            mdef = s.defs.module(definition_ref(m))
            icon = self.app.icons.module if mdef else self.app.icons.unknown
            sev = self.app.tree_marks_for(m)
            row = tk.Frame(box, background=COLORS["view_bg"])
            row.pack(fill="x")
            LinkLabel(row, arxml.short_name(m), lambda m=m: self.app.open_editor([m], arxml.short_name(m)),
                      image=icon).pack(side="left", padx=(6, 0), pady=1)
            if sev is not None:
                tk.Label(row, image=self.app.icons.severity(sev), background=COLORS["view_bg"]).pack(side="left")

    def _toggle(self, name):
        if name in self.open_domains:
            self.open_domains.discard(name)
        else:
            self.open_domains.add(name)
        self.rebuild()
