"""Generate the gateway files of a topology, the contract (every Ethernet PDU of the network) and the lock file."""
from __future__ import annotations

import csv

from ..planner import load_base
from ..writer import Result, generate
from .planner import TopologyPlan

CONTRACT_COLUMNS = ("Sender", "Bus", "Message", "CAN ID", "Length", "Ethernet PDU", "Header ID", "From", "Receiver",
                    "To", "Receiver bus / message", "Header note")


def contract_rows(tplan: TopologyPlan) -> list[tuple]:
    """One row per Ethernet PDU and receiver."""
    rows = []
    for link in tplan.links:
        for node, addr, target in link.receivers:
            rows.append((link.sender, link.bus, link.message, link.can_id, link.length, link.eth_pdu,
                         f"0x{link.header_id:08X}", link.sender_addr, node, addr, target, link.header_note))
    return rows


def write_contract(tplan: TopologyPlan, path: str | None = None) -> str:
    path = path or tplan.cfg.contract_path
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(CONTRACT_COLUMNS)
        w.writerows(contract_rows(tplan))
    return path


def generate_topology(tplan: TopologyPlan, only: list[str] | None = None) -> list[tuple[str, Result]]:
    """Write the gateway file of the selected ECUs (default: those with generate = True), then the lock file and
    the contract next to the topology file."""
    if not tplan.ok:
        raise ValueError("The topology plan has errors:\n" + "\n".join(
            tplan.errors + [f"[{n}] {e}" for n, p in tplan.plans.items() for e in p.errors]))
    cfg = tplan.cfg
    names = list(only) if only else [n.name for n in cfg.ecus if n.generate]
    unknown = [n for n in names if n not in tplan.plans]
    if unknown:
        raise ValueError("Not an ECU of the topology: " + ", ".join(unknown))
    results = []
    for name in names:
        plan = tplan.plans[name]
        results.append((name, generate(plan, load_base(plan.cfg))))
    if cfg.path:
        cfg.save_lock(tplan.header_ids)
        write_contract(tplan)
    return results
