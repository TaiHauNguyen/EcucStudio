"""Message path report: where every routed message comes from, which gateway ECUs it passes, where it goes.

A message is identified by its CAN id (and id type): the same CAN id on several channels is one message. Messages
paired with another id (a CAN -> CAN or ECU -> ECU link between renamed messages) belong to the same flow.
Works for one gateway plan and for all ECUs of a topology.
"""
from __future__ import annotations

import collections
import csv
import datetime
import html
from dataclasses import dataclass, field

from .planner import CAN_TO_ETH, Plan

COLUMNS = ("CAN ID", "Message", "From", "Via", "To", "Gateways", "Length", "Cycle ms")
NOT_ROUTED_COLUMNS = ("CAN ID", "Message", "Gateway", "Bus / route", "Reason")
_ARROW = " → "


@dataclass
class MessagePath:
    can_id: str
    names: str
    origin: str
    via: list[str]
    destination: str
    gateways: list[str]
    length: int = 0
    cycle_ms: int | None = None
    sort_key: tuple = ()

    @property
    def row(self) -> tuple:
        return (self.can_id, self.names, self.origin, _ARROW.join(self.via), self.destination,
                ", ".join(self.gateways), self.length, self.cycle_ms or "")


@dataclass
class PathReport:
    title: str
    paths: list[MessagePath] = field(default_factory=list)
    not_routed: list[tuple] = field(default_factory=list)
    gateways: list[str] = field(default_factory=list)

    @property
    def messages(self) -> int:
        return len({(p.can_id, p.names) for p in self.paths})


class _Union:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def join(self, a, b):
        self.parent[self.find(a)] = self.find(b)


def _mid(m):
    return (m.can_id, m.extended)


def _id_text(mid):
    return f"0x{mid[0]:08X}" if mid[1] else f"0x{mid[0]:03X}"


