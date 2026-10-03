"""Command line of the CAN <-> Ethernet gateway generator.

    python -m ecucstudio gateway inspect  --base network.arxml [--dbc bus.dbc]
    python -m ecucstudio gateway template [--base network.arxml] --dbc bus.dbc --node ECU -o gateway.json
    python -m ecucstudio gateway suggest  gateway.json [--write]
    python -m ecucstudio gateway plan     gateway.json
    python -m ecucstudio gateway generate gateway.json [-o out.arxml]
    python -m ecucstudio gateway gui      [gateway.json]
    python -m ecucstudio gateway routes   network.arxml [--csv routes.csv]
    python -m ecucstudio gateway edit     network.arxml --header PDU=0x123 --delete PDU --port SOCKET=50000
    python -m ecucstudio gateway editor   [network.arxml]
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
    from . import dbcread, dvproject, report
    from .base import Base
    if a.base and dvproject.is_project(a.base):
        proj = dvproject.read(a.base)
        base = dvproject.load_communication(proj)
        print(f"DaVinci project {proj.name}: ECU instance {proj.ecu_path}")
        print(f"Communication description: {proj.communication}")
        for i in proj.inputs:
            print(f"  input {i.category:<34} {os.path.basename(i.path)}")
        print(report.inventory(base))
        print(f"CAN channels of {proj.ecu_name} (messages received / sent):")
        for ch, rx, tx in dvproject.ecu_can_channels(base, proj.ecu_path):
            print(f"  {ch:<50} {rx:>4} / {tx:<4}")
    elif a.base:
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
    from . import dvproject
    from .config import BusInput, GatewayConfig
    proj = dvproject.read(a.base) if a.base and dvproject.is_project(a.base) else None
    if not proj and not a.dbc:
        print("[ERROR] give --dbc (or --base with a DaVinci project .dpa)", file=sys.stderr)
        return 2
    if proj:                    # additional input file of the project
        default_out = os.path.join(proj.dir, f"{proj.ecu_name}_CanEthGateway.arxml")
    elif a.base:
        default_out = os.path.splitext(a.base)[0] + "_gateway.arxml"
    else:                       # only DBC files: a new network file next to the first DBC
        default_out = os.path.splitext(a.dbc[0])[0] + "_network.arxml"
    cfg = GatewayConfig(base=os.path.abspath(a.base) if a.base else "",
                        output=os.path.abspath(a.output_arxml or default_out), ecu=a.ecu or "",
                        schema=a.schema)
    for d in a.dbc or []:
        cfg.buses.append(BusInput(dbc=os.path.abspath(d), node=a.node or ""))
    if proj:
        channels = a.can_channel or [c for c, _rx, _tx in dvproject.ecu_can_channels(
            dvproject.load_communication(proj), proj.ecu_path)]
        for c in channels:
            cfg.buses.append(BusInput(channel=c))
    cfg.ethernet.channel = a.channel or ""
    cfg.ethernet.vlan_id = a.vlan
    cfg.ethernet.ecu_ip = a.ecu_ip or ""
    cfg.ethernet.new_channel = bool(a.new_channel)
    if not a.no_suggest:
        from .suggest import apply, suggest
        try:
            for s in apply(cfg, suggest(cfg)):
                print("[SUGGESTED]", s)
        except (OSError, RuntimeError, ValueError) as exc:
            print("[INFO]      no Ethernet suggestion:", exc)
    cfg.save(a.out)
    print("Written", os.path.abspath(a.out), "- check the Ethernet settings and run 'plan'.")
    if proj and not a.can_channel:
        print(f"All {len(cfg.buses)} CAN channel(s) of {proj.ecu_name} are listed in 'buses'; remove the ones "
              f"that are not routed.")
    return 0


def cmd_suggest(a):
    from .suggest import apply, suggest
    cfg = _load(a.config)
    sugg = suggest(cfg)
    applied = apply(cfg, sugg) if a.write else []
    for s in sugg:
        tag = "[WRITTEN]  " if s in applied else ("[KEPT]     " if a.write else "[SUGGESTED]")
        print(tag, s)
    if not sugg:
        print("Nothing to suggest: all Ethernet settings are filled in.")
    if a.write and applied:
        cfg.save(a.config)
        print("Updated", os.path.abspath(a.config))
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


def _route_table(rows, cols):
    widths = [max(len(c), *(len(str(r[i])) for r in rows)) if rows else len(c) for i, c in enumerate(cols)]
    widths = [min(w, 60) for w in widths]
    line = lambda vals: "  ".join(str(v)[:w].ljust(w) for v, w in zip(vals, widths)).rstrip()
    return "\n".join([line(cols), line(["-" * w for w in widths])] + [line(r) for r in rows])


def cmd_routes(a):
    import csv
    from .existing import ROUTE_COLUMNS, GatewayModel, route_rows
    m = GatewayModel(a.file)
    routes = [r for r in m.routes if not a.direction or r.direction == a.direction]
    rows = route_rows(m, routes)
    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(ROUTE_COLUMNS)
            w.writerows(rows)
        print("Written", os.path.abspath(a.csv))
    else:
        print(_route_table(rows, ROUTE_COLUMNS))
    import collections
    print(f"\n{len(routes)} route(s): " + ", ".join(f"{k} {v}" for k, v in
                                                     sorted(collections.Counter(r.direction for r in routes).items())))
    for c in m.check():
        print("[WARNING]", c)
    return 0


def cmd_edit(a):
    from .existing import EditError, GatewayModel
    m = GatewayModel(a.file)

    def one_route(name):
        hits = m.find_routes(name)
        if len(hits) != 1:
            raise EditError(f"'{name}' matches {len(hits)} routes (use the Ethernet PDU or header id name)")
        return hits[0]

    try:
        for item in a.header:
            name, _, value = item.partition("=")
            if name in {h for h in m.ids} or name in {p.rsplit("/", 1)[-1] for p in m.ids}:
                id_path = next(p for p in m.ids if name in (p, p.rsplit("/", 1)[-1]))
            else:
                ids = (one_route(name).eth.ids if one_route(name).eth else [])
                if len(ids) != 1:
                    raise EditError(f"route '{name}' has {len(ids)} header ids: give the header id name instead")
                id_path = ids[0].path
            m.set_header_id(id_path, value)
        for item in a.port:
            name, _, value = item.partition("=")
            hits = [s for s in m.sockets() if name in (s.path, s.name)]
            if len(hits) != 1:
                raise EditError(f"socket '{name}' not found (or ambiguous)")
            m.set_port(hits[0].path, value)
        for item in a.ip:
            name, _, value = item.partition("=")
            ip, _, mask = value.partition("/")
            hits = [e for e in m.endpoints() if name in (e.path, e.name)]
            if len(hits) != 1:
                raise EditError(f"endpoint '{name}' not found (or ambiguous)")
            m.set_endpoint(hits[0].path, ip or None, mask or None)
        if a.delete:
            routes = []
            for name in a.delete:
                hits = m.find_routes(name)
                if not hits:
                    raise EditError(f"no route matches '{name}'")
                routes += [r for r in hits if r not in routes]
            m.delete_routes(routes, cleanup=not a.keep_pdus)
    except EditError as exc:
        print("[ERROR]", exc, file=sys.stderr)
        print("Nothing written.")
        return 1
    for c in m.changes:
        print("[CHANGED]" if not c.startswith("warning") else "[WARNING]", c)
    if not m.changes:
        print("Nothing to change.")
        return 0
    out = m.save(a.output or None)
    print("Written", out + ("" if a.output else " (previous version kept as .bak)"))
    return 0


def cmd_editor(a):
    from .editor_gui import main as editor_main
    editor_main(a.file)
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
    p.add_argument("--base", help="network file or DaVinci project .dpa (omit when there are only DBC files)")
    p.add_argument("--ecu", help="ECU instance name of a new file (default: the DBC node)")
    p.add_argument("--schema", default="AUTOSAR_00052", help="schema of a new file (DaVinci 5.24: AUTOSAR_00049 "
                                                             "or older)")
    p.add_argument("--dbc", action="append", help="DBC file (repeat for several buses)")
    p.add_argument("--can-channel", action="append",
                   help="with a .dpa: CAN channel of the project (path or cluster name; default: all of the ECU)")
    p.add_argument("--node")
    p.add_argument("--channel", help="Ethernet channel (path, short name or VLANnn)")
    p.add_argument("--new-channel", action="store_true", help="create a new Ethernet channel (VLAN)")
    p.add_argument("--vlan", type=int, help="VLAN id of a new channel (base file without Ethernet / --new-channel)")
    p.add_argument("--ecu-ip", help="IP address of the ECU when its network endpoint is created")
    p.add_argument("--output-arxml")
    p.add_argument("--no-suggest", action="store_true", help="do not fill in suggested Ethernet settings")
    p.add_argument("-o", "--out", required=True)
    p.set_defaults(fn=cmd_template)
    p = sub.add_parser("suggest", help="suggest the empty Ethernet settings of a gateway.json")
    p.add_argument("config")
    p.add_argument("--write", action="store_true", help="write the suggestions into the empty fields of the file")
    p.set_defaults(fn=cmd_suggest)
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
    p = sub.add_parser("routes", help="list the gateway routes of a network file")
    p.add_argument("file")
    p.add_argument("--direction", help="e.g. CAN->ETH")
    p.add_argument("--csv", help="write the table to a CSV file")
    p.set_defaults(fn=cmd_routes)
    p = sub.add_parser("edit", help="change the gateway of a network file")
    p.add_argument("file")
    p.add_argument("--header", action="append", default=[], metavar="ROUTE=VALUE",
                   help="header id of a route (Ethernet PDU, CAN frame or header id name)")
    p.add_argument("--port", action="append", default=[], metavar="SOCKET=PORT")
    p.add_argument("--ip", action="append", default=[], metavar="ENDPOINT=IP[/NETMASK]")
    p.add_argument("--delete", action="append", default=[], metavar="ROUTE", help="delete matching routes")
    p.add_argument("--keep-pdus", action="store_true", help="delete only the gateway mapping, keep the Ethernet PDUs")
    p.add_argument("-o", "--output", help="write to another file (default: the file itself, with .bak)")
    p.set_defaults(fn=cmd_edit)
    p = sub.add_parser("editor", help="open the gateway editor window")
    p.add_argument("file", nargs="?")
    p.set_defaults(fn=cmd_editor)
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
