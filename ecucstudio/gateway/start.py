"""Guided start of the gateway generator: what does the user have, and good first values for it.

Three situations:

1. only DBC files (no DaVinci project yet): a complete new network file is generated;
2. a DaVinci project with the DBC files imported: an additional input file (Ethernet + gateway) is generated;
3. a gateway file generated before and already imported in the project: it is regenerated (DaVinci keeps what
   does not change).
"""
from __future__ import annotations

import collections
import os
import re
from dataclasses import dataclass, field

from . import dbcread, dvproject
from .config import BusInput, GatewayConfig
from .regen import META_GID, config_from_file

DAVINCI_SCHEMAS = {"5.24 or older": "AUTOSAR_00049", "5.31 or newer": "AUTOSAR_00052"}
_GW_NAME = re.compile(r"(^|_)(X?GW|GTW|GWY|GATEWAY|ZONE|ZCU|ZC|CGW)\d*(_|$)", re.IGNORECASE)
_ZONE_NAME = re.compile(r"^Z[A-Z]?\d+$", re.IGNORECASE)      # zone controllers named like Z1, ZC2, ZB3


@dataclass
class NodeGuess:
    node: str
    reason: str
    sure: bool                      # False: the user should check the choice


def node_counts(db: dbcread.Database) -> dict[str, tuple[int, int]]:
    """node -> (messages it receives, messages it sends)."""
    out = {}
    for n in db.nodes:
        rx, tx = db.node_messages(n)
        out[n] = (len(rx), len(tx))
    return out


def _in_file_name(node: str, path: str) -> bool:
    tokens = re.split(r"[^A-Za-z0-9]+", os.path.splitext(os.path.basename(path or ""))[0].upper())
    return node.upper() in tokens


def guess_nodes(dbs: list[dbcread.Database]) -> list[NodeGuess]:
    """The gateway ECU node of every DBC: a node that is in every DBC (one ECU on all buses), else a node named in
    the DBC file name, else a node with a gateway / zone-like name, else the node with the most messages. Different
    nodes in different DBCs mean different ECUs (see ecu_for_node)."""
    counts = [node_counts(db) for db in dbs]
    used = [{n for n, (rx, tx) in c.items() if rx + tx} for c in counts]
    if len(dbs) > 1:
        common = set.intersection(*used) if used else set()
        if common:
            best = max(sorted(common), key=lambda n: sum(sum(c[n]) for c in counts))
            return [NodeGuess(best, "the node that is in every DBC", True) for _ in dbs]
    shared = collections.Counter(n for names in used for n in names)
    out = []
    for db, c, names in zip(dbs, counts, used):
        in_name = [n for n in sorted(names) if _in_file_name(n, db.path)]
        gw = [n for n in sorted(names) if _GW_NAME.search(n) or _ZONE_NAME.match(n)]
        if len(in_name) == 1:
            out.append(NodeGuess(in_name[0], "the node named in the DBC file name", True))
        elif gw:
            # prefer the one that is also in other DBCs (an ECU on several buses), then the busiest
            best = max(gw, key=lambda n: (shared[n], sum(c[n])))
            out.append(NodeGuess(best, "its name looks like a gateway / zone ECU", len(gw) == 1))
        elif names:
            best = max(sorted(names), key=lambda n: sum(c[n]))
            out.append(NodeGuess(best, "it receives / sends the most messages - check it", False))
        else:
            out.append(NodeGuess("", "the DBC has no node with messages", False))
    return out


def ecu_for_node(node: str, bus: str) -> str:
    """ECU of a gateway node: the node itself (ZC1, ZC2 ...), or without the bus name when the node is named after
    the bus (XGW_Body on bus Body and XGW_Chassis on bus Chassis are one ECU XGW)."""
    n, b = node or "", (bus or "").strip("_")
    if b and len(n) > len(b) + 1:
        if n.lower().endswith("_" + b.lower()):
            return n[: -len(b) - 1]
        if n.lower().startswith(b.lower() + "_"):
            return n[len(b) + 1:]
    return n


def group_ecus(rows: list[tuple[str, str, str]]) -> dict[str, list[tuple[str, str]]]:
    """[(dbc path, gateway node, ECU)] -> {ECU: [(dbc path, node)]} in the order of the rows."""
    out: dict = {}
    for path, node, ecu in rows:
        out.setdefault(ecu or node, []).append((path, node))
    return out