def build_report(plans: dict, cross=(), title: str = "") -> PathReport:
    """*plans*: {gateway ECU name: Plan}; *cross*: CrossRoute of a topology (ECU -> ECU over Ethernet)."""
    ecus = set(plans)
    uf = _Union()
    edges = collections.defaultdict(set)      # message id -> {(from node, to node, label)}
    info = {}                                  # message id -> (names, length, cycle)
    senders = collections.defaultdict(set)     # (bus, message id) -> nodes sending it there
    receivers = collections.defaultdict(set)   # (bus, message id) -> nodes receiving it there
    cross_to = {(c.src_ecu, c.src.key, c.dst_ecu): c for c in cross if c.enabled}
    for c in cross:
        if c.enabled:
            uf.join(_mid(c.src.message), _mid(c.dst.message))

    def note(m):
        mid = _mid(m)
        uf.find(mid)
        names, length, cycle = info.get(mid, (set(), m.length, m.cycle_ms))
        names.add(m.name)
        info[mid] = (names, length or m.length, cycle or m.cycle_ms)
        return mid

    # every gateway ECU, as named in the topology / plans and in the DBC files: never an origin or a receiver
    all_gw = (set(plans) | {p.ecu_name for p in plans.values()} |
              {b.node for p in plans.values() for b in p.cfg.buses}) - {"", None}

    def gw_names(_plan: Plan, _r):
        return all_gw

    # nodes: ("bus", name), ("peer", name, "from" | "to") and one gateway hop per route ("gw", ecu, direction, route key): a path
    # only continues through the route that really carries the message (one ECU can carry two flows of a CAN id)
    def hop(ecu, direction, key):
        return ("gw", ecu, direction, key)

    for ecu, plan in plans.items():
        for r in plan.enabled_routes:
            m = r.message
            mid = note(m)
            bus = ("bus", r.bus.name)
            here = hop(ecu, r.direction, r.key)
            eth = f"ETH {r.header_text}" if r.header_id >= 0 else "ETH"
            if r.direction == CAN_TO_ETH:
                edges[mid].add((bus, here, ""))
                senders[(r.bus.name, mid)] |= set(m.senders) - gw_names(plan, r)
                for p in r.peers:
                    c = cross_to.get((ecu, r.key, p))
                    if c is not None:
                        edges[mid].add((here, hop(p, c.dst.direction, c.dst.key), eth))
                    else:
                        # an Ethernet node ends a flow (it is not known to forward it): separate start / end nodes
                        edges[mid].add((here, ("peer", p, "to"), eth))
            else:
                src = r.peers[0] if r.peers else "?"
                if src not in ecus:
                    edges[mid].add((("peer", src, "from"), here, eth))
                edges[mid].add((here, bus, ""))
                receivers[(r.bus.name, mid)] |= set(m.receivers) - gw_names(plan, r)
        for cr in plan.enabled_can_routes:
            a, b = note(cr.src.message), note(cr.dst.message)
            uf.join(a, b)
            here = hop(ecu, cr.src.direction, cr.src.key)      # the same reception as the CAN -> ETH route
            edges[a].add((("bus", cr.src.bus.name), here, ""))
            edges[b].add((here, ("bus", cr.dst.bus.name), ""))
            senders[(cr.src.bus.name, a)] |= set(cr.src.message.senders) - gw_names(plan, cr.src)
            receivers[(cr.dst.bus.name, b)] |= set(cr.dst.message.receivers) - gw_names(plan, cr.dst)
    # one flow per group of message ids
    groups = collections.defaultdict(list)
    for mid in info:
        groups[uf.find(mid)].append(mid)
    report = PathReport(title=title, gateways=sorted(ecus))
    for mids in groups.values():
        mids.sort()
        g_edges = set().union(*(edges[m] for m in mids))
        if not g_edges:
            continue
        out = collections.defaultdict(list)
        has_in = set()
        for a, b, label in sorted(g_edges):
            out[a].append((b, label))
            has_in.add(b)
        # where flows start: buses / Ethernet nodes no gateway feeds, and buses where another node sends it
        sources = sorted({a for a, _b, _l in g_edges if a[0] in ("bus", "peer") and
                          (a not in has_in or (a[0] == "bus" and any(senders.get((a[1], m)) for m in mids)))})
        names = " / ".join(sorted(set().union(*(info[m][0] for m in mids))))
        ids = " / ".join(_id_text(m) for m in mids)
        length = info[mids[0]][1]
        cycle = next((info[m][2] for m in mids if info[m][2]), None)

        def name_of(node):
            return {"gw": node[1], "bus": f"bus {node[1]}", "peer": f"{node[1]} (Ethernet)"}[node[0]]

        def label_of(node, end):
            kind, name = node[0], node[1]
            if kind == "peer":
                return f"{name} (Ethernet)"
            if kind == "bus":
                nodes = set().union(*((senders if end == "from" else receivers).get((name, m), set()) for m in mids))
                nodes = sorted(n for n in nodes if n and n not in all_gw)
                if not nodes:
                    return f"bus {name}"
                return f"{', '.join(nodes)} @ {name}" if end == "from" else f"{name}{_ARROW}{', '.join(nodes)}"
            return name

        def walk(node, trail, labels):
            nexts = [x for x in out.get(node, []) if x[0] not in trail]
            if not nexts:
                if len(trail) > 1:
                    yield trail, labels
                return
            for nxt, label in nexts:
                yield from walk(nxt, trail + [nxt], labels + [label])
        for src in sources:
            for trail, labels in walk(src, [src], []):
                via = []
                for k, node in enumerate(trail[1:-1], start=1):
                    if labels[k - 1]:
                        via.append(f"[{labels[k - 1]}]")
                    via.append(name_of(node))
                if labels[-1]:
                    via.append(f"[{labels[-1]}]")
                gws = list(dict.fromkeys(n[1] for n in trail if n[0] == "gw"))
                report.paths.append(MessagePath(ids, names, label_of(trail[0], "from"), via,
                                                label_of(trail[-1], "to"), gws, length, cycle,
                                                (mids[0], label_of(trail[0], "from"), label_of(trail[-1], "to"))))
    report.paths.sort(key=lambda p: p.sort_key)
    report.not_routed = _not_routed(plans, cross, set(info))
    return report


