"""Model of the main window (gateway/app.py): a zonal Ethernet network described by a network file (topology JSON).

* Ethernet nodes: one central computer (HPC, the topology's default peer) and the zonal ECUs (MAC, IPv4, port).
* CAN buses: every DBC file belongs to one zonal ECU (the ECU's gateway node in it).
* Routing table of the customer (always given): CAN -> CAN inside an ECU (PduR / Com signal gateway, .vsde file) and
  ECU -> ECU over Ethernet for rows whose buses belong to two ECUs.
* Every message an ECU receives goes to the HPC; a message an ECU sends comes from the HPC unless the routing table
  names a bus of another ECU as its source.

The functions here work on that model without any window; the window only shows and edits it.
"""
from __future__ import annotations

import copy
import os
import re
from dataclasses import dataclass, field

from . import dbcread, nodes
from .config import BusInput, GatewayConfig
from .planner import _match_network
from .topology.config import EcuNode, PeerNode, TopologyConfig

HPC, ZONAL = "HPC", "Zonal ECU"


def new_network(table: list | None = None) -> TopologyConfig:
    """A new network from the Ethernet node table of the user settings: the node marked HPC (else the first node) is
    the central computer, the others are zonal ECUs."""
    t = TopologyConfig(one_socket=True, table_hw=True)
    t.cross.also_to_default_peer = True       # every received message also goes to the HPC
    t.cross.from_default_peer = True          # a sent message without a source ECU in the table comes from the HPC
    t.cross.match_id = False
    table = list(table or [])
    hpc = next((n for n in table if getattr(n, "role", "") == "hpc"), table[0] if table else None)
    for n in table:
        if n is hpc:
            t.peers.append(PeerNode(name=n.name, ip=n.ip, tx_port=n.port, mac=n.mac))
            t.default_peer = n.name
        else:
            t.ecus.append(EcuNode(name=n.name, ip=n.ip, tx_port=n.port, mac=n.mac, generate=False,
                                  gateway=_gateway()))
    return t


def _gateway() -> GatewayConfig:
    g = GatewayConfig()
    g.options.dbc_imported = True             # the DBC files are imported in the ECU's DaVinci project
    g.ethernet.one_socket = True
    return g


def hpc(t: TopologyConfig) -> PeerNode | None:
    return next((p for p in t.peers if p.name == t.default_peer), None)


@dataclass
class NodeRow:
    name: str
    role: str                   # HPC | ZONAL
    mac: str
    ip: str
    port: int | None
    buses: int = 0


def node_rows(t: TopologyConfig) -> list[NodeRow]:
    out = []
    h = hpc(t)
    if h is not None:
        out.append(NodeRow(h.name, HPC, h.mac, h.ip, h.tx_port))
    out += [NodeRow(e.name, ZONAL, e.mac, e.ip, e.tx_port, len(e.gateway.buses)) for e in t.ecus]
    out += [NodeRow(p.name, ZONAL, p.mac, p.ip, p.tx_port) for p in t.peers if p is not h]
    return out


def set_node(t: TopologyConfig, old: str, row: NodeRow) -> str:
    """Add (old = '') or change a node; the HPC role moves to it (the former HPC becomes a zonal ECU). Returns an
    error text ('' = done)."""
    name = row.name.strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name):
        return "The name must start with a letter and contain letters, digits and _ only."
    if name != old and t.node(name) is not None:
        return f"There is already a node {name}."
    cur = t.node(old) if old else None
    if cur is None:
        cur = EcuNode(name=name, gateway=_gateway(), generate=False)
        t.ecus.append(cur)
    if old and old != name:
        if t.default_peer == old:
            t.default_peer = name
        if t.target == old:
            t.target = name
    cur.name, cur.mac, cur.ip, cur.tx_port = name, row.mac.strip(), row.ip.strip(), row.port
    if row.role == HPC and t.default_peer != name:
        make_hpc(t, name)
    elif row.role == ZONAL and t.default_peer == name:
        _to_ecu(t, cur)
        t.default_peer = ""
    return ""