def suggest_ips(n: int, vlan: int | None = None, like: str = "", used=()) -> list[str]:
    """*n* free addresses: in the subnet of *like* (an address a project already has), else 192.168.<VLAN>.x."""
    if like and like.count(".") == 3:
        prefix = like.rsplit(".", 1)[0]
    else:
        prefix = f"192.168.{vlan if vlan is not None and 0 < int(vlan) < 255 else 1}"
    out, k, taken = [], 11, set(used) | {like}
    while len(out) < n and k < 255:
        ip = f"{prefix}.{k}"
        if ip not in taken:
            out.append(ip)
        k += 1
    return out


def topology_for_dbcs(groups: dict, folder: str, schema: str, ips: dict, generate: dict | None = None,
                      outputs: dict | None = None, vlan: int | None = None, peer: tuple[str, str] | None = None,
                      tx_port: int = 50000, rx_port: int = 50001, name: str = "", projects: dict | None = None,
                      gateway_only: bool = False):
    """Several gateway ECUs -> topology: one ECU per group, optionally a central Ethernet node (*peer* = (name, ip))
    for the messages no ECU needs.

    An ECU of *projects* ({ECU: (dpa, [CAN channel paths])}) is read from its DaVinci project, in which its DBC files
    are imported: its output is an additional input file with only Ethernet + gateway (nothing is imported twice).
    The other ECUs come from their DBC files: a complete new network file each (or only referenced)."""
    from .topology import EcuNode, PeerNode, TopoEthernet, TopologyConfig
    t = TopologyConfig(name=name, ethernet=TopoEthernet(vlan_id=vlan), tx_port=tx_port, rx_port=rx_port)
    projects = projects or {}
    for ecu, dbcs in groups.items():
        out = (outputs or {}).get(ecu)
        if ecu in projects:
            dpa, channels = projects[ecu]
            g = GatewayConfig(base=os.path.abspath(dpa),
                              output=out or default_output(os.path.dirname(os.path.abspath(dpa)), ecu, project=True),
                              buses=[BusInput(channel=c) for c in channels])
        else:
            g = GatewayConfig(base="", output=out or default_output(folder, ecu, project=gateway_only), ecu=ecu,
                              schema=schema, buses=[BusInput(dbc=os.path.abspath(p), node=n) for p, n in dbcs])
            # the DBC files are imported in the ECU's DaVinci project: only Ethernet + gateway (gateway/imported.py)
            g.options.dbc_imported = gateway_only
        t.ecus.append(EcuNode(name=ecu, ip=ips.get(ecu, ""), generate=(generate or {}).get(ecu, True), gateway=g))
    if peer and peer[0]:
        t.peers.append(PeerNode(name=peer[0], ip=peer[1]))
        t.default_peer = peer[0]
    return t


def ecu_name_for(nodes: list[str]) -> str:
    """Name of the ECU-INSTANCE of a new file: the common node, else the common prefix of the nodes
    (XGW_Body + XGW_Chassis -> XGW), else the first node."""
    nodes = [n for n in nodes if n]
    if not nodes:
        return "GatewayEcu"
    if len(set(nodes)) == 1:
        return nodes[0]
    prefix = os.path.commonprefix(nodes).rstrip("_")
    return prefix if len(prefix) >= 3 else nodes[0]


def default_output(folder: str, ecu: str, project: bool = False) -> str:
    return os.path.join(folder, f"{ecu}_CanEthGateway.arxml" if project else f"{ecu}_Gateway.arxml")


