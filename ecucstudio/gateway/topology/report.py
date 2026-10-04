"""Text output of a topology plan (command line) and the rows of the ECU -> ECU table (GUI)."""
from __future__ import annotations

from .. import report
from .planner import TopologyPlan
from .writer import CONTRACT_COLUMNS, contract_rows

CROSS_COLUMNS = ("Enabled", "Change", "From", "To", "CAN ID", "Length", "Ethernet PDU", "Header ID", "Remark")


def cross_rows(tplan: TopologyPlan) -> list[tuple]:
    rows = []
    for c in tplan.cross:
        s, d = c.src.message, c.dst.message
        cid = s.id_text if s.can_id == d.can_id else f"{s.id_text} -> {d.id_text}"
        rows.append(("yes" if c.enabled else "no", c.change if c.enabled else "",
                     f"{c.src_ecu}/{c.src.bus.name}/{s.name}", f"{c.dst_ecu}/{c.dst.bus.name}/{d.name}", cid,
                     c.src.length, c.eth_pdu if c.enabled else "",
                     f"0x{c.header_id:08X}" if c.enabled and c.header_id >= 0 else "",
                     "; ".join(([c.reason] if c.reason else []) + c.notes)))
    return rows


def _table(head, rows) -> str:
    rows = [tuple(str(x) for x in r) for r in rows]
    widths = [max(len(h), *(len(r[k]) for r in rows)) if rows else len(h) for k, h in enumerate(head)]
    line = lambda vals: "  ".join(v.ljust(w) for v, w in zip(vals, widths)).rstrip()
    return "\n".join([line(head), line(tuple("-" * w for w in widths))] + [line(r) for r in rows])


def text(tplan: TopologyPlan, verbose: bool = True) -> str:
    cfg = tplan.cfg
    out = []
    for node in cfg.ecus:
        plan = tplan.plans.get(node.name)
        tx, rx = cfg.ports(node)
        state = "generate" if node.generate else "reference"
        out.append(f"ECU {node.name} ({state}) {node.ip}  tx {tx} / rx {rx}")
        if plan is not None:
            out.append("    " + report.count_text(len(plan.enabled_routes), len(plan.enabled_can_routes)) +
                       (f", output {plan.cfg.output}" if node.generate else ""))
    for p in cfg.peers:
        tx, rx = cfg.ports(p)
        out.append(f"Peer {p.name}{' (default)' if p.name == cfg.default_peer else ''} {p.ip}  tx {tx} / rx {rx}")
    if tplan.cross:
        out += ["", "ECU -> ECU:", _table(CROSS_COLUMNS, cross_rows(tplan))]
    if verbose and tplan.links:
        out += ["", "Ethernet PDUs (contract):", _table(CONTRACT_COLUMNS, contract_rows(tplan))]
    return "\n".join(out)