def _not_routed(plans: dict, cross, routed: set) -> list[tuple]:
    """Messages a gateway could route but does not, with the reason (deselected, layout differs, N:1 ...)."""
    rows, seen = [], set()
    skip = ("CAN <-> Ethernet routing is off", "fed from ", "NM message", "diagnostic message")
    for ecu, plan in plans.items():
        for r in plan.routes:
            if r.enabled or not r.reason or r.reason.startswith(skip) or _mid(r.message) in routed:
                continue
            key = (ecu, r.key, r.direction)
            if key not in seen:
                seen.add(key)
                rows.append((r.message.id_text, r.message.name, ecu, f"{r.bus.name} ({r.direction})", r.reason))
        for c in plan.can_routes:
            if not c.enabled:
                rows.append((c.src.message.id_text, c.src.message.name, ecu,
                             f"{c.src.bus.name} -> {c.dst.bus.name} (CAN->CAN)", c.reason or "deselected"))
    for c in cross:
        if not c.enabled:
            rows.append((c.src.message.id_text, c.src.message.name, f"{c.src_ecu} -> {c.dst_ecu}",
                         f"{c.src.bus.name} -> {c.dst.bus.name} (ECU->ECU)", c.reason or "deselected"))
    rows.sort(key=lambda x: (x[0], x[2]))
    return rows


def report_of_plan(plan: Plan) -> PathReport:
    return build_report({plan.ecu_name or "Gateway": plan}, (), f"Message paths - {plan.ecu_name or 'gateway'}")


def report_of_topology(tplan) -> PathReport:
    name = tplan.cfg.name or "topology"
    return build_report(dict(tplan.plans), tplan.cross, f"Message paths - {name}")


# ---------------------------------------------------------------------------- files
def write_csv(rep: PathReport, path: str) -> str:
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(COLUMNS)
        w.writerows(p.row for p in rep.paths)
        if rep.not_routed:
            w.writerow(())
            w.writerow(("Not routed",))
            w.writerow(NOT_ROUTED_COLUMNS)
            w.writerows(rep.not_routed)
    return path


_CSS = """
:root { --bg:#ffffff; --fg:#1d2733; --muted:#5d6b7a; --line:#d8dee6; --head:#eef3f8; --alt:#f7f9fb; --accent:#1f6feb;
        --eth:#0b7a5c; --warn:#a15c00; }
@media (prefers-color-scheme: dark) { :root { --bg:#14181d; --fg:#e3e8ee; --muted:#9aa7b4; --line:#2c343d;
        --head:#1c232b; --alt:#181e24; --accent:#6ea8fe; --eth:#4fd1a5; --warn:#f0b35a; } }
body { background:var(--bg); color:var(--fg); font:14px/1.45 "Segoe UI", system-ui, sans-serif; margin:0;
       padding:24px 16px; }
main { max-width:1500px; margin:0 auto; }
h1 { font-size:20px; margin:0 0 4px; } h2 { font-size:16px; margin:28px 0 8px; }
.meta { color:var(--muted); margin-bottom:16px; }
.cards { display:flex; gap:12px; flex-wrap:wrap; margin-bottom:8px; }
.card { border:1px solid var(--line); border-radius:6px; padding:8px 14px; min-width:120px; }
.card b { display:block; font-size:20px; font-variant-numeric:tabular-nums; }
.wrap { overflow-x:auto; }
table { border-collapse:collapse; width:100%; }
th, td { border-bottom:1px solid var(--line); padding:6px 8px; text-align:left; vertical-align:top; }
th { background:var(--head); position:sticky; top:0; font-weight:600; }
tr.g1 td { background:var(--alt); }
td.id { font-family:Consolas, monospace; white-space:nowrap; font-variant-numeric:tabular-nums; }
td.via { white-space:nowrap; } .eth { color:var(--eth); font-family:Consolas, monospace; }
.gw { color:var(--accent); font-weight:600; } .reason { color:var(--warn); }
input { padding:6px 8px; width:min(420px, 100%); border:1px solid var(--line); border-radius:4px; background:var(--bg);
        color:var(--fg); margin:4px 0 10px; }
"""