def is_gateway_file(path: str) -> bool:
    """True for a gateway file generated by this tool (its configuration is embedded at the top)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(400_000)
    except OSError:
        return False
    return META_GID.encode() in head


def project_gateway_files(dpa: str) -> list[str]:
    """Gateway files of this tool that are input files of the DaVinci project."""
    try:
        proj = dvproject.read(dpa)
    except Exception:  # noqa: BLE001 - not readable: nothing found
        return []
    return [i.path for i in proj.inputs if i.path.lower().endswith(".arxml") and os.path.isfile(i.path)
            and is_gateway_file(i.path)]


@dataclass
class ProjectInfo:
    project: dvproject.DvProject
    base: object
    channels: list = field(default_factory=list)        # (path, label, received, sent)
    gateway_files: list = field(default_factory=list)


def read_project(dpa: str) -> ProjectInfo:
    proj = dvproject.read(dpa)
    base = dvproject.load_communication(proj)
    chans = []
    names = {c.path: c for c in base.can_channels()}
    for path, rx, tx in dvproject.ecu_can_channels(base, proj.ecu_path):
        c = names.get(path)
        label = c.cluster_name if c is not None and c.name.upper() in ("CHNL", "CHANNEL", "CH") else \
            path.rsplit("/", 1)[-1]
        chans.append((path, label, rx, tx))
    return ProjectInfo(proj, base, chans, project_gateway_files(dpa))


def project_ecu_ip(info: ProjectInfo) -> str:
    """IP address of the project's ECU on Ethernet ('' when it has none yet)."""
    b, want = info.base, info.project.ecu_path
    ecu = next((e for e in b.ecus() if want in (e, e.rsplit("/", 1)[-1])), "")
    if not ecu:
        return ""
    ips = {e.path: e.ip for ch in b.eth_channels() for e in ch.endpoints if e.ip}
    from ..arxml import q
    for c in b.ecu_connectors(ecu):
        el = b.el(c)
        for nep in b.refs(el.find(q("NETWORK-ENDPOINT-REFS")) if el is not None else None, "NETWORK-ENDPOINT-REF"):
            if ips.get(nep):
                return ips[nep].strip()
    return ""


def channels_in_use(info: ProjectInfo) -> list[str]:
    """CAN channels of the project on which the ECU receives or sends messages."""
    return [c[0] for c in info.channels if c[2] + c[3]]


def config_for_dbcs(dbcs: list[tuple[str, str]], output: str, ecu: str, schema: str, eth_routes: bool = True,
                    can_routes: bool = True) -> GatewayConfig:
    """Case 1: [(dbc path, gateway node)] -> configuration of a new network file."""
    cfg = GatewayConfig(base="", output=output, ecu=ecu, schema=schema)
    cfg.buses = [BusInput(dbc=os.path.abspath(p), node=n) for p, n in dbcs]
    cfg.options.eth_routes, cfg.options.can_routes = eth_routes, can_routes
    return cfg


def config_for_project(dpa: str, channels: list[str], output: str, eth_routes: bool = True,
                       can_routes: bool = True) -> GatewayConfig:
    """Case 2: CAN channels of the project -> configuration of an additional input file."""
    cfg = GatewayConfig(base=os.path.abspath(dpa), output=output)
    cfg.buses = [BusInput(channel=c) for c in channels]
    cfg.options.eth_routes, cfg.options.can_routes = eth_routes, can_routes
    return cfg


@dataclass
class UpdateInfo:
    cfg: GatewayConfig
    notes: list
    unused_channels: list = field(default_factory=list)  # (path, label, received, sent) not in the file yet
    routes: int = 0


def read_update(path: str) -> UpdateInfo:
    """Case 3: configuration of a generated gateway file and the project channels it does not use yet."""
    from .existing import GatewayModel
    cfg, notes = config_from_file(path)
    info = UpdateInfo(cfg, list(notes))
    try:
        info.routes = len(GatewayModel(path).routes)
    except Exception:  # noqa: BLE001 - only informative
        pass
    if dvproject.is_project(cfg.base) and os.path.isfile(cfg.base):
        try:
            p = read_project(cfg.base)
        except Exception as exc:  # noqa: BLE001 - shown to the user
            info.notes.append(f"Cannot read the project {cfg.base}: {exc}")
            return info
        have = {b.channel for b in cfg.buses if b.channel}
        # a bus may name its channel by path, short name or cluster name (/Cluster/<cluster>/<channel>)
        known = lambda path, label: {path, label, path.rsplit("/", 1)[-1], path.rsplit("/", 2)[-2]} & have
        info.unused_channels = [c for c in p.channels if not known(c[0], c[1])]
    elif cfg.base and not os.path.exists(cfg.base):
        info.notes.append("The DaVinci project / network file of this gateway was not found: select it.")
    return info