def make_hpc(t: TopologyConfig, name: str):
    """*name* becomes the central computer; the former one becomes a zonal ECU. Its DBC files become unassigned."""
    old = hpc(t)
    if old is not None and old.name != name:
        _to_ecu(t, old)
    node = t.node(name)
    if isinstance(node, EcuNode):
        t.ecus.remove(node)
        t.unassigned += node.gateway.buses
        t.peers.insert(0, PeerNode(name=node.name, ip=node.ip, tx_port=node.tx_port, mac=node.mac))
    t.default_peer = name
    if t.target == name:
        t.target = ""


def _to_ecu(t: TopologyConfig, p):
    if isinstance(p, PeerNode):
        t.peers.remove(p)
        t.ecus.append(EcuNode(name=p.name, ip=p.ip, tx_port=p.tx_port, mac=p.mac, gateway=_gateway(), generate=False))


def remove_node(t: TopologyConfig, name: str):
    node = t.node(name)
    if isinstance(node, EcuNode):
        t.ecus.remove(node)
        t.unassigned += node.gateway.buses
    elif node is not None:
        t.peers.remove(node)
    if t.default_peer == name:
        t.default_peer = ""
    if t.target == name:
        t.target = ""


def default_nodes(t: TopologyConfig) -> list:
    """The network's nodes as the Ethernet node table of the user settings (HPC marked)."""
    return [nodes.EthNode(r.name, r.mac, r.ip, r.port, "hpc" if r.role == HPC else "zonal") for r in node_rows(t)]


# ---------------------------------------------------------------------------- CAN buses
def dbc_rows(t: TopologyConfig) -> list[tuple[str, BusInput]]:
    """(ECU name or '', bus) of every DBC file of the network."""
    return [(e.name, b) for e in t.ecus for b in e.gateway.buses] + [("", b) for b in t.unassigned]


def guess_ecu(t: TopologyConfig, db: dbcread.Database, node: str) -> str:
    """Zonal ECU a gateway node belongs to: the ECU of another DBC file with this node, the same name, the ECU
    attribute of the node, or the ECU name as a word of the node name (ZoneA_Body, Gw_ZoneA) - case does not matter.
    '' when none fits."""
    known = {ecu for ecu, b in dbc_rows(t) if ecu and node and b.node == node}
    if len(known) == 1:
        return known.pop()
    names = {e.name.lower(): e.name for e in t.ecus}
    for cand in (node, db.ecu_of(node) if db is not None and node else ""):
        if cand and cand.lower() in names:
            return names[cand.lower()]
    words = {w.lower() for w in re.split(r"[^A-Za-z0-9]+", node or "") if w}
    hits = [n for k, n in names.items() if k in words]
    return hits[0] if len(hits) == 1 else ""


def guess_node(t: TopologyConfig, db: dbcread.Database) -> str:
    """The node of *db* that is one of the zonal ECUs (by name, ECU attribute or a word of the node name), when
    exactly one node with messages is; else ''."""
    counts = {n: sum(len(x) for x in db.node_messages(n)) for n in db.nodes}
    hits = [n for n in db.nodes if counts.get(n) and guess_ecu(t, db, n)]
    return hits[0] if len(hits) == 1 else ""


def add_dbc(t: TopologyConfig, path: str, db: dbcread.Database, node: str, ecu: str) -> BusInput:
    b = BusInput(dbc=os.path.abspath(path), node=node)
    place_dbc(t, b, ecu)
    return b


def assign_node(t: TopologyConfig, node: str, ecu: str) -> int:
    """Give the DBC files without ECU whose gateway node is *node* to *ecu*; returns how many."""
    todo = [b for b in t.unassigned if node and b.node == node]
    for b in todo:
        place_dbc(t, b, ecu)
    return len(todo)