_JS = """
const f = document.getElementById('filter');
f.addEventListener('input', () => { const q = f.value.toLowerCase();
  document.querySelectorAll('tbody tr').forEach(tr => {
    tr.hidden = q && !tr.textContent.toLowerCase().includes(q); }); });
"""


def _via_html(via: list[str], gateways: set) -> str:
    parts = []
    for v in via:
        if v.startswith("[ETH"):
            parts.append(f'<span class="eth">{html.escape(v[1:-1])}</span>')
        else:
            parts.append(f'<span class="gw">{html.escape(v)}</span>' if v in gateways else html.escape(v))
    return _ARROW.join(parts)


def write_html(rep: PathReport, path: str) -> str:
    gws = set(rep.gateways)
    rows, last, band = [], None, 0
    for p in rep.paths:
        if (p.can_id, p.names) != last:
            band, last = 1 - band, (p.can_id, p.names)
        rows.append(f'<tr class="g{band}"><td class="id">{html.escape(p.can_id)}</td><td>{html.escape(p.names)}</td>'
                    f'<td>{html.escape(p.origin)}</td><td class="via">{_via_html(p.via, gws)}</td>'
                    f'<td>{html.escape(p.destination)}</td><td>{html.escape(", ".join(p.gateways))}</td>'
                    f'<td>{p.length}</td><td>{p.cycle_ms or ""}</td></tr>')
    nr = "".join(f'<tr><td class="id">{html.escape(str(a))}</td><td>{html.escape(str(b))}</td>'
                 f'<td>{html.escape(str(c))}</td><td>{html.escape(str(d))}</td>'
                 f'<td class="reason">{html.escape(str(e))}</td></tr>' for a, b, c, d, e in rep.not_routed)
    head = "".join(f"<th>{c}</th>" for c in COLUMNS)
    nr_head = "".join(f"<th>{c}</th>" for c in NOT_ROUTED_COLUMNS)
    when = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(rep.title)}</title><style>{_CSS}</style></head>
<body><main>
<h1>{html.escape(rep.title)}</h1>
<div class="meta">Generated {when} by EcucStudio. A message is identified by its CAN id: the same id on several
channels is one message. "From" is the node that sends it on its bus (or an Ethernet node), "Via" the gateway ECUs
and Ethernet hops (header id), "To" the bus and its receivers (or an Ethernet node).</div>
<div class="cards"><div class="card"><b>{rep.messages}</b>message(s)</div><div class="card"><b>{len(rep.paths)}</b>path(s)</div>
<div class="card"><b>{len(rep.gateways)}</b>gateway ECU(s)</div><div class="card"><b>{len(rep.not_routed)}</b>not routed</div></div>
<input id="filter" type="search" placeholder="Filter: CAN id, message, bus, ECU ...">
<div class="wrap"><table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<h2>Not routed</h2>
<div class="wrap"><table><thead><tr>{nr_head}</tr></thead><tbody>{nr or '<tr><td colspan="5">-</td></tr>'}</tbody></table></div>
</main><script>{_JS}</script></body></html>
"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(doc)
    return path


def write_report(rep: PathReport, stem: str) -> tuple[str, str]:
    """<stem>_message_paths.html and .csv"""
    return write_html(rep, stem + "_message_paths.html"), write_csv(rep, stem + "_message_paths.csv")
