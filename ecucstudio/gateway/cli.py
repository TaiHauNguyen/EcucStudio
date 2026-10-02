"""Command line of the CAN <-> Ethernet gateway generator.

    python -m ecucstudio gateway inspect  --base network.arxml [--dbc bus.dbc]
    python -m ecucstudio gateway template [--base network.arxml] --dbc bus.dbc --node ECU -o gateway.json
    python -m ecucstudio gateway plan     gateway.json
    python -m ecucstudio gateway generate gateway.json [-o out.arxml]
    python -m ecucstudio gateway gui      [gateway.json]
"""
from __future__ import annotations

import argparse
import os
import sys
import time


def _load(path):
    from .config import GatewayConfig
    return GatewayConfig.load(path)


def _print_plan(plan, verbose=True):
    from . import report
    print(report.summary(plan))
    if verbose and plan.routes:
        print()
        print(report.text_table(plan))
    for w in plan.infos:
        print("[INFO]   ", w)
    for w in plan.warnings:
        print("[WARNING]", w)
    for e in plan.errors:
        print("[ERROR]  ", e)


def cmd_inspect(a):
    from . import dbcread, report
    from .base import Base
    if a.base:
        print(report.inventory(Base(a.base)))
    for path in a.dbc or []:
        db = dbcread.load(path)
        print(f"\nDBC {db.name}: {len(db.messages)} messages, baud {db.baudrate or '?'}"
              f"{', CAN FD' if db.has_fd else ''}")
        for n in db.nodes:
            rx, tx = db.node_messages(n)
            print(f"  node {n:<24} receives {len(rx):>4}   sends {len(tx):>4}")
    return 0


def cmd_template(a):
    from .config import BusInput, GatewayConfig
    if a.base:
        default_out = os.path.splitext(a.base)[0] + "_gateway.arxml"
    else:                       # only DBC files: a new network file next to the first DBC
        default_out = os.path.splitext(a.dbc[0])[0] + "_network.arxml"
    cfg = GatewayConfig(base=os.path.abspath(a.base) if a.base else "",
                        output=os.path.abspath(a.output_arxml or default_out), ecu=a.ecu or "",
                        schema=a.schema)
    for d in a.dbc:
        cfg.buses.append(BusInput(dbc=os.path.abspath(d), node=a.node or ""))
    cfg.ethernet.channel = a.channel or ""
    cfg.ethernet.vlan_id = a.vlan
    cfg.ethernet.ecu_ip = a.ecu_ip or ""
    cfg.ethernet.new_channel = bool(a.new_channel)
    cfg.save(a.out)
    print("Written", os.path.abspath(a.out), "- fill in the Ethernet settings (ports, remote IP) and run 'plan'.")
    return 0


def cmd_plan(a):
    from .planner import make_plan
    t = time.time()
    plan = make_plan(_load(a.config))
    _print_plan(plan)
    print(f"\n{len(plan.enabled_routes)} route(s), {len(plan.warnings)} warning(s), {len(plan.errors)} error(s) "
          f"({time.time() - t:.1f} s)")
    return 1 if plan.errors else 0


def cmd_generate(a):
    from . import report
    from .planner import make_plan
    from .writer import generate
    t = time.time()
    cfg = _load(a.config)
    if a.output:
        cfg.output = os.path.abspath(a.output)
    plan = make_plan(cfg)
    _print_plan(plan, verbose=a.verbose)
    if plan.errors:
        print("\nNothing written: fix the errors above.")
        return 1
    res = generate(plan)
    csv_path = os.path.splitext(res.output)[0] + "_gateway_routes.csv"
    report.write_csv(plan, csv_path)
    for w in res.warnings[len(plan.warnings):]:
        print("[WARNING]", w)
    print(f"\nWritten {res.output} ({len(res.routes)} route(s), {time.time() - t:.1f} s)")
    print("Created:", ", ".join(f"{k} {v}" for k, v in sorted(res.created.items())))
    print("Route table:", csv_path)
    return 0


def cmd_gui(a):
    from .gui import main as gui_main
    gui_main(a.config)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ecucstudio gateway",
                                 description="Generate a CAN <-> Ethernet PDU gateway into a system description")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("inspect", help="list ECUs, channels, sockets of a base file and nodes of DBC files")
    p.add_argument("--base")
    p.add_argument("--dbc", action="append")
    p.set_defaults(fn=cmd_inspect)
    p = sub.add_parser("template", help="write a starting gateway.json")
    p.add_argument("--base", help="network file of the project (omit when there are only DBC files)")
    p.add_argument("--ecu", help="ECU instance name of a new file (default: the DBC node)")
    p.add_argument("--schema", default="AUTOSAR_00052", help="schema of a new file (DaVinci 5.24: AUTOSAR_00049 "
                                                             "or older)")
    p.add_argument("--dbc", action="append", required=True)
    p.add_argument("--node")
    p.add_argument("--channel", help="Ethernet channel (path, short name or VLANnn)")
    p.add_argument("--new-channel", action="store_true", help="create a new Ethernet channel (VLAN)")
    p.add_argument("--vlan", type=int, help="VLAN id of a new channel (base file without Ethernet / --new-channel)")
    p.add_argument("--ecu-ip", help="IP address of the ECU when its network endpoint is created")
    p.add_argument("--output-arxml")
    p.add_argument("-o", "--out", required=True)
    p.set_defaults(fn=cmd_template)
    p = sub.add_parser("plan", help="show the routes, header ids and warnings without writing")
    p.add_argument("config")
    p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("generate", help="write base file + gateway")
    p.add_argument("config")
    p.add_argument("-o", "--output")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(fn=cmd_generate)
    p = sub.add_parser("gui")
    p.add_argument("config", nargs="?")
    p.set_defaults(fn=cmd_gui)
    a = ap.parse_args(argv)
    if not getattr(a, "fn", None):
        return cmd_gui(argparse.Namespace(config=None))
    try:
        return a.fn(a)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