def place_dbc(t: TopologyConfig, b: BusInput, ecu: str):
    """Give DBC bus *b* to zonal ECU *ecu* ('' = not assigned)."""
    for e in t.ecus:
        if b in e.gateway.buses:
            e.gateway.buses.remove(b)
    if b in t.unassigned:
        t.unassigned.remove(b)
    target = t.ecu(ecu) if ecu else None
    (target.gateway.buses if target is not None else t.unassigned).append(b)


def remove_dbc(t: TopologyConfig, b: BusInput):
    place_dbc(t, b, "")
    t.unassigned.remove(b)


def bus_name(b: BusInput, db: dbcread.Database | None) -> str:
    return db.name if db is not None else os.path.splitext(os.path.basename(b.dbc))[0]


def table_network(b: BusInput, db: dbcread.Database | None, networks: list[str]) -> tuple[str, bool]:
    """(network column of the routing table for bus *b*, chosen by the user)."""
    if b.table_network:
        hit = next((n for n in networks if n.lower() == b.table_network.lower()), "")
        return hit, True
    return _match_network(networks, (bus_name(b, db), os.path.splitext(os.path.basename(b.dbc))[0])), False


# ---------------------------------------------------------------------------- routing table overview
@dataclass
class NetUse:
    network: str
    ecu: str = ""
    bus: str = ""
    kind: str = "CAN"           # CAN | LIN | Ethernet
    as_source: int = 0
    as_dest: int = 0


@dataclass
class TableOverview:
    rows: int = 0
    messages: int = 0
    signals: int = 0
    hw: int = 0
    problems: int = 0
    networks: list[NetUse] = field(default_factory=list)
    paths: list[tuple] = field(default_factory=list)     # (row, type, from, message / signal, to, path)
    counts: dict = field(default_factory=dict)            # path kind -> rows


def _kind(net: str) -> str:
    up = net.upper()
    return "Ethernet" if "ETH" in up else "LIN" if "LIN" in up else "CAN"


def overview(t: TopologyConfig, table, dbs: dict) -> TableOverview:
    """What the routing table means for this network, before planning: for each row whether it stays inside one ECU
    (CAN -> CAN), goes from one ECU to another (Ethernet), or is not routed (LIN, Ethernet column, no DBC)."""
    ov = TableOverview(rows=len(table.rows))
    owner = {}
    for ecu, b in dbc_rows(t):
        net, _ = table_network(b, dbs.get(os.path.abspath(b.dbc)), table.networks)
        if net and ecu:
            owner.setdefault(net, (ecu, bus_name(b, dbs.get(os.path.abspath(b.dbc)))))
    use = {n: NetUse(n, *owner.get(n, ("", "")), kind=_kind(n)) for n in table.networks}
    for r in table.rows:
        ov.messages += r.routing == "message"
        ov.signals += r.routing == "signal"
        ov.hw += r.hw
        if r.source in use:
            use[r.source].as_source += 1
        for d in r.dests:
            if d in use:
                use[d].as_dest += 1
        typ = (r.routing or "?") + (" (HW)" if r.hw else "")
        what = f"{r.src_msg}.{r.signal}" if r.routing == "signal" else r.src_msg
        target = (f"{r.target_msg}.{r.target_signal}" if r.routing == "signal" else r.target_msg)
        if target != what:
            what += f" -> {target}"
        if r.problems:
            ov.problems += 1
            paths = ["problem: " + "; ".join(r.problems)]
        else:
            paths = [_path(r, d, owner, t) for d in r.dests]
        for p in dict.fromkeys(paths):
            kind = category(p)
            ov.counts[kind] = ov.counts.get(kind, 0) + 1
        ov.paths.append((r.label, typ, r.source, what, ", ".join(r.dests), " | ".join(dict.fromkeys(paths))))
    ov.networks = list(use.values())
    return ov


