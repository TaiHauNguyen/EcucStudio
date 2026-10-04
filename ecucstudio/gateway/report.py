"""Human readable output of a gateway plan: route table, CSV report and an inventory of the base file."""
from __future__ import annotations

import csv

from .base import Base
from .planner import Plan

COLUMNS = ("Enabled", "Change", "Direction", "Bus", "Message", "CAN ID", "Frame", "Length", "Cycle ms", "CAN PDU",
           "Ethernet PDU", "Header ID", "Header note", "Remark")


def route_rows(plan: Plan, removed: bool = True) -> list[tuple]:
    """One row per planned route; with *removed* also one per route of the previous file that is not
    generated any more."""
    return [row for _, row in route_items(plan, removed)]


def route_items(plan: Plan, removed: bool = True) -> list[tuple]:
    """(Route | CanRoute | PrevRoute, row): CAN <-> Ethernet routes (not listed when that routing is off),
    CAN -> CAN routes, then the removed routes of the previous file."""
    rows = []
    eth = plan.cfg.options.eth_routes if plan.cfg is not None else True
    for r in plan.routes if eth else ():
        m = r.message
        rows.append((r, (
            "yes" if r.enabled else "no", r.change if r.enabled else "", r.direction, r.bus.name, m.name, m.id_text,
            ("EXT" if m.extended else "STD") + (" FD" if m.fd else ""), r.length, m.cycle_ms or "",
            (r.can_pdu or "").rsplit("/", 1)[-1] or r.can_pdu, r.eth_pdu,
            r.header_text if r.header_id >= 0 and r.enabled else "", r.header_note if r.enabled else "",
            "; ".join(([r.reason] if r.reason else []) + r.notes))))
    for c in plan.can_routes:
        s, d = c.src.message, c.dst.message
        msg = s.name if s.name == d.name else f"{s.name} -> {d.name}"
        cid = s.id_text if s.can_id == d.can_id else f"{s.id_text} -> {d.id_text}"
        pdus = f"{(c.src.can_pdu or '').rsplit('/', 1)[-1]} -> {(c.dst.can_pdu or '').rsplit('/', 1)[-1]}"
        rows.append((c, (
            "yes" if c.enabled else "no", c.change if c.enabled else "", "CAN->CAN",
            f"{c.src.bus.name} -> {c.dst.bus.name}", msg, cid,
            ("EXT" if d.extended else "STD") + (" FD" if d.fd else ""), c.dst.length, s.cycle_ms or "", pdus,
            "", "", "", "; ".join(([c.reason] if c.reason else []) + c.notes))))
    if removed:
        for p in plan.removed:
            can_id = "" if p.can_id is None else f"0x{p.can_id:X}"
            target = p.eth_pdu or p.dst_pt.rsplit("/", 1)[-1]
            rows.append((p, ("-", "removed", p.direction, "", p.can_frame or p.can_pt.rsplit("/", 1)[-1], can_id, "",
                             "", "", p.can_pt.rsplit("/", 1)[-1], target,
                             "" if p.header_id is None else f"0x{p.header_id:08X}", "",
                             "in the previous file, not generated any more")))
    return rows


def write_csv(plan: Plan, path: str):
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(COLUMNS)
        w.writerows(route_rows(plan))


def text_table(plan: Plan) -> str:
    cols = (0, 1, 2, 3, 4, 5, 6, 7, 10, 11, 12)
    rows = [tuple(str(x) for x in r) for r in route_rows(plan)]
    head = tuple(COLUMNS[i] for i in cols)
    data = [tuple(r[i] for i in cols) for r in rows]
    widths = [max(len(h), *(len(d[k]) for d in data)) if data else len(h) for k, h in enumerate(head)]
    line = lambda vals: "  ".join(v.ljust(w) for v, w in zip(vals, widths)).rstrip()
    out = [line(head), line(tuple("-" * w for w in widths))] + [line(d) for d in data]
    return "\n".join(out)


def summary(plan: Plan) -> str:
    lines = [f"ECU              : {plan.ecu or '-'}",
             f"Ethernet channel : {plan.eth_channel or '-'}",
             f"ECU connector    : {plan.eth_connector or '-'}",
             f"Local endpoint   : {plan.local_endpoint or '-'}",
             f"Header id set    : {plan.id_set or '-'}{' (new)' if plan.id_set_new else ''}",
             f"Gateway          : {plan.gateway or '-'}{' (new)' if plan.gateway_new else ''}"]
    for d, sp in plan.sides.items():
        lines.append(f"{d:<17}: {sp.local.rsplit('/', 1)[-1]}{' (new' if sp.local_new else ' (existing'}, port "
                     f"{sp.local_port}) -> {sp.remote.rsplit('/', 1)[-1]}{' (new' if sp.remote_new else ' (existing'}"
                     f", port {sp.remote_port}) via {sp.connection.rsplit('/', 1)[-1]}")
    for bp in plan.buses:
        lines.append(f"CAN bus {bp.name:<9}: {bp.channel}{' (new cluster)' if bp.new_cluster else ''}, "
                     f"connector {bp.connector.rsplit('/', 1)[-1]}{' (new)' if bp.new_connector else ''}")
    n = len(plan.enabled_routes)
    lines.append(f"Routes           : {n} enabled of {len(plan.routes)}")
    if plan.can_routes:
        lines.append(f"CAN -> CAN       : {len(plan.enabled_can_routes)} enabled of {len(plan.can_routes)}")
    return "\n".join(lines)


def count_text(eth: int, can: int) -> str:
    """"3 CAN<->Ethernet + 2 CAN->CAN route(s)" (only the parts that exist)."""
    parts = [f"{eth} CAN<->Ethernet"] if eth or not can else []
    if can:
        parts.append(f"{can} CAN->CAN")
    return " + ".join(parts) + " route(s)"


def inventory(base: Base) -> str:
    """What the base file offers for the Ethernet settings (ECUs, channels, connectors, endpoints, sockets)."""
    out = [f"Schema: {base.schema or '?'}", "ECU instances:"]
    for e in base.ecus():
        gw = base.gateway_of(e)
        out.append(f"  {e}" + (f"   (gateway {gw.rsplit('/', 1)[-1]})" if gw else ""))
    out.append("Ethernet channels:")
    if not base.eth_channels():
        out.append("  (none - the generator creates an Ethernet cluster and channel: set ethernet.ecu_ip and,"
                   " for a tagged channel, ethernet.vlan_id)")
    for ch in base.eth_channels():
        out.append(f"  {ch.label}   {ch.path}")
        for c in ch.connectors:
            out.append(f"      connector {c}")
        for ep in ch.endpoints:
            out.append(f"      endpoint  {ep.name:<40} {ep.ip or ''}")
        for s in ch.sockets:
            owner = f"local, {s.connector.rsplit('/', 1)[-1]}" if s.connector else "remote"
            out.append(f"      socket    {s.name:<40} {s.ip or '?'}:{s.port} {s.protocol} ({owner})")
            for c in s.connections:
                out.append(f"          -> {', '.join(r.rsplit('/', 1)[-1] for r in c.remotes)}  "
                           f"[{c.name}, {len(c.ids)} PDU ids]")
    out.append("CAN channels:")
    for c in base.can_channels():
        out.append(f"  {c.name:<20} {c.path}  baud {c.baudrate or '?'}" +
                   (f" / FD {c.fd_baudrate}" if c.fd_baudrate else ""))
    out.append("Header id sets:")
    for s in base.id_sets():
        out.append(f"  {s}")
    return "\n".join(out)