def category(path: str) -> str:
    """Short name of a route of overview() for the counts."""
    if path.startswith("CAN -> CAN"):
        return "CAN -> CAN inside an ECU"
    if path.startswith("ECU -> ECU"):
        return "ECU -> ECU signal (message only)" if "message only" in path else "ECU -> ECU over Ethernet"
    if path.startswith("no DBC"):
        return "bus without DBC file"
    if path.startswith("problem"):
        return "problem"
    return path.split(":")[0]


def _path(r, d, owner, t) -> str:
    if r.hw and not t.table_hw:
        return "HW accelerator: not routed"
    src, dst = owner.get(r.source), owner.get(d)
    for net, end in ((r.source, src), (d, dst)):
        if end is None:
            k = _kind(net)
            return f"{k}: not routed" if k != "CAN" else f"no DBC: {net} is not a bus of an ECU"
    if src[0] == dst[0]:
        return f"CAN -> CAN in {src[0]}" + (" (Com signal gateway)" if r.routing == "signal" else "")
    if r.routing == "signal":
        return f"ECU -> ECU {src[0]} -> {dst[0]}: message only (signal gateway not generated)"
    return f"ECU -> ECU {src[0]} -> {dst[0]} (Ethernet)"


# ---------------------------------------------------------------------------- planning
def default_output(t: TopologyConfig, ecu: str) -> str:
    folder = os.path.dirname(t.path) if t.path else os.getcwd()
    return os.path.join(folder, f"{ecu}_Gateway.arxml")


def instance_name(e: EcuNode, dbs: dict) -> str:
    """ECU instance of the DaVinci project as the DBC converter names it (ECU attribute of the node, else the node)."""
    for b in e.gateway.buses:
        db = dbs.get(os.path.abspath(b.dbc))
        if db is not None and b.node:
            return db.ecu_of(b.node)
    return e.name


def planning_config(t: TopologyConfig, dbs: dict | None = None) -> TopologyConfig:
    """Copy of the network for the planner: only the target ECU is generated, zonal ECUs without DBC files are
    Ethernet-only nodes, every ECU gets its output file and ECU instance."""
    p = TopologyConfig.from_dict(copy.deepcopy(t.to_dict()))
    p.path = t.path
    p.cross.also_to_default_peer = True       # the rules of the main window, also for network files made before
    p.cross.from_default_peer = True
    p.one_socket = True                       # every node sends from and receives on its port base
    for e in list(p.ecus):
        if not e.gateway.buses:
            p.ecus.remove(e)
            p.peers.append(PeerNode(name=e.name, ip=e.ip, tx_port=e.tx_port, mac=e.mac))
            continue
        e.generate = e.name == t.target
        g = e.gateway
        g.output = g.output or default_output(t, e.name)
        g.ecu = g.ecu or instance_name(e, dbs or {})
        if not g.base:
            g.options.dbc_imported = True
    return p


def missing(t: TopologyConfig) -> str:
    """The next thing the user has to do ('' = ready to generate)."""
    if hpc(t) is None:
        return "1. Network: mark the central computer (HPC) - select it and press 'Set as HPC'."
    for r in node_rows(t):
        if not r.ip:
            return f"1. Network: enter the IPv4 address of {r.name}."
        if r.port is None:
            return f"1. Network: enter the port of {r.name}."
    if not t.ecus:
        return "1. Network: add the zonal ECUs."
    rows = dbc_rows(t)
    if not rows:
        return "2. CAN buses: add the DBC files of the network."
    if any(not ecu for ecu, _b in rows):
        return "2. CAN buses: choose the ECU of every DBC file (double-click the row)."
    if any(not b.node for _e, b in rows):
        return "2. CAN buses: choose the gateway node of every DBC file (double-click the row)."
    if not t.routing_table:
        return "3. Routing table: select the routing table (Excel) of the network."
    if not os.path.isfile(t.routing_table):
        return f"3. Routing table: {t.routing_table} does not exist."
    if not t.target or t.ecu(t.target) is None:
        return "4. Generate: choose the ECU to generate."
    if not t.ecu(t.target).gateway.buses:
        return f"4. Generate: {t.target} has no DBC file."
    return ""
