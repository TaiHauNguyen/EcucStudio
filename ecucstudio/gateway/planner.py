"""Plan a CAN <-> Ethernet PDU gateway: routes, names, SoAd header ids and sockets.

The plan only reads the base file; :mod:`writer` applies it. Rules:

* messages the selected DBC node receives are routed CAN -> ETH, messages it sends ETH -> CAN (1:1)
* the SoAd header id is the CAN id, zero-padded to 32 bit. When that id is already used by another PDU
  received on the same socket, a flag is set in bits 29..31 (unused by 29-bit CAN ids) and a warning
  is reported
* a CAN bus that already exists in the base file is reused (frames are matched by CAN id), otherwise a
  new CAN cluster is created from the DBC
"""
from __future__ import annotations

import collections
import dataclasses
import math
import os
import re
from dataclasses import dataclass, field

from .. import arxml
from ..arxml import local, q
from . import dbcread, dvproject
from .base import Base, CanChannel, EthChannel, new_document
from .config import BusInput, GatewayConfig, SocketSide

CAN_TO_ETH = "CAN->ETH"
ETH_TO_CAN = "ETH->CAN"

_BAD = re.compile(r"[^A-Za-z0-9_]")


def sanitize(name: str) -> str:
    """AUTOSAR short name: [a-zA-Z][a-zA-Z0-9_]*, at most 128 characters."""
    n = _BAD.sub("_", str(name))
    if not n or not n[0].isalpha():
        n = "X" + n
    return n


def fmt(pattern: str, **fields) -> str:
    try:
        return sanitize(pattern.format(**fields))
    except (KeyError, IndexError, ValueError) as exc:
        raise ValueError(f"Naming pattern '{pattern}' uses an unknown field: {exc}") from None


def parse_int(v) -> int | None:
    if v is None or v == "":
        return None
    if isinstance(v, int):
        return v
    try:
        return int(str(v).strip(), 0)
    except ValueError:
        return None


@dataclass
class SignalSpec:
    name: str                   # I-SIGNAL short name
    system_signal: str          # SYSTEM-SIGNAL short name
    mapping: str                # I-SIGNAL-TO-I-PDU-MAPPING short name
    start: int
    length: int
    little_endian: bool
    signed: bool
    is_float: bool
    initial: float
    transfer: str


@dataclass
class Route:
    key: str                    # "<bus>/<message>"
    direction: str              # CAN->ETH | ETH->CAN
    bus: "BusPlan"
    message: dbcread.Message
    enabled: bool = True
    reason: str = ""            # why it is disabled / special
    change: str = ""            # regeneration: "kept" / "new" compared with the previous gateway file
    locked: bool = False        # header id kept from the previous gateway file
    can_problem: str = ""       # the CAN side cannot be used (existing frame unsuitable)
    prev: object = None         # matching route of the previous gateway file
    length: int = 0
    # CAN side: existing elements (paths) or names of new ones
    can_ft: str = ""            # existing CAN-FRAME-TRIGGERING path
    can_pt: str = ""            # existing PDU-TRIGGERING path (gateway end on CAN)
    can_frame: str = ""
    can_pdu: str = ""
    can_ft_name: str = ""
    can_pt_name: str = ""
    can_signals: list[SignalSpec] = field(default_factory=list)
    # Ethernet side (always new)
    eth_pdu: str = ""
    eth_pt_name: str = ""
    eth_id_name: str = ""
    eth_signals: list[SignalSpec] = field(default_factory=list)
    copy_signals_from: str = ""     # existing CAN I-SIGNAL-I-PDU whose signals are copied to the ETH PDU
    header_id: int = 0
    header_note: str = ""
    notes: list[str] = field(default_factory=list)
    peers: list[str] = field(default_factory=list)  # Ethernet nodes: CAN->ETH destinations / ETH->CAN source
    fanout_of: "Route | None" = None    # ETH->CAN 1:N: this bus gets the Ethernet PDU of that route (no own PDU)
    no_com: bool = False        # ETH->CAN: Com of the ECU does not send the CAN PDU (.vsde file of the DBC files)
    fanout_reason: str = ""     # ETH->CAN: why it does not share the Ethernet PDU of the same CAN id on another bus
    eth_send: str = ""          # CAN->ETH with PDU collection: "immediate" (TRIGGER_ALWAYS) | "collect" ("" = off)
    eth_send_why: str = ""

    @property
    def can_side_new(self) -> bool:
        return not self.can_ft

    @property
    def header_text(self) -> str:
        return f"0x{self.header_id:08X}"


@dataclass
class CanRoute:
    """PDU gateway between two CAN buses: the node receives the message on src.bus and sends it on dst.bus.
    *src* / *dst* are the CAN <-> Ethernet routes of those messages (their CAN side is shared)."""
    key: str
    src: Route
    dst: Route
    match: str
    enabled: bool = True
    reason: str = ""
    notes: list[str] = field(default_factory=list)
    change: str = ""
    direction: str = "CAN->CAN"
    vsde: bool = False          # routed by DaVinci from the extension file (.vsde) of the DBC files: no Com
    row: str = ""               # routing table row it comes from ("row 12 (#10)")
    keep_signals: list[str] = field(default_factory=list)  # (.vsde) source signals Com keeps: signal routes use them


@dataclass
class SignalRoute:
    """Com signal gateway from a routing table row (Routing Type = signal): the node receives *src_signal* in the
    message of route *src* and sends it as *dst_signal* in the message of route *dst*. Written to the .vsde file
    (COM-SIGNAL-ROUTING): the DBC converter makes the I-SIGNAL-MAPPING, DaVinci a ComGwMapping."""
    key: str                    # "<bus>/<msg>.<signal>-><bus>/<msg>.<signal>"
    src: Route                  # CAN -> ETH route of the source message (the node receives it)
    src_signal: dbcread.Signal
    dst: Route                  # ETH -> CAN route of the target message (the node sends it)
    dst_signal: dbcread.Signal
    row: str = ""
    enabled: bool = True
    reason: str = ""
    notes: list[str] = field(default_factory=list)
    change: str = ""
    direction: str = "SIGNAL"


@dataclass
class TableStatus:
    """What became of one routing table row: per network a (status, text, route or None, network)."""
    row: object                 # routing_table.TableRow
    outcomes: list = field(default_factory=list)

    def add(self, status: str, text: str, route=None, net: str = ""):
        self.outcomes.append((status, text, route, net))

    def replace(self, net: str, items: list):
        """Replace the outcome(s) of network *net* by *items* [(status, text, route)] (the topology knows more)."""
        new = [(s, t, r, net) for s, t, r in items]
        at = next((i for i, o in enumerate(self.outcomes) if o[3] == net), None)
        rest = [o for o in self.outcomes if o[3] != net]
        if at is None:
            self.outcomes = rest + new
        else:
            self.outcomes = rest[:at] + new + rest[at:]

    def items(self) -> list[tuple[str, str]]:
        """(status, text) per destination; a route made from the row is "routed" or "off" (with its reason)."""
        out = []
        for status, text, route, _net in self.outcomes:
            if route is None:
                out.append((status, text))
            elif route.enabled:
                notes = [n for n in route.notes if not n.startswith("routing table")]
                out.append(("routed", "; ".join([text] + notes)))
            else:
                out.append(("off", f"{text}: {route.reason or 'deselected'}"))
        return out

    @property
    def status(self) -> str:
        kinds = list(dict.fromkeys(s for s, _ in self.items()))
        return " + ".join(kinds) if kinds else "-"

    @property
    def detail(self) -> str:
        return " | ".join(t for _, t in self.items() if t)


@dataclass
class BusPlan:
    cfg: BusInput
    db: dbcread.Database
    name: str                   # {bus}
    channel: str                # CAN-PHYSICAL-CHANNEL path (existing or planned)
    new_cluster: bool
    cluster: str = ""           # CAN-CLUSTER path (existing or planned)
    connector: str = ""         # ECU connector path (existing or planned)
    new_connector: bool = False
    controller: str = ""        # planned controller path when the connector is new
    baudrate: int | None = None
    fd_baudrate: int | None = None


@dataclass
class SidePlan:
    """Sockets for one direction and one Ethernet peer; *_new* flags tell the writer what to create."""
    direction: str
    local: str                  # SOCKET-ADDRESS path
    local_new: bool
    local_port: int | None
    remote: str
    remote_new: bool
    remote_port: int | None
    remote_endpoint: str        # NETWORK-ENDPOINT path
    remote_endpoint_new: bool
    remote_ip: str
    remote_netmask: str
    connection: str             # STATIC-SOCKET-CONNECTION path
    connection_new: bool
    peer: str = ""


@dataclass
class Plan:
    cfg: GatewayConfig
    ecu: str = ""
    ecu_name: str = ""
    delta: bool = False             # output = additional input file for a DaVinci project (not base + gateway)
    system: str | None = None
    eth_cluster: str = ""
    eth_cluster_new: bool = False
    eth_channel: str = ""
    eth_channel_new: bool = False
    eth_vlan: int | None = None
    eth_controller: str = ""
    eth_controller_new: bool = False
    eth_connector: str = ""
    eth_connector_new: bool = False
    local_endpoint: str = ""
    local_endpoint_new: bool = False
    ecu_ip: str = ""
    ecu_netmask: str = ""
    id_set: str = ""
    id_set_new: bool = False
    gateway: str = ""
    gateway_new: bool = False
    packages: dict = field(default_factory=dict)
    buses: list[BusPlan] = field(default_factory=list)
    sides: dict = field(default_factory=dict)          # (direction, peer) -> SidePlan
    default_peer: str = "default"                      # name of the peer of ethernet.can_to_eth / eth_to_can
    routes: list[Route] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    infos: list[str] = field(default_factory=list)
    previous: str = ""                                 # gateway file of the previous generation
    removed: list = field(default_factory=list)        # PrevRoute of the previous file that are not generated

    can_routes: list = field(default_factory=list)     # CanRoute
    signal_routes: list = field(default_factory=list)  # SignalRoute (routing table)
    table_rows: list = field(default_factory=list)     # TableStatus: every row of the routing table
    table_file: str = ""                               # the routing table that was read
    table_nets: dict = field(default_factory=dict)     # network column of the routing table -> bus name
    load: list = field(default_factory=list)           # LoadRow: CAN -> ETH packets with / without PDU collection
    burst: list = field(default_factory=list)          # BurstRow: ETH -> CAN frames per collection window per bus

    @property
    def enabled_routes(self) -> list[Route]:
        return [r for r in self.routes if r.enabled]

    @property
    def enabled_can_routes(self) -> list:
        return [r for r in self.can_routes if r.enabled]

    @property
    def enabled_signal_routes(self) -> list:
        return [r for r in self.signal_routes if r.enabled]

    @property
    def ok(self) -> bool:
        return not self.errors


# default package of each element type when the base file has none of that type yet
DEFAULT_PACKAGES = {
    "CAN-CLUSTER": "/Topology/Clusters",
    "ETHERNET-CLUSTER": "/Topology/Clusters",
    "SOCKET-CONNECTION-IPDU-IDENTIFIER-SET": "/Topology/Clusters",
    "GATEWAY": "/Topology/HardwareComponents",
    "CAN-FRAME": "/Communication/Frames",
    "I-SIGNAL-I-PDU": "/Communication/PDUs",
    "I-SIGNAL": "/Communication/Signals",
    "SYSTEM-SIGNAL": "/Communication/SystemSignals",
    "SW-BASE-TYPE": "/DataTypes/BaseTypes",
}
# where to look first when the type itself is missing
_RELATED = {
    "CAN-CLUSTER": ("ETHERNET-CLUSTER", "LIN-CLUSTER", "FLEXRAY-CLUSTER"),
    "ETHERNET-CLUSTER": ("CAN-CLUSTER", "LIN-CLUSTER", "FLEXRAY-CLUSTER"),
    "SOCKET-CONNECTION-IPDU-IDENTIFIER-SET": ("CAN-CLUSTER", "ETHERNET-CLUSTER"),
    "GATEWAY": ("ECU-INSTANCE",),
    "CAN-FRAME": ("LIN-UNCONDITIONAL-FRAME", "FLEXRAY-FRAME", "ETHERNET-FRAME"),
    "I-SIGNAL-I-PDU": ("SECURED-I-PDU", "N-PDU", "NM-PDU", "DCM-I-PDU"),
    "I-SIGNAL": ("I-SIGNAL-GROUP",),
    "SYSTEM-SIGNAL": ("SYSTEM-SIGNAL-GROUP",),
    "SW-BASE-TYPE": ("IMPLEMENTATION-DATA-TYPE",),
}

_TRANSFER = {"cyclic": "PENDING", "onwrite": "TRIGGERED", "onwritewithrepetition": "TRIGGERED",
             "onchange": "TRIGGERED-ON-CHANGE", "onchangewithrepetition": "TRIGGERED-ON-CHANGE",
             "ifactive": "PENDING", "ifactivewithrepetition": "PENDING"}


def new_ecu_name(cfg: GatewayConfig) -> str:
    """Name of the ECU-INSTANCE of a new file: cfg.ecu, else the gateway node of the first DBC."""
    name = (cfg.ecu or "").rsplit("/", 1)[-1] or next((b.node for b in cfg.buses if b.node), "") or "GatewayEcu"
    return sanitize(name)


def load_base(cfg: GatewayConfig) -> Base:
    """The base file of *cfg*: a network file, the communication description of a DaVinci project (.dpa),
    or a new system description when no base file is given. The elements of the previous generation
    (cfg.previous) are taken out (base.prev_removed, base.prev_problem)."""
    from .regen import base_without_previous
    if dvproject.is_project(cfg.base):
        base = dvproject.load_communication(dvproject.read(cfg.base))
    elif cfg.base:
        base = Base(cfg.base)
    elif cfg.options.dbc_imported:
        from .imported import imported_view
        return imported_view([(b.dbc, b.node) for b in cfg.buses if b.dbc], new_ecu_name(cfg),
                             cfg.schema or "AUTOSAR_00052", cfg.output or "network_gateway.arxml")
    else:
        return new_document(cfg.output or "network_gateway.arxml", new_ecu_name(cfg), cfg.schema or "AUTOSAR_00052")
    base, base.prev_removed, base.prev_problem = base_without_previous(cfg, base)
    return base


_GW_PREFIXES = ("XGW_", "GW_", "GTW_", "GWY_")


def _norm_gateway_name(name: str) -> str:
    """Message name without a gateway prefix / suffix (GW_BCM_Status -> BCM_Status)."""
    n = name
    for p in _GW_PREFIXES:
        if n.upper().startswith(p):
            n = n[len(p):]
            break
    for sfx in ("_GW", "_GTW"):
        if n.upper().endswith(sfx):
            n = n[: -len(sfx)]
    return n


def choose_package(base: Base, tag: str) -> str:
    """Package for new elements of *tag*: where the base file keeps that type (or a related one)."""
    pkg = base.package_for(tag)
    for rel in _RELATED.get(tag, ()):
        if pkg:
            break
        pkg = base.package_for(rel)
    return pkg or DEFAULT_PACKAGES[tag]


class Planner:
    def __init__(self, cfg: GatewayConfig, base: Base | None = None, dbc_cache: dict | None = None):
        self.cfg = cfg
        self.project = dvproject.read(cfg.base) if dvproject.is_project(cfg.base) else None
        # DBC files imported in the ECU's DaVinci project (no project given): reference the converter's elements
        self.imported = not cfg.base and cfg.options.dbc_imported
        self.base = base or load_base(cfg)
        self.previous = None
        if cfg.previous and os.path.isfile(cfg.previous):
            from .regen import load_previous
            self.previous = load_previous(cfg.previous)
        self._prev_used: set = set()
        self._remotes: list = []            # (ECU, database, node) of the buses of other ECUs
        self.dbc_cache = dbc_cache if dbc_cache is not None else {}
        self.plan = Plan(cfg)
        self._taken: set[str] = set()          # planned paths (to keep new names unique)
        self._planned: dict = {}               # new sockets / endpoints / connections shared by both directions
        # CAN -> CAN routes DaVinci already makes from the extension file of this gateway (not "already routed")
        from . import vsde
        self._own_vsde = set()
        for f in {vsde.path_for(x) for x in (cfg.previous, cfg.output) if x}:
            self._own_vsde |= vsde.triggerings(f)

    # ------------------------------------------------------------------ helpers
    def err(self, msg):
        self.plan.errors.append(msg)

    def warn(self, msg):
        self.plan.warnings.append(msg)

    def info(self, msg):
        self.plan.infos.append(msg)

    def _find(self, value: str, tag: str, candidates: list[str] | None = None) -> str | None:
        """Path of an element given by path or short name."""
        if not value:
            return None
        cands = candidates if candidates is not None else self.base.of_type(tag)
        if value in cands:
            return value
        hits = [p for p in cands if p.rsplit("/", 1)[-1] == value]
        return hits[0] if len(hits) == 1 else None

    def _unique(self, parent: str, name: str, what: str, quiet: bool = False) -> str:
        free = self.base.free_name(parent, name, self._taken)
        if free != name and not quiet:
            self.warn(f"{what} '{name}' already exists in {parent}; using '{free}'")
        self._taken.add(f"{parent}/{free}")
        return free

    def _package(self, tag: str) -> str:
        if tag not in self.plan.packages:
            self.plan.packages[tag] = choose_package(self.base, tag)
        return self.plan.packages[tag]

    def dbc(self, path: str) -> dbcread.Database:
        key = os.path.abspath(path)
        if key not in self.dbc_cache:
            self.dbc_cache[key] = dbcread.load(path)
        return self.dbc_cache[key]

    # ------------------------------------------------------------------ main
    def run(self) -> Plan:
        if self.prepare():
            self.assign_header_ids()
            self.finish()
        return self.plan

    def prepare(self) -> bool:
        """Routes, CAN -> CAN pairs, Ethernet sockets and identifier set; False when errors stop the planning.
        The multi-ECU planner assigns the header ids of all ECUs between prepare() and assign_header_ids()."""
        cfg, plan = self.cfg, self.plan
        if cfg.previous:
            plan.previous = os.path.abspath(cfg.previous)
            if self.previous is None:
                self.warn(f"Previous gateway file {cfg.previous} does not exist: all routes are new.")
            else:
                problem = getattr(self.base, "prev_problem", "")
                if problem:
                    self.err(problem)
                removed = getattr(self.base, "prev_removed", None)
                n = sum(removed.values()) if removed else 0
                self.info(f"Regenerating {os.path.basename(cfg.previous)} ({len(self.previous.routes)} route(s)): "
                          + (f"its {n} element(s) were taken out of the base; " if n else "")
                          + "Ethernet PDU names and header ids of the routes that stay are kept.")
        if self.project:
            plan.delta = True
            self.info(f"DaVinci project {self.project.name}: CAN messages are read from "
                      f"{os.path.basename(self.project.communication)}; the output is an additional input file "
                      f"(Ethernet + gateway) for ECU instance {self.project.ecu_name}, the DBC files stay imported.")
        if self.imported:
            plan.delta = True
            self.info(f"The DBC files are imported in the DaVinci project of ECU instance "
                      f"{self.base.ecus()[0].rsplit('/', 1)[-1] if self.base.ecus() else '?'}: the output holds only "
                      f"Ethernet + gateway and references the CAN part DaVinci created from them.")
        elif not cfg.base:
            self.info(f"No base file: a new system description ({self.base.schema}) is created with the ECU "
                      f"{self.base.ecus()[0].rsplit('/', 1)[-1] if self.base.ecus() else '?'}.")
            if not cfg.output:
                self.err("Select the output file (there is no base file).")
        self._resolve_ecu()
        if plan.errors:
            return False
        eth = cfg.options.eth_routes
        if eth:
            self._check_peers()
        if eth and not plan.errors:
            self._resolve_ethernet()
        if plan.errors:
            return False
        for bus_cfg in cfg.buses:
            try:
                self._plan_bus(bus_cfg)
            except (OSError, RuntimeError, ValueError) as exc:
                self.err(f"{bus_cfg.dbc or '(no DBC)'}: {exc}")
        if not cfg.buses:
            self.err("No DBC file selected.")
        if plan.errors:
            return False
        if cfg.options.can_routes and cfg.routing_table:
            self._plan_table()
        elif cfg.options.can_routes:
            self._plan_can_routes()
        elif cfg.routing_table:
            self.info("CAN -> CAN routing is off: the routing table is not used.")
        if eth and self._remotes:
            self._plan_remote_routes()
        if eth and cfg.options.eth_fanout:
            self._plan_eth_fanout()
        if eth:
            self._plan_vsde_tx()
        if plan.errors:
            return False
        if eth and plan.enabled_routes:
            self._resolve_sides()
            self._resolve_id_set()
        if eth and plan.enabled_routes and not plan.errors:
            self._plan_collection()
        return True

    def assign_header_ids(self):
        if self.cfg.options.eth_routes and self.plan.enabled_routes:
            self._assign_header_ids()

    def finish(self):
        """Gateway, SYSTEM and the comparison with the previous gateway file."""
        b, cfg, plan = self.base, self.cfg, self.plan
        if plan.enabled_routes or plan.enabled_can_routes:
            self._resolve_gateway()
        plan.system = self._find(cfg.system, "SYSTEM") if cfg.system else b.system_for(plan.ecu)
        if cfg.options.add_fibex and plan.system is None:
            self.warn("The base file has no SYSTEM; new elements are not added to FIBEX-ELEMENTS.")
        if self.previous is not None:
            plan.removed = [p for p in self.previous.routes if id(p) not in self._prev_used]
            self._describe_removed(plan.removed)
            every = plan.enabled_routes + plan.enabled_can_routes
            kept = sum(1 for r in every if r.change == "kept")
            new = sum(1 for r in every if r.change == "new")
            self.info(f"Compared with the previous file: {kept} route(s) kept, {new} new, {len(plan.removed)} removed.")
        if not plan.enabled_routes and not plan.enabled_can_routes and not plan.enabled_signal_routes:
            self.warn("No message is selected for routing.")

    # ------------------------------------------------------------------ ECU / Ethernet
    def _resolve_ecu(self):
        b, cfg, plan = self.base, self.cfg, self.plan
        ecus = b.ecus()
        if not cfg.base and len(ecus) == 1:          # new file: the ECU created from cfg.ecu / the DBC node
            plan.ecu = ecus[0]
        elif self.project and not cfg.ecu and self._find(self.project.ecu_path, "ECU-INSTANCE"):
            plan.ecu = self._find(self.project.ecu_path, "ECU-INSTANCE")
        elif cfg.ecu:
            plan.ecu = self._find(cfg.ecu, "ECU-INSTANCE") or ""
            if not plan.ecu:
                names = ", ".join(p.rsplit("/", 1)[-1] for p in ecus)
                self.err(f"ECU '{cfg.ecu}' is not in the base file (ECUs: {names}).")
                return
        elif len(ecus) == 1:
            plan.ecu = ecus[0]
        else:
            with_gw = [b.ref(b.el(g), "ECU-REF") for g in b.gateways()]
            with_gw = [e for e in with_gw if e in ecus]
            if len(with_gw) == 1:
                plan.ecu = with_gw[0]
            else:
                self.err("Select the gateway ECU: the base file has " +
                         (f"{len(ecus)} ECU instances." if ecus else "no ECU-INSTANCE."))
                return
        plan.ecu_name = plan.ecu.rsplit("/", 1)[-1]

    def _resolve_ethernet(self):
        """Ethernet channel, ECU connector and local endpoint. Missing ones are planned as new elements:
        no Ethernet cluster in the base file, a new VLAN, or an ECU that is not connected to the channel yet."""
        b, eth, plan = self.base, self.cfg.ethernet, self.plan
        channels = b.eth_channels()
        mine = [c for c in channels if any(b.connector_ecu(x) == plan.ecu for x in c.connectors)]
        ch = None
        if not eth.new_channel and channels:
            if eth.channel:
                hits = [c for c in channels if c.path == eth.channel or c.name == eth.channel or
                        (c.vlan is not None and eth.channel.strip().upper() in (f"VLAN{c.vlan}", str(c.vlan)))]
                if len(hits) != 1:
                    self.err(f"Ethernet channel '{eth.channel}' not found (or ambiguous) in the base file.")
                    return
                ch = hits[0]
            elif len(mine) == 1:
                ch = mine[0]
            elif not mine and len(channels) == 1:
                ch = channels[0]
            else:
                self.err(f"Select the Ethernet channel (VLAN): {plan.ecu_name} is connected to {len(mine)} of "
                         f"{len(channels)} channels (or create a new channel).")
                return
        if ch is None:
            if not channels and self.cfg.base:
                self.info("The base file has no Ethernet channel: a new Ethernet cluster / channel is created.")
            ch = self._new_channel()
            if ch is None:
                return
        else:
            plan.eth_channel, plan.eth_cluster, plan.eth_vlan = ch.path, ch.cluster, ch.vlan
        self._eth = ch
        conns = [c for c in ch.connectors if b.connector_ecu(c) == plan.ecu]
        if eth.connector and conns:
            plan.eth_connector = self._find(eth.connector, "ETHERNET-COMMUNICATION-CONNECTOR", conns) or ""
            if not plan.eth_connector:
                self.err(f"Connector '{eth.connector}' of {plan.ecu_name} is not connected to {ch.name}.")
                return
        elif conns:
            plan.eth_connector = conns[0]
            if len(conns) > 1:
                self.warn(f"{plan.ecu_name} has {len(conns)} connectors on {ch.name}; using "
                          f"{conns[0].rsplit('/', 1)[-1]} (choose another in the Ethernet settings).")
        elif not self._new_connector(ch):
            return
        # local endpoint = IP address of the ECU on the channel
        if eth.local_endpoint and not plan.eth_channel_new:
            plan.local_endpoint = self._find(eth.local_endpoint, "NETWORK-ENDPOINT",
                                             [e.path for e in ch.endpoints]) or ""
            if not plan.local_endpoint:
                self.err(f"Network endpoint '{eth.local_endpoint}' is not in {ch.name}.")
                return
        elif not plan.eth_connector_new:
            conn_el = b.el(plan.eth_connector)
            neps = [p for p in b.refs(conn_el.find(q("NETWORK-ENDPOINT-REFS")), "NETWORK-ENDPOINT-REF")
                    if p.startswith(ch.path + "/")]
            plan.local_endpoint = neps[0] if neps else ""
        if not plan.local_endpoint:
            ip = (eth.ecu_ip or "").strip()
            if not ip:
                if plan.eth_connector_new:
                    self.err(f"Enter the IP address of {plan.ecu_name} on {ch.name} (its network endpoint is "
                             f"created).")
                return              # existing connector without endpoint: only needed for new sockets
            if not _valid_ip(ip):
                self.err(f"'{ip}' is not an IPv4 address (IP address of {plan.ecu_name}).")
                return
            hits = [e for e in ch.endpoints if (e.ip or "").strip() == ip]
            if hits:
                plan.local_endpoint = hits[0].path
            else:
                name = self._unique(ch.path, fmt(self.cfg.naming.eth_endpoint, **self._eth_fields()),
                                    "Network endpoint")
                plan.local_endpoint, plan.local_endpoint_new = f"{ch.path}/{name}", True
                plan.ecu_ip, plan.ecu_netmask = ip, eth.ecu_netmask or "255.255.255.0"

    def _eth_fields(self) -> dict:
        v = self.plan.eth_vlan
        return dict(ecu=self.plan.ecu_name, vlan=f"VLAN{v}" if v is not None else "Untagged",
                    vlan_id="" if v is None else v)

    def _new_channel(self) -> EthChannel | None:
        """Plan a new ETHERNET-PHYSICAL-CHANNEL (and an ETHERNET-CLUSTER when the file has none)."""
        b, eth, plan, naming = self.base, self.cfg.ethernet, self.plan, self.cfg.naming
        clusters = b.of_type("ETHERNET-CLUSTER")
        vlan = eth.vlan_id
        if vlan is not None and not 0 < int(vlan) < 4095:
            self.err(f"VLAN id {vlan} is out of range (1..4094).")
            return None
        plan.eth_vlan = None if vlan is None else int(vlan)
        if eth.cluster:
            cluster = self._find(eth.cluster, "ETHERNET-CLUSTER", clusters)
            if not cluster:
                self.err(f"Ethernet cluster '{eth.cluster}' is not in the base file.")
                return None
        elif len(clusters) == 1:
            cluster = clusters[0]
        elif not clusters:
            pkg = self._package("ETHERNET-CLUSTER")
            cname = self._unique(pkg, fmt(naming.eth_cluster, **self._eth_fields()), "Ethernet cluster")
            cluster, plan.eth_cluster_new = f"{pkg}/{cname}", True
        else:
            self.err("The base file has several Ethernet clusters: select the cluster of the new channel.")
            return None
        if not plan.eth_cluster_new:
            for c in b.eth_channels():
                if c.cluster == cluster and c.vlan == plan.eth_vlan:
                    what = f"VLAN {c.vlan}" if c.vlan is not None else "an untagged channel"
                    self.err(f"{cluster.rsplit('/', 1)[-1]} already has {what} ({c.name}): select that channel "
                             f"instead of creating a new one.")
                    return None
        name = sanitize(eth.channel_name) if eth.channel_name else fmt(naming.eth_channel, **self._eth_fields())
        name = self._unique(cluster, name, "Ethernet channel")
        plan.eth_cluster, plan.eth_channel, plan.eth_channel_new = cluster, f"{cluster}/{name}", True
        vlan_text = f"VLAN {plan.eth_vlan}" if plan.eth_vlan is not None else "untagged"
        where = ("the new cluster " if plan.eth_cluster_new else "") + cluster.rsplit("/", 1)[-1]
        self.info(f"New Ethernet channel {name} ({vlan_text}) in {where}.")
        return EthChannel(path=plan.eth_channel, name=name, cluster=cluster, vlan=plan.eth_vlan)

    def _new_connector(self, ch: EthChannel) -> bool:
        """Plan an ETHERNET-COMMUNICATION-CONNECTOR of the ECU on *ch* (and a controller if the ECU has none)."""
        b, eth, plan, naming = self.base, self.cfg.ethernet, self.plan, self.cfg.naming
        ctrls = b.ecu_controllers(plan.ecu, "ETHERNET-COMMUNICATION-CONTROLLER")
        if eth.controller:
            plan.eth_controller = self._find(eth.controller, "ETHERNET-COMMUNICATION-CONTROLLER", ctrls) or ""
            if not plan.eth_controller:
                self.err(f"Ethernet controller '{eth.controller}' is not a controller of {plan.ecu_name}.")
                return False
        elif ctrls:
            plan.eth_controller = ctrls[0]
            if len(ctrls) > 1:
                self.warn(f"{plan.ecu_name} has {len(ctrls)} Ethernet controllers; the new connector uses "
                          f"{ctrls[0].rsplit('/', 1)[-1]} (choose another in the Ethernet settings).")
        else:
            name = self._unique(plan.ecu, fmt(naming.eth_controller, **self._eth_fields()), "Ethernet controller")
            plan.eth_controller, plan.eth_controller_new = f"{plan.ecu}/{name}", True
        name = self._unique(plan.ecu, fmt(naming.eth_connector, **self._eth_fields()), "Ethernet connector")
        plan.eth_connector, plan.eth_connector_new = f"{plan.ecu}/{name}", True
        ctrl = plan.eth_controller.rsplit("/", 1)[-1] + (", new" if plan.eth_controller_new else "")
        self.info(f"{plan.ecu_name} is connected to {ch.name} by the new connector {name} (controller {ctrl}).")
        return True

    # ------------------------------------------------------------------ buses
    def _plan_bus(self, bc: BusInput):
        b, plan, naming = self.base, self.plan, self.cfg.naming
        if bc.remote_ecu and bc.dbc:
            # bus of another ECU: only read (which messages it sends / receives); nothing of it is generated
            db = self.dbc(bc.dbc)
            if bc.node and bc.node not in db.nodes:
                raise ValueError(f"node '{bc.node}' is not in {db.name} (nodes: {', '.join(db.nodes)})")
            self._remotes.append((sanitize(bc.remote_ecu), db, bc.node))
            self.info(f"{db.name}: bus of {bc.remote_ecu}; its messages to / from {plan.ecu_name} go over Ethernet "
                      f"(no CAN -> CAN, no CAN element of it is generated).")
            return
        can_channels = b.can_channels()
        if not bc.dbc:
            if not self.project:
                raise ValueError("no DBC file selected")
            # messages of the ECU on a CAN channel of the DaVinci project
            hits = [c for c in can_channels if bc.channel and bc.channel in (c.path, c.name, c.cluster_name)]
            if len(hits) != 1:
                raise ValueError(f"select the CAN channel of the project ('{bc.channel}' not found or ambiguous)")
            db = dvproject.channel_database(b, hits[0].path, plan.ecu)
            node = plan.ecu_name
            if not b.ecu_connector_on(plan.ecu, hits[0].path):
                raise ValueError(f"{plan.ecu_name} is not connected to {hits[0].path}")
        else:
            db = self.dbc(bc.dbc)
            node = bc.node
            if not node:
                raise ValueError("select the gateway node of the DBC")
            if node not in db.nodes and not any(node in m.senders or node in m.receivers for m in db.messages):
                raise ValueError(f"node '{node}' is not in {db.name} (nodes: {', '.join(db.nodes)})")
        ch: CanChannel | None = None
        if self.imported and bc.dbc and not bc.channel:
            from .imported import channel_path
            ch = next((c for c in can_channels if c.path == channel_path(db.name)), None)
        if ch is None and bc.channel and not bc.new_channel:
            hits = [c for c in can_channels if bc.channel in (c.path, c.name, c.cluster_name)]
            if len(hits) != 1:
                raise ValueError(f"CAN channel '{bc.channel}' not found (or ambiguous) in the base file")
            ch = hits[0]
        # the channel of a converted DBC is called CHNL on every bus: then the cluster names the bus
        chname = (ch.cluster_name if ch.name.upper() in ("CHNL", "CHANNEL", "CH") else ch.name) if ch else db.name
        busname = sanitize(bc.bus or (db.name if not bc.dbc else chname))
        if ch is None and not bc.new_channel and not bc.channel:
            hits = [c for c in can_channels if busname.lower() in (c.name.lower(), c.cluster_name.lower())]
            if len(hits) == 1:
                ch = hits[0]
                self.info(f"{db.name}: using the existing CAN channel {ch.path} (same name as the bus).")
        fields = dict(bus=busname, ecu=plan.ecu_name, node=node)
        if ch is not None:
            bp = BusPlan(bc, db, busname, ch.path, False, ch.cluster, baudrate=ch.baudrate,
                         fd_baudrate=ch.fd_baudrate)
        else:
            pkg = self._package("CAN-CLUSTER")
            cluster = self._unique(pkg, fmt(naming.can_cluster, **fields), "CAN cluster")
            chname = fmt(naming.can_channel, **fields)
            bp = BusPlan(bc, db, busname, f"{pkg}/{cluster}/{chname}", True, f"{pkg}/{cluster}",
                         baudrate=bc.baudrate or db.baudrate or 500000,
                         fd_baudrate=bc.fd_baudrate or db.fd_baudrate or (2000000 if db.has_fd else None))
            self._taken.add(bp.channel)
            if not (bc.baudrate or db.baudrate):
                self.info(f"{db.name}: the DBC has no baud rate; using 500000 for the new cluster.")
        conn = b.ecu_connector_on(plan.ecu, bp.channel) if ch is not None else None
        if conn:
            bp.connector = conn
        else:
            ctrl = self._unique(plan.ecu, fmt(naming.can_controller, **fields), "CAN controller")
            cname = self._unique(plan.ecu, fmt(naming.can_connector, **fields), "CAN connector")
            bp.connector, bp.controller, bp.new_connector = f"{plan.ecu}/{cname}", f"{plan.ecu}/{ctrl}", True
        plan.buses.append(bp)
        rx, tx = db.node_messages(node)
        todo = ([(m, CAN_TO_ETH) for m in rx] if bc.rx else []) + ([(m, ETH_TO_CAN) for m in tx] if bc.tx else [])
        if not todo:
            self.warn(f"{db.name}: node {node} has no {'/'.join(x for x, f in (('RX', bc.rx), ('TX', bc.tx)) if f)} "
                      f"messages.")
        for m, direction in todo:
            self._plan_route(bp, m, direction)

    def _plan_route(self, bp: BusPlan, m: dbcread.Message, direction: str):
        b, plan, naming, opts = self.base, self.plan, self.cfg.naming, self.cfg.options
        over = bp.cfg.messages.get(m.name, {}) if isinstance(bp.cfg.messages, dict) else {}
        r = Route(f"{bp.name}/{m.name}", direction, bp, m, length=m.length)
        if m.nm and not bp.cfg.include_nm:
            r.enabled, r.reason = False, "NM message"
        elif m.diag and not bp.cfg.include_diag:
            r.enabled, r.reason = False, "diagnostic message"
        if "enabled" in over:
            r.enabled = bool(over["enabled"])
            if r.enabled:
                r.reason = ""
            elif not r.reason:
                r.reason = over.get("reason") or "deselected"
        fields = dict(bus=bp.name, msg=m.name, ecu=plan.ecu_name, node=bp.cfg.node or plan.ecu_name,
                      canid=f"{m.can_id:X}")
        # ---------------------------------------------------------- CAN side
        ft = None if bp.new_cluster else b.frame_triggering(bp.channel, m.can_id, m.extended)
        if ft is not None:
            self._existing_can(r, ft)
        else:
            if not bp.new_cluster:
                r.notes.append("frame not in the base file: created from the DBC")
            frame_pkg, pdu_pkg = self._package("CAN-FRAME"), self._package("I-SIGNAL-I-PDU")
            r.can_frame = self._unique(frame_pkg, fmt(naming.can_frame, **fields), "CAN frame")
            r.can_pdu = self._unique(pdu_pkg, fmt(naming.can_pdu, **fields), "PDU")
            r.can_ft_name = self._unique(bp.channel, fmt(naming.frame_triggering, frame=r.can_frame, **fields),
                                         "Frame triggering")
            r.can_pt_name = self._unique(bp.channel, fmt(naming.pdu_triggering, pdu=r.can_pdu, **fields),
                                         "PDU triggering")
            if m.multiplexed:
                r.notes.append("multiplexed message: PDUs are routed without signals")
            else:
                r.can_signals = self._signals(m, naming.can_signal, naming.system_signal, fields, bp.channel)
        if self.imported and not m.il_support:
            # the DBC converter makes a USER-DEFINED-PDU without PDU triggering: nothing to reference
            r.enabled = False
            r.can_problem = r.reason = "GenMsgILSupport = No in the DBC: DaVinci imports it without PDU triggering"
        # ---------------------------------------------------------- Ethernet side
        if not opts.eth_routes:
            r.enabled, r.reason, r.header_id = False, "CAN <-> Ethernet routing is off", -1
            plan.routes.append(r)
            return
        pdu_pkg = self._package("I-SIGNAL-I-PDU")
        eth_name = sanitize(over["eth_pdu"]) if over.get("eth_pdu") else fmt(naming.eth_pdu, **fields)
        prev = None
        if self.previous is not None:
            can_pt = r.can_pt or (f"{bp.channel}/{r.can_pt_name}" if r.can_pt_name else "")
            prev = self.previous.match(can_pt, direction, eth_name)
            if prev is not None and not over.get("eth_pdu"):
                eth_name = prev.eth_pdu                     # same name -> same paths and UUIDs as before
            bus_known = any(x.can_pt.startswith(bp.channel + "/") for x in self.previous.routes)
            if "enabled" not in over and opts.only_previous and bus_known:
                if prev is None and r.enabled:
                    r.enabled, r.reason = False, "not in the previous gateway file"
                elif prev is not None and not r.enabled:
                    r.enabled, r.reason = True, ""
            r.prev = prev
            if r.enabled:
                r.change = "kept" if prev is not None else "new"
                if prev is not None:
                    self._prev_used.add(id(prev))
        r.header_id = -1
        if r.can_pt and self._already_routed(r.can_pt, f"{pdu_pkg}/{eth_name}", direction):
            r.enabled, r.reason = False, f"already routed to {eth_name} in the base file"
            r.eth_pdu = eth_name
            plan.routes.append(r)
            return
        if r.can_pt:
            self._gateway_notes(r)
            self._com_usage(r)
        # ETH->CAN 1:N in the previous file: the other buses had the same Ethernet PDU (they share it again)
        shared = (prev is not None and any(x.prev is not None and x.prev.eth_pdu == prev.eth_pdu for x in plan.routes)
                  or bool(over.get("eth_pdu")) and any(x.eth_pdu == eth_name for x in plan.routes))
        r.eth_pdu = self._unique(pdu_pkg, eth_name, "Ethernet PDU", quiet=shared)
        r.eth_pt_name = self._unique(plan.eth_channel, fmt(naming.pdu_triggering, pdu=r.eth_pdu, **fields),
                                     "PDU triggering")
        if opts.eth_signals == "copy":
            if r.can_signals:
                r.eth_signals = [
                    SignalSpec(name=self._unique(self._package("I-SIGNAL"),
                                                 fmt(naming.eth_signal, sig=s.mapping, eth_pdu=r.eth_pdu, **fields),
                                                 "Signal"),
                               system_signal=s.system_signal, mapping=s.mapping, start=s.start, length=s.length,
                               little_endian=s.little_endian, signed=s.signed, is_float=s.is_float,
                               initial=s.initial, transfer=s.transfer)
                    for s in r.can_signals]
            elif r.copy_signals_from:
                pass            # existing CAN PDU: the writer copies its signal mappings
        self._route_peers(r, over)
        r.header_id = -1
        if over.get("header_id") not in (None, ""):
            hid = parse_int(over["header_id"])
            if hid is None or not 0 <= hid <= 0xFFFFFFFF:
                self.err(f"{r.key}: header id '{over['header_id']}' is not a 32-bit number.")
            else:
                r.header_id = hid
                r.header_note = "set by user"
        elif prev is not None and prev.header_id is not None:
            r.header_id, r.locked = prev.header_id, True
            r.header_note = "kept from the previous file"
        plan.routes.append(r)

    # ------------------------------------------------------------------ Ethernet peers
    def _check_peers(self):
        eth, plan = self.cfg.ethernet, self.plan
        plan.default_peer = sanitize(eth.default_peer) if eth.default_peer else "default"
        self._peer_cfg = {plan.default_peer: (eth.can_to_eth, eth.eth_to_can)}
        for p in eth.peers:
            name = sanitize(p.name) if p.name else ""
            if not name:
                self.err("An Ethernet peer has no name.")
            elif name in self._peer_cfg or name == "default":
                self.err(f"Ethernet peer name '{name}' is used twice.")
            else:
                self._peer_cfg[name] = (p.can_to_eth, p.eth_to_can)

    def _route_peers(self, r: Route, over: dict):
        """Ethernet nodes of a route: CAN -> ETH can go to several peers, ETH -> CAN comes from one."""
        plan = self.plan
        alias = lambda n: plan.default_peer if n in ("", "default") else sanitize(str(n))
        many = over.get("eth_peers")
        if isinstance(many, str):
            many = [x.strip() for x in many.split(",")]
        if r.direction == CAN_TO_ETH:
            want = [alias(x) for x in (many or [plan.default_peer])]
        else:
            one = over.get("eth_peer") or (many[0] if many else "")
            want = [alias(one)]
            if many and len(many) > 1:
                self.warn(f"{r.key}: an ETH->CAN route has one source; using peer {want[0]}.")
        peers = []
        for n in want:
            if n not in self._peer_cfg:
                self.err(f"{r.key}: Ethernet peer '{n}' is not defined (peers: {', '.join(self._peer_cfg)}).")
            elif n not in peers:
                peers.append(n)
        r.peers = peers

    def _existing_can(self, r: Route, ft):
        """Route uses a frame that already exists in the base file."""
        b, plan = self.base, self.plan
        r.can_ft = b.path_of[ft]
        pts = b.refs(ft.find(q("PDU-TRIGGERINGS")), "PDU-TRIGGERING-REF")
        if not pts:
            r.enabled, r.reason = False, "existing frame has no PDU triggering"
            r.can_problem = r.reason
            return
        if len(pts) > 1:
            r.notes.append(f"frame carries {len(pts)} PDUs; routing {pts[0].rsplit('/', 1)[-1]}")
        r.can_pt = pts[0]
        pt = b.el(r.can_pt)
        pdu = b.el(b.ref(pt, "I-PDU-REF"))
        frame = b.el(b.ref(ft, "FRAME-REF"))
        if pdu is not None:
            length = parse_int(arxml.text(pdu, "LENGTH"))
            if length:
                if length != r.message.length:
                    r.notes.append(f"length {length} in the base file, {r.message.length} in the DBC; using {length}")
                    self.warn(f"{r.key}: PDU length is {length} in the base file but {r.message.length} in the DBC; "
                              f"using {length}.")
                r.length = length
            if local(pdu) == "I-SIGNAL-I-PDU":
                r.copy_signals_from = b.path_of[pdu]
            else:
                r.notes.append(f"{local(pdu)} is routed as an opaque PDU")
        r.can_frame = b.path_of.get(frame, "")
        r.can_pdu = b.path_of.get(pdu, "")
        r.notes.append("CAN frame reused from the base file")
        # direction check: the ECU must receive (CAN->ETH) / send (ETH->CAN) the frame
        bp = r.bus
        want = "IN" if r.direction == CAN_TO_ETH else "OUT"
        if not bp.new_connector:
            _port, have = b.port_of(ft, bp.connector, "FRAME-PORT-REF")
            if have and have != want:
                r.enabled = False
                r.can_problem = r.reason = (f"base file has {plan.ecu_name} "
                                            f"{'sending' if have == 'OUT' else 'receiving'} this "
                            f"frame")

    def _gateway_notes(self, r: Route):
        """1:N / N:1 information for a CAN PDU that is already routed in the base file."""
        b = self.base
        for g in b.gateways():
            for m in b.el(g).iter(q("I-PDU-MAPPING")):
                src = b.ref(m, "SOURCE-I-PDU-REF")
                dst = b.refs(m, "TARGET-I-PDU-REF")
                if r.direction == CAN_TO_ETH and src == r.can_pt:
                    r.notes.append("already routed to " + ", ".join(d.rsplit("/", 1)[-1] for d in dst) +
                                   " (adds a destination)")
                if r.direction == ETH_TO_CAN and r.can_pt in dst:
                    other = src.rsplit("/", 1)[-1] if src else "?"
                    r.notes.append(f"already a gateway target of {other} (N:1)")
                    self.warn(f"{r.key}: the CAN PDU is already the target of {other}; a second source makes an "
                              f"N:1 route (only supported as a MICROSAR extension).")

    # ------------------------------------------------------------------ CAN <-> CAN
    @staticmethod
    def _can_pt_path(r: Route) -> str:
        return r.can_pt or (f"{r.bus.channel}/{r.can_pt_name}" if r.can_pt_name else "")

    def _plan_can_routes(self):
        """Pair every message the node sends on one bus with the message it receives on another bus: same name,
        else same name without a gateway prefix, else (renamed) same CAN id, length and signal layout."""
        plan, cfg, opts = self.plan, self.cfg, self.cfg.options
        rx = [r for r in plan.routes if r.direction == CAN_TO_ETH and not r.can_problem]
        tx = [r for r in plan.routes if r.direction == ETH_TO_CAN and not r.can_problem]
        by_name, by_norm, by_id = (collections.defaultdict(list) for _ in range(3))
        for r in rx:
            m = r.message
            by_name[m.name].append(r)
            by_norm[_norm_gateway_name(m.name)].append(r)
            by_id[(m.can_id, m.extended, r.length)].append(r)
        pairs = {}
        for t in tx:
            m = t.message
            if (m.nm and not t.bus.cfg.include_nm) or (m.diag and not t.bus.cfg.include_diag):
                continue
            tiers = [(by_name.get(m.name), "same name"),
                     (by_norm.get(_norm_gateway_name(m.name)), "same name without gateway prefix")]
            if opts.can_match_id:
                tiers.append((by_id.get((m.can_id, m.extended, t.length)), "renamed: same CAN id and length"))
            for cands, how in tiers:
                cands = [c for c in (cands or []) if c.bus is not t.bus]
                if cands:
                    if len(cands) > 1:          # prefer the source with the same CAN id
                        same = [c for c in cands if c.message.can_id == m.can_id]
                        cands = same if len(same) == 1 else cands
                    pairs[id(t)] = (t, cands, how)
                    break
        for link in cfg.can_links:
            src = next((r for r in rx if r.bus.name == link.get("src_bus") and r.message.name == link.get("src_msg")),
                       None)
            dst = next((r for r in tx if r.bus.name == link.get("dst_bus") and r.message.name == link.get("dst_msg")),
                       None)
            if src is None or dst is None:
                self.warn(f"CAN link {link.get('src_bus')}/{link.get('src_msg')} -> {link.get('dst_bus')}/"
                          f"{link.get('dst_msg')}: message not received / sent by the node on that bus.")
                continue
            pairs[id(dst)] = (dst, [src], "link")
        prev_can = [p for p in (self.previous.routes if self.previous else []) if p.direction == "CAN->CAN"]
        for t, cands, how in pairs.values():
            src = cands[0]
            cr = CanRoute(f"{src.bus.name}/{src.message.name}->{t.bus.name}/{t.message.name}", src, t, how)
            if len(cands) > 1:
                cr.enabled = False
                cr.reason = (f"received on {len(cands)} buses (" + ", ".join(c.bus.name for c in cands) +
                             "): add a link to choose the source")
            else:
                self._check_can_route(cr, prev_can)
            plan.can_routes.append(cr)
        self._finish_can_routes("paired message(s) routed between the buses")

    def _finish_can_routes(self, what: str):
        """CAN -> CAN routes are planned: their target messages are not also fed from Ethernet; .vsde file."""
        plan = self.plan
        plan.can_routes.sort(key=lambda c: (c.src.bus.name, c.dst.bus.name, c.dst.message.can_id))
        # a message fed from another CAN bus is not also fed from Ethernet (that would be N:1)
        for cr in plan.enabled_can_routes:
            t = cr.dst
            over = t.bus.cfg.messages.get(t.message.name, {}) if isinstance(t.bus.cfg.messages, dict) else {}
            if over.get("enabled") and t.enabled:
                self.warn(f"{t.key}: sent from Ethernet and from {cr.src.bus.name} (CAN->CAN): two sources (N:1).")
            elif t.enabled:
                t.enabled, t.reason, t.change = False, f"fed from {cr.src.bus.name} (CAN->CAN)", ""
                if t.prev is not None:
                    self._prev_used.discard(id(t.prev))
            if cr.src.enabled:
                cr.notes.append("also routed to Ethernet (1:N)")
        if plan.can_routes:
            self.info(f"CAN -> CAN: {len(plan.enabled_can_routes)} of {len(plan.can_routes)} {what}.")
        self._plan_vsde()

    def _plan_vsde(self):
        """CAN -> CAN routes between buses of DBC files imported in DaVinci go into the extension file (.vsde) of
        the DBC converter: DaVinci routes them in PduR only, Com neither receives nor sends them."""
        from . import vsde
        plan = self.plan
        if not plan.delta:
            return
        name = os.path.basename(vsde.path_for(self.cfg.output or "gateway.arxml"))
        left = []
        for cr in plan.enabled_can_routes:
            why = vsde.problem(cr)
            if why:
                left.append(f"{cr.key} ({why})")
            else:
                cr.vsde = True
                cr.notes.append(f"PduR only, no Com ({name})")
        # a PDU routed as a whole whose signals are also signal-routed: Com keeps those signals (SOURCE-SIGNALS)
        keep = collections.defaultdict(set)
        for sr in plan.enabled_signal_routes:
            keep[id(sr.src)].add(sr.src_signal.name)
        for cr in plan.enabled_can_routes:
            cr.keep_signals = sorted(keep.get(id(cr.src), ())) if cr.vsde else []
            if cr.keep_signals:
                cr.notes.append(f"Com still receives {', '.join(cr.keep_signals)} (signal routing)")
        n = sum(1 for cr in plan.enabled_can_routes if cr.vsde)
        if n:
            self.info(f"CAN -> CAN: {n} route(s) are written to {name}: add it to the input files of the DaVinci "
                      f"project next to the DBC files. DaVinci then routes them in PduR only and removes them from "
                      f"Com (no CanIf -> Com, no Com -> CanIf).")
        sig = plan.enabled_signal_routes
        if sig:
            self.info(f"Signal routing: {len(sig)} signal(s) are written to {name} (COM-SIGNAL-ROUTING); DaVinci "
                      f"makes a ComGwMapping for each. Set /Com/ComGeneral/ComSignalGateway to "
                      f"COMPLETESIGNALPROCESSING (validation COM01009) and solve the main function / partition "
                      f"references of Com and of the new PDUs (COM02600, COM02702, RTE01216) once.")
        if left:
            self.warn(f"CAN -> CAN routes kept in the gateway file (Com keeps them): {', '.join(left[:6])}"
                      f"{' ...' if len(left) > 6 else ''}.")

    # ------------------------------------------------------------------ routing table (cfg.routing_table)
    def _plan_table(self):
        """CAN -> CAN routes from the routing table, and only from it: a message row becomes a PduR route (CanRoute)
        for every destination bus, a signal row a Com signal gateway (SignalRoute). Rows of LIN / Ethernet networks
        and of other ECUs' buses, and rows of a hardware accelerator, are only listed (plan.table_rows)."""
        from . import routing_table as rtab
        plan, cfg, opts = self.plan, self.cfg, self.cfg.options
        try:
            table = rtab.read(cfg.routing_table)
        except Exception as exc:        # OSError / ValueError / RuntimeError, and openpyxl's own errors
            self.err(f"Routing table {os.path.basename(cfg.routing_table)}: {exc}")
            return
        plan.table_file = table.path
        for w in table.warnings:
            self.warn(f"Routing table: {w}")
        nets = self._table_networks(table)
        plan.table_nets = {n: bp.name for n, bp in nets.items()}
        if cfg.can_links:
            self.info("CAN -> CAN links are not used: the CAN -> CAN routes come from the routing table.")
        prev_can = [p for p in (self.previous.routes if self.previous else []) if p.direction == "CAN->CAN"]
        rx, tx = collections.defaultdict(dict), collections.defaultdict(dict)   # bus -> {message: route}
        for r in plan.routes:
            (rx if r.direction == CAN_TO_ETH else tx)[r.bus.name][r.message.name] = r
        seen, hw_fed = {}, []
        for row in table.rows:
            st = TableStatus(row)
            plan.table_rows.append(st)
            if row.problems:
                st.add("problem", "; ".join(row.problems))
                continue
            sbp = nets.get(row.source)
            if sbp is None:
                st.add(*self._table_elsewhere(row.source, row.src_protocol, "source"), net=row.source)
                continue
            if row.hw and not opts.table_hw:        # the LLCE / PFE routes it: no PduR / Com route, no DBC check
                for net in row.dests:
                    dbp = nets.get(net)
                    if dbp is None:
                        st.add(*self._table_elsewhere(net, row.dst_protocol, "destination"), net=net)
                        continue
                    st.add("HW accelerator", f"{sbp.name}/{row.src_msg} -> {dbp.name}/{row.target_msg}: "
                                             f"HW-Accelerator = 1 (LLCE / PFE routes it)", net=net)
                    dst = self._table_route(tx, dbp, row.target_msg, row.dst_id, False)
                    if not isinstance(dst, str):
                        hw_fed.append((dst, sbp, row))
                continue
            src = self._table_route(rx, sbp, row.src_msg, row.src_id, True)
            if isinstance(src, str):
                st.add("problem", src)
                continue
            for net in row.dests:
                dbp = nets.get(net)
                if dbp is None:
                    st.add(*self._table_elsewhere(net, row.dst_protocol, "destination"), net=net)
                elif dbp is sbp:
                    st.add("problem", f"{net} is the source and a destination", net=net)
                else:
                    dst = self._table_route(tx, dbp, row.target_msg, row.dst_id, False)
                    if isinstance(dst, str):
                        st.add("problem", dst, net=net)
                    elif row.routing == rtab.MESSAGE:
                        st.add(*self._table_message(row, src, dst, seen, prev_can), net=net)
                    else:
                        st.add(*self._table_signal(row, src, dst, seen), net=net)
        self._table_conflicts()
        for dst, sbp, row in hw_fed:        # the accelerator sends it on that bus: not also from Ethernet
            over = dst.bus.cfg.messages.get(dst.message.name, {}) if isinstance(dst.bus.cfg.messages, dict) else {}
            if dst.enabled and "enabled" not in over:
                dst.enabled, dst.change = False, ""
                dst.reason = f"routed by the HW accelerator from {sbp.name} (routing table {row.label})"
                if dst.prev is not None:
                    self._prev_used.discard(id(dst.prev))
        count = collections.Counter(s for st in plan.table_rows for s, _ in st.items())
        self.info(f"Routing table {os.path.basename(table.path)}: {len(table.rows)} row(s); for {plan.ecu_name}: "
                  f"{len(plan.enabled_can_routes)} message route(s), {len(plan.enabled_signal_routes)} signal "
                  f"route(s)" + "".join(f", {n} {k}" for k, n in sorted(count.items())
                                        if k not in ("routed", "not this ECU")) +
                  " (see the Routing table section of the message report).")
        if count.get("HW accelerator"):
            self.info(f"Routing table: {count['HW accelerator']} destination(s) of rows with HW-Accelerator = 1 are "
                      f"left to the hardware accelerator (option 'Route HW accelerator rows too').")
        self._finish_can_routes("message route(s) of the routing table")

    def _table_networks(self, table) -> dict:
        """Network column of the routing table -> BusPlan of this ECU: BusInput.table_network, else the column named
        like the bus, the DBName or the DBC file (also one word of it, e.g. BusA in Vehicle_BusA_v3.dbc)."""
        out, plan = {}, self.plan
        for bp in plan.buses:
            if bp.cfg.table_network:
                net = table.network(bp.cfg.table_network)
                if not net:
                    self.warn(f"{bp.name}: the routing table has no network column '{bp.cfg.table_network}'.")
            else:
                net = _match_network(table.networks, (bp.name, bp.db.name if bp.db else "",
                                                      os.path.splitext(os.path.basename(bp.cfg.dbc or ""))[0]))
            if not net:
                continue
            if net in out:
                self.warn(f"Routing table network {net} matches the buses {out[net].name} and {bp.name}: choose the "
                          f"network of each bus (Edit DBC).")
                continue
            out[net] = bp
        self._table_remote = {}
        for ecu, db, _node in self._remotes:
            net = _match_network(table.networks, (db.name, os.path.splitext(os.path.basename(db.path))[0]))
            if net and net not in out:
                self._table_remote[net] = ecu
        unmatched = [bp.name for bp in plan.buses if bp not in out.values()]
        self.info("Routing table networks: " + (", ".join(f"{n} = {bp.name}" for n, bp in out.items()) or "none") +
                  (f"; bus(es) without a network column: {', '.join(unmatched)} (Edit DBC: Routing table network)"
                   if unmatched else "") + ".")
        return out

    def _table_elsewhere(self, net: str, protocol: str, side: str) -> tuple[str, str]:
        """(status, text) of a network that is not a CAN bus of this ECU."""
        if net in self._table_remote:
            return "other ECU", f"{net} is a bus of {self._table_remote[net]} (CAN -> ETH -> CAN follows the DBC files)"
        hint = (net + " " + protocol).upper()
        if "ETH" in hint:
            return "Ethernet", f"{side} {net} is an Ethernet network (CAN <-> Ethernet follows the DBC files)"
        if "LIN" in hint:
            return "LIN", f"{side} {net} is a LIN network (not supported)"
        return "not this ECU", f"{side} {net} is not a CAN bus of {self.plan.ecu_name}"

    def _table_route(self, index: dict, bp: BusPlan, name: str, can_id, received: bool):
        """Route of message *name* (else of CAN id *can_id*) the node receives / sends on bus *bp*, or why not."""
        routes = index.get(bp.name, {})
        r = routes.get(name)
        if r is None and can_id is not None:
            hits = [x for x in routes.values() if x.message.can_id == can_id]
            r = hits[0] if len(hits) == 1 else None
        if r is not None:
            return r
        node = bp.cfg.node or self.plan.ecu_name           # a channel of the DaVinci project: the ECU itself
        dbc = os.path.basename(bp.db.path) if bp.db and bp.db.path else bp.name
        m = next((x for x in bp.db.messages if x.name == name), None) if bp.db else None
        if m is None and can_id is not None and bp.db:
            m = next((x for x in bp.db.messages if x.can_id == can_id), None)
        if m is None:
            return f"{bp.name}: no message {name}" + (f" / 0x{can_id:X}" if can_id is not None else "") + f" in {dbc}"
        if received and node not in m.receivers:
            return f"{bp.name}: {node} does not receive {m.name} ({dbc})"
        if not received and node not in m.senders:
            return f"{bp.name}: {node} does not send {m.name} ({dbc})"
        return f"{bp.name}: the {'received' if received else 'sent'} messages of this bus are not selected (Edit DBC)"

    def _table_message(self, row, src: Route, dst: Route, seen: dict, prev_can: list):
        key = f"{src.bus.name}/{src.message.name}->{dst.bus.name}/{dst.message.name}"
        text = f"{src.bus.name}/{src.message.name} -> {dst.bus.name}/{dst.message.name}"
        if key in seen:
            return "duplicate", f"{text}: also in {seen[key].row}", None
        cr = CanRoute(key, src, dst, f"routing table {row.label}", row=row.label)
        if src.can_problem or dst.can_problem:
            cr.enabled, cr.reason = False, src.can_problem or dst.can_problem
        else:
            self._check_can_route(cr, prev_can)
        for r, want in ((src, row.src_msg), (dst, row.target_msg)):
            if r.message.name != want:
                cr.notes.append(f"{want} is {r.message.name} in the DBC (same CAN id)")
        seen[key] = cr
        self.plan.can_routes.append(cr)
        return "route", text, cr

    def _table_signal(self, row, src: Route, dst: Route, seen: dict):
        from . import vsde
        plan, name = self.plan, row.target_signal
        text = f"{src.bus.name}/{src.message.name}.{row.signal} -> {dst.bus.name}/{dst.message.name}.{name}"
        ss = next((s for s in src.message.signals if s.name == row.signal), None)
        ds = next((s for s in dst.message.signals if s.name == name), None)
        if ss is None or ds is None:
            miss, m = (row.signal, src.message) if ss is None else (name, dst.message)
            return "problem", f"{text}: no signal {miss} in {m.name} (DBC)", None
        key = f"{src.bus.name}/{src.message.name}.{ss.name}->{dst.bus.name}/{dst.message.name}.{ds.name}"
        if key in seen:
            return "duplicate", f"{text}: also in {seen[key].row}", None
        sr = SignalRoute(key, src, ss, dst, ds, row.label)
        node = src.bus.cfg.node or plan.ecu_name
        why = (src.can_problem or dst.can_problem or
               ("" if plan.delta else "a signal route needs the DBC files imported in DaVinci (gateway-only output: "
                                      "the .vsde file)") or vsde.signal_problem(sr) or
               ("" if node in ss.receivers else
                f"{node} does not receive signal {ss.name} in the DBC (the DBC converter would skip it)"))
        if why:
            sr.enabled, sr.reason = False, why
        if ss.length != ds.length:
            sr.notes.append(f"length {ss.length} -> {ds.length} bit")
            self.warn(f"Routing table {row.label}: {ss.name} has {ss.length} bit, {ds.name} {ds.length} bit.")
        if ss.multiplexed or ds.multiplexed:
            sr.notes.append("multiplexed signal")
        over = self.cfg.can_gateway.get(key, {}) if isinstance(self.cfg.can_gateway, dict) else {}
        if "enabled" in over:
            sr.enabled = bool(over["enabled"])
            sr.reason = "" if sr.enabled else (sr.reason or "deselected")
        seen[key] = sr
        plan.signal_routes.append(sr)
        return "route", text, sr

    def _table_conflicts(self):
        """One source per target PDU; a target signal set once; Com sends the targets of signal routes."""
        plan = self.plan
        fed = {}
        for cr in plan.can_routes:
            if not cr.enabled:
                continue
            first = fed.setdefault(id(cr.dst), cr)
            if first is not cr:
                cr.enabled, cr.reason = False, (f"{cr.dst.key} is already fed from {first.src.key} ({first.row}): "
                                                 f"one source per PDU")
        done = {}
        for sr in plan.signal_routes:
            if not sr.enabled:
                continue
            cr = fed.get(id(sr.dst))
            if cr is not None:
                sr.enabled, sr.reason = False, f"{sr.dst.key} is routed as a whole from {cr.src.key} ({cr.row})"
                continue
            first = done.setdefault((id(sr.dst), sr.dst_signal.name), sr)
            if first is not sr:
                sr.enabled, sr.reason = False, f"{sr.dst_signal.name} is already set from {first.src.key} ({first.row})"
        warned = set()
        for sr in plan.enabled_signal_routes:
            t = sr.dst
            over = t.bus.cfg.messages.get(t.message.name, {}) if isinstance(t.bus.cfg.messages, dict) else {}
            if over.get("enabled") and t.enabled:
                if id(t) not in warned:
                    warned.add(id(t))
                    self.warn(f"{t.key}: sent from Ethernet and by Com (signal routing from {sr.src.bus.name}): two "
                              f"sources (N:1).")
            elif t.enabled:
                t.enabled, t.reason, t.change = False, f"Com sends it (signals routed from {sr.src.bus.name})", ""
                if t.prev is not None:
                    self._prev_used.discard(id(t.prev))

    def _plan_vsde_tx(self):
        """ETH -> CAN routes on buses of DBC files imported in DaVinci: the extension file (.vsde) tells the DBC
        converter that the ECU does not send the PDU's signals, so Com does not send it too (one source: Ethernet)."""
        from . import vsde
        plan = self.plan
        for r in plan.routes:
            r.no_com = False
        if not plan.delta or not self.cfg.options.eth_no_com:
            return
        name = os.path.basename(vsde.path_for(self.cfg.output or "gateway.arxml"))
        n = 0
        for r in plan.enabled_routes:
            if r.direction == ETH_TO_CAN and not r.can_problem and not vsde.tx_problem(r):
                r.no_com = True
                r.notes.append(f"Com does not send it ({name})")
                n += 1
        # a bus on which Com no longer sends anything: its Com Tx I-PDU group disappears in DaVinci
        fed = {id(cr.dst) for cr in plan.enabled_can_routes if cr.vsde}
        for bp in plan.buses:
            tx = [r for r in plan.routes if r.bus is bp and r.direction == ETH_TO_CAN
                  and not r.message.nm and not r.message.diag]
            if tx and all(r.no_com or id(r) in fed for r in tx):
                self.warn(f"{bp.name}: Com of {plan.ecu_name} no longer sends any PDU on this bus, so DaVinci removes "
                          f"its Com Tx I-PDU group: delete the BswM actions that switch it (BswMPduGroupSwitch, "
                          f"e.g. CC_Enable/DisablePDUGroup_..._o{bp.name}_Tx) or run the BswM auto configuration.")
        if n:
            self.info(f"ETH -> CAN: Com of {plan.ecu_name} does not send the {n} CAN PDU(s) fed from Ethernet "
                      f"({name}, add it to the input files of the DaVinci project next to the DBC files). The DBC "
                      f"converter logs 'ECU ... does not receive source pdu ...' for them: expected.")

    # ------------------------------------------------------------------ PDU collection (SoAd nPdu)
    def _plan_collection(self):
        """CAN -> ETH with PDU collection: which PDUs wait for the collection timeout and which are sent at once,
        checks of the buffer, and the estimated Ethernet load (and the CAN bursts when the Ethernet node collects the
        ETH -> CAN PDUs the same way)."""
        plan, col = self.plan, self.cfg.ethernet.collection
        plan.load, plan.burst = [], []
        for r in plan.routes:
            r.eth_send, r.eth_send_why = "", ""
        if not col.enabled:
            return
        if not col.timeout_ms or col.timeout_ms <= 0:
            self.err("PDU collection: enter the collection timeout (ms), e.g. the SoAd main function period.")
            return
        tx = [r for r in plan.enabled_routes if r.direction == CAN_TO_ETH]
        for r in tx:
            over = r.bus.cfg.messages.get(r.message.name, {}) if isinstance(r.bus.cfg.messages, dict) else {}
            choice, cycle = over.get("eth_send"), r.message.cycle_ms
            if choice in ("immediate", "collect"):
                r.eth_send, r.eth_send_why = choice, "chosen"
            elif col.mode == "cycle" and not cycle:
                r.eth_send, r.eth_send_why = "immediate", "event message"
            elif col.mode == "cycle" and cycle <= col.immediate_cycle_ms:
                r.eth_send, r.eth_send_why = "immediate", f"cycle {cycle} ms <= {col.immediate_cycle_ms} ms"
            else:
                r.eth_send, r.eth_send_why = "collect", ""
        if not tx:
            return
        biggest = max(r.length for r in tx) + PDU_HEADER_LEN
        if col.buffer < biggest:
            self.err(f"PDU collection: the buffer ({col.buffer} bytes) is smaller than the largest PDU with its header "
                     f"({biggest} bytes).")
        elif col.buffer > MAX_UDP_PAYLOAD:
            self.warn(f"PDU collection: a buffer of {col.buffer} bytes gives UDP datagrams above {MAX_UDP_PAYLOAD} "
                      f"bytes, which are IP fragmented on a 1500 byte MTU.")
        if all(r.eth_send == "immediate" for r in tx):
            self.info("PDU collection: every CAN -> ETH PDU is sent immediately, nothing is collected.")
        for (d, peer), sp in plan.sides.items():
            if d == CAN_TO_ETH and not sp.local_new:
                self.warn(f"PDU collection: socket {sp.local.rsplit('/', 1)[-1]} is not created by this file: set "
                          f"SoAdSocketnPduUdpTxBufferMin = {col.buffer} and SoAdSocketUdpTriggerTimeout = "
                          f"{_seconds(col.timeout_ms)} of its socket connection group in DaVinci (the trigger mode "
                          f"of every PDU is in the file).")
        self._estimate_load(tx)

    def _estimate_load(self, tx):
        plan, col = self.plan, self.cfg.ethernet.collection
        t = float(col.timeout_ms)
        ovh = ETH_OVERHEAD + (4 if plan.eth_vlan is not None else 0)

        def rate(r):
            return 1000.0 / r.message.cycle_ms

        def wire_bits(payload):
            return (max(64, ovh + payload) + ETH_WIRE_EXTRA) * 8
        for (d, peer), sp in plan.sides.items():
            if d != CAN_TO_ETH:
                continue
            rs = [r for r in tx if peer in r.peers]
            if not rs:
                continue
            cyc = [r for r in rs if r.message.cycle_ms]
            imm = [r for r in cyc if r.eth_send == "immediate"]
            coll = [r for r in cyc if r.eth_send == "collect"]
            pkts1 = sum(rate(r) for r in cyc)
            bits1 = sum(rate(r) * wire_bits(PDU_HEADER_LEN + r.length) for r in cyc)
            # worst window: every collected PDU that can come within the timeout (a 10 ms PDU twice in 15 ms ...)
            worst = sum(math.ceil(t / r.message.cycle_ms) * (PDU_HEADER_LEN + r.length) for r in coll)
            per_window = max(1, math.ceil(worst / col.buffer)) if coll else 0
            pkts2 = sum(rate(r) for r in imm) + min(sum(rate(r) for r in coll), per_window * 1000.0 / t)
            payload = sum(rate(r) * (PDU_HEADER_LEN + r.length) for r in cyc)
            bits2 = payload * 8 + pkts2 * (ovh + ETH_WIRE_EXTRA) * 8
            n_imm = sum(1 for r in rs if r.eth_send == "immediate")
            row = LoadRow(peer, sp.local.rsplit("/", 1)[-1], len(rs), n_imm, len(rs) - n_imm, len(rs) - len(cyc),
                          pkts1, bits1, pkts2, bits2, worst, per_window)
            plan.load.append(row)
            self.info(f"PDU collection to {peer}: {row.text}")
            if per_window > 1:
                self.warn(f"PDU collection to {peer}: up to {worst} bytes per {t:g} ms window do not fit into one "
                          f"datagram of {col.buffer} bytes: {per_window} datagrams per window.")
        # ETH -> CAN: when the Ethernet node collects the same way, a datagram puts its PDUs on CAN at once
        for bp in plan.buses:
            rs = [r for r in plan.enabled_routes if r.direction == ETH_TO_CAN and r.bus is bp]
            if not rs:
                continue
            baud = bp.baudrate or 500000
            data_baud = bp.fd_baudrate or baud
            frames = 0
            busy_us = 0.0
            for r in rs:
                n = math.ceil(t / r.message.cycle_ms) if r.message.cycle_ms else 1
                frames += n
                busy_us += n * can_frame_us(r.length, r.message.extended, r.message.fd, baud, data_baud)
            share = busy_us / (t * 1000.0)
            row = BurstRow(bp.name, len(rs), frames, busy_us, t, share, bp.baudrate is None)
            plan.burst.append(row)
            if share > BURST_WARN:
                self.warn(f"ETH -> CAN on {bp.name}: if the Ethernet node collects its PDUs every {t:g} ms too, up to "
                          f"{frames} frames come at once and keep the bus busy {busy_us / 1000:.1f} ms of {t:g} ms "
                          f"({share:.0%}): check the CanIf transmit buffers and the bus load.")

    # ------------------------------------------------------------------ ETH -> CAN 1:N
    def _plan_eth_fanout(self):
        """A message the node sends on several buses (same CAN id, length and signal layout, from the same Ethernet
        node) comes as one Ethernet PDU (one header id) and is forwarded to every bus: the first bus keeps the PDU,
        the others map it to their CAN PDU (fanout_of). Per message the user can force it although the signal layout
        differs (override "fanout": true, same length needed) or keep an own Ethernet PDU ("fanout": false)."""
        plan = self.plan
        for r in plan.routes:
            r.fanout_of, r.fanout_reason = None, ""

        def choice(r):
            over = r.bus.cfg.messages.get(r.message.name, {}) if isinstance(r.bus.cfg.messages, dict) else {}
            return over.get("fanout")
        groups = collections.OrderedDict()
        for r in plan.enabled_routes:
            if r.direction != ETH_TO_CAN or r.can_problem:
                continue
            over = r.bus.cfg.messages.get(r.message.name, {}) if isinstance(r.bus.cfg.messages, dict) else {}
            # an Ethernet PDU / header id chosen by the user (or by the topology: the sender's) is shared only with
            # the buses that have the same choice
            chosen = (over.get("eth_pdu") or "", str(over.get("header_id") if over.get("header_id") is not None
                                                     else ""), r.key if over.get("fanout") is False else "")
            m = r.message
            groups.setdefault((m.can_id, m.extended, r.length, tuple(r.peers)) + chosen, []).append(r)
        n = 0
        for rs in groups.values():
            if len({r.bus.name for r in rs}) < 2:
                continue
            first = rs[0]
            members = []
            for r in rs[1:]:
                if r.bus is first.bus:
                    continue
                reason = pair_problem(first, r)[0]
                if not reason:
                    members.append(r)
                elif reason.startswith("signal layout differs") and True in (choice(first), choice(r)):
                    members.append(r)               # chosen by the user: the bytes are forwarded unchanged
                    r.notes.append("1:N chosen although the signal layout differs")
                    self.warn(f"{r.key}: gets the Ethernet PDU of {first.key} although the signal layout differs "
                              f"(chosen by the user): the PDU is forwarded unchanged, the layout of {first.bus.name} "
                              f"is put on {r.bus.name}.")
            if not members:
                continue
            for r in members:
                r.fanout_of = first
                r.eth_pdu, r.eth_pt_name, r.eth_signals = first.eth_pdu, first.eth_pt_name, []
                r.notes.append(f"1:N: Ethernet PDU of {first.key}")
            first.notes.append("1:N: forwarded to " + ", ".join(r.bus.name for r in [first] + members))
            n += 1
        # the same CAN id on other buses that keeps an own Ethernet PDU (own header id): tell why
        by_id = collections.OrderedDict()
        for key, rs in groups.items():
            for r in rs:
                if r.fanout_of is None:
                    by_id.setdefault(key[:2], []).append((key, r))
        for owners in by_id.values():
            (key0, first), others = owners[0], owners[1:]
            for key, r in others:
                if r.bus is first.bus:
                    continue
                if key[6] or key0[6]:
                    why = "own Ethernet PDU chosen (1:N off)"
                elif key[2] != key0[2] or pair_problem(first, r)[0]:
                    why = pair_problem(first, r)[0]
                elif key[3] != key0[3]:
                    why = f"Ethernet source differs ({', '.join(first.peers)} / {', '.join(r.peers)})"
                else:
                    why = "own Ethernet PDU / header id chosen"
                r.fanout_reason = f"not 1:N with {first.key}: {why}"
                r.notes.append(r.fanout_reason)
                hint = (" To forward it from the same Ethernet PDU anyway: route table, right click -> 1:N with the "
                        "same CAN id..." if why.startswith("signal layout differs") else "")
                self.warn(f"{r.key}: same CAN id as {first.key} but not one Ethernet PDU ({why}): it gets its own "
                          f"Ethernet PDU and header id.{hint}")
        if n:
            self.info(f"ETH -> CAN 1:N: {n} Ethernet PDU(s) forwarded to several CAN buses (one PDU, one header id).")

    # ------------------------------------------------------------------ buses of other ECUs (over Ethernet)
    def _plan_remote_routes(self):
        """A message this ECU receives on CAN and another ECU sends on its bus goes to that ECU too (besides the
        default peer, which gets every received message); a message this ECU sends on CAN and another ECU receives on
        its bus comes from that ECU. Pairing: same name, name without gateway prefix, same CAN id + length."""
        from types import SimpleNamespace
        plan, opts = self.plan, self.cfg.options
        sends, receives = [], []
        for ecu, db, node in self._remotes:
            rx, tx = db.node_messages(node)
            sends += [(ecu, db, m) for m in tx]
            receives += [(ecu, db, m) for m in rx]

        def index(items):
            by_name, by_norm, by_id = (collections.defaultdict(list) for _ in range(3))
            for ecu, db, m in items:
                by_name[m.name].append((ecu, db, m))
                by_norm[_norm_gateway_name(m.name)].append((ecu, db, m))
                by_id[(m.can_id, m.extended, m.length)].append((ecu, db, m))
            return by_name, by_norm, by_id

        def find(idx, r):
            by_name, by_norm, by_id = idx
            m = r.message
            for hits in (by_name.get(m.name), by_norm.get(_norm_gateway_name(m.name)),
                         by_id.get((m.can_id, m.extended, r.length)) if opts.can_match_id else None):
                if hits:
                    return hits
            return []

        def check(local, ecu, db, rm, local_is_src):
            other = SimpleNamespace(length=rm.length, message=rm, bus=SimpleNamespace(name=f"{ecu}/{db.name}"))
            a, b_ = (local, other) if local_is_src else (other, local)
            return pair_problem(a, b_)

        missing, both = set(), []
        to_ecu, from_ecu = collections.defaultdict(list), collections.defaultdict(list)
        sent_idx, recv_idx = index(sends), index(receives)
        # a route that is off only because it is new in a regeneration or because Com sends the CAN PDU is switched
        # on when another ECU needs / feeds it (that ECU's DBC was added for this); NM, diagnostic, deselected and
        # CAN -> CAN fed routes stay off
        revivable = ("not in the previous gateway file", "sent by Com of")

        def switch_on(r, why):
            r.enabled, r.reason = True, ""
            r.notes.append(why)
            if self.previous is not None:
                r.change = "kept" if r.prev is not None else "new"
                if r.prev is not None:
                    self._prev_used.add(id(r.prev))

        for r in plan.routes:
            if r.can_problem:
                continue
            over = r.bus.cfg.messages.get(r.message.name, {}) if isinstance(r.bus.cfg.messages, dict) else {}
            if not r.enabled and ("enabled" in over or not r.reason.startswith(revivable)):
                continue
            if r.direction == CAN_TO_ETH:
                if "eth_peers" in over:
                    continue                        # chosen by the user
                for ecu, db, rm in find(sent_idx, r):
                    reason, notes = check(r, ecu, db, rm, True)
                    if reason:
                        r.notes.append(f"{ecu} sends it on {db.name} but {reason}: not routed to {ecu}")
                        self.warn(f"{r.key}: {ecu} sends it on {db.name} but {reason}; it is not routed to {ecu}.")
                        continue
                    if ecu not in self._peer_cfg:
                        missing.add(ecu)
                        continue
                    if not r.enabled:
                        switch_on(r, f"needed by {ecu}")
                    if ecu not in r.peers:
                        r.peers.append(ecu)
                        r.notes.append(f"also to {ecu} ({db.name}/{rm.name})" + (f"; {notes[0]}" if notes else ""))
                        to_ecu[ecu].append(r)
                if r.enabled and len(r.peers) > 1 and plan.default_peer in r.peers:
                    both.append(r)
            else:
                if "eth_peer" in over or "eth_peers" in over:
                    continue
                hits = [h for h in find(recv_idx, r) if not check(r, h[0], h[1], h[2], False)[0]]
                ecus = list(dict.fromkeys(h[0] for h in hits))
                if len(ecus) > 1:
                    self.warn(f"{r.key}: received by several ECUs ({', '.join(ecus)}); it keeps coming from "
                              f"{r.peers[0] if r.peers else plan.default_peer} (choose the source in the route).")
                elif ecus:
                    ecu = ecus[0]
                    if ecu not in self._peer_cfg:
                        missing.add(ecu)
                    else:
                        if not r.enabled:
                            if r.reason.startswith("sent by Com of"):
                                self.warn(f"{r.key}: comes from {ecu} over Ethernet but Com of {plan.ecu_name} also "
                                          f"sends it (two sources): remove it from the Com transmit PDUs of the "
                                          f"project or deselect the route.")
                            switch_on(r, f"fed by {ecu}")
                        r.peers = [ecu]
                        r.notes.append(f"from {ecu} ({hits[0][1].name}/{hits[0][2].name})")
                        from_ecu[ecu].append(r)
        for ecu in sorted(missing):
            self.err(f"{ecu}: enter its IP address and ports (Ethernet tab -> Ethernet peers -> Add peer, name {ecu}): "
                     f"messages go to / come from it over Ethernet.")
        nodes = collections.Counter(b.node for b in self.cfg.buses if b.dbc and b.node and not b.remote_ecu)
        me = nodes.most_common(1)[0][0] if nodes else plan.ecu_name
        for ecu, _db, _node in self._remotes:
            if ecu in missing:
                continue
            ta, fr = to_ecu.get(ecu, []), from_ecu.get(ecu, [])
            self.info(f"{me} -> {ecu} (CAN -> ETH): {len(ta)} message(s)" +
                      (f": {', '.join(r.key for r in ta[:8])}{' ...' if len(ta) > 8 else ''}" if ta else "") +
                      f".  {ecu} -> {me} (ETH -> CAN): {len(fr)} message(s)" +
                      (f": {', '.join(r.key for r in fr[:8])}{' ...' if len(fr) > 8 else ''}" if fr else "") + ".")
        if both:
            names = ", ".join(f"{r.key} ({', '.join(p for p in r.peers if p != plan.default_peer)})" for r in both[:12])
            more = f" and {len(both) - 12} more" if len(both) > 12 else ""
            self.warn(f"{len(both)} message(s) are forwarded to {plan.default_peer} and to another zone ECU: "
                      f"{names}{more}.")

    def _check_can_route(self, cr: CanRoute, prev_can: list):
        b, cfg, opts = self.base, self.cfg, self.cfg.options
        src, dst = cr.src, cr.dst
        if cr.match != "same name":
            cr.notes.append(cr.match)
        reason, notes = pair_problem(src, dst)
        cr.notes += notes
        if reason:
            cr.enabled, cr.reason = False, reason
        src_pt, dst_pt = self._can_pt_path(src), self._can_pt_path(dst)
        if src.can_pt and dst.can_pt and (src_pt, dst_pt) not in self._own_vsde:
            for g in b.gateways():
                for m in b.el(g).iter(q("I-PDU-MAPPING")):
                    if b.ref(m, "SOURCE-I-PDU-REF") == src_pt and dst_pt in b.refs(m, "TARGET-I-PDU-REF"):
                        cr.enabled, cr.reason = False, "already routed in the base file"
        prev = next((p for p in prev_can if p.can_pt == src_pt and p.dst_pt == dst_pt), None)
        over = cfg.can_gateway.get(cr.key, {}) if isinstance(cfg.can_gateway, dict) else {}
        if self.previous is not None:
            known = any(p.can_pt.startswith(src.bus.channel + "/") and p.dst_pt.startswith(dst.bus.channel + "/")
                        for p in prev_can)
            if "enabled" not in over and opts.only_previous and known:
                if prev is None and cr.enabled:
                    cr.enabled, cr.reason = False, "not in the previous gateway file"
                elif prev is not None and not cr.enabled and cr.reason != "already routed in the base file":
                    cr.enabled, cr.reason = True, ""
        if "enabled" in over:
            cr.enabled = bool(over["enabled"])
            cr.reason = "" if cr.enabled else (cr.reason or "deselected")
        if cr.enabled and self.previous is not None:
            cr.change = "kept" if prev is not None else "new"
            if prev is not None:
                self._prev_used.add(id(prev))

    def _describe_removed(self, removed):
        """CAN frame and id of removed routes whose CAN side is outside the previous file (project mode)."""
        b = self.base
        todo = {p.can_pt: p for p in removed if p.can_id is None and p.can_pt}
        if not todo:
            return
        for ftp in b.of_type("CAN-FRAME-TRIGGERING"):
            ft = b.el(ftp)
            for pt in b.refs(ft.find(q("PDU-TRIGGERINGS")), "PDU-TRIGGERING-REF"):
                p = todo.get(pt)
                if p is not None:
                    frame = (b.ref(ft, "FRAME-REF") or "").rsplit("/", 1)[-1]
                    p.can_frame = re.sub(r"_o[A-Za-z0-9]+$", "", frame) or frame
                    p.can_id = parse_int(arxml.text(ft, "IDENTIFIER"))

    def _com_usage(self, r: Route):
        """The ECU itself may send / receive the CAN PDU through Com (its signals have I-SIGNAL-PORTs)."""
        b, bp = self.base, r.bus
        if bp.new_connector:
            return
        com = b.com_direction(r.can_pt, bp.connector)
        if com == "IN" and r.direction == CAN_TO_ETH:
            r.notes.append("also received by Com (CanIf -> Com + Ethernet, 1:N)")
        elif com == "OUT" and r.direction == ETH_TO_CAN:
            over = bp.cfg.messages.get(r.message.name, {}) if isinstance(bp.cfg.messages, dict) else {}
            from . import vsde
            if over.get("enabled") and self.plan.delta and self.cfg.options.eth_no_com and not vsde.tx_problem(r):
                r.notes.append("Com sends it today: the .vsde file stops that")
            elif over.get("enabled"):
                self.warn(f"{r.key}: the PDU is also sent by Com of {self.plan.ecu_name}; routing it from Ethernet "
                          f"gives the CAN PDU two sources (N:1).")
                r.notes.append("also sent by Com (N:1)")
            elif r.enabled:
                r.enabled, r.reason = False, f"sent by Com of {self.plan.ecu_name} (Ethernet would be a 2nd source)"

    def _already_routed(self, can_pt: str, eth_pdu: str, direction: str) -> bool:
        """True when a gateway of the base file already maps *can_pt* to / from a triggering of *eth_pdu*."""
        b = self.base
        if eth_pdu not in b.by_path:
            return False
        for g in b.gateways():
            for m in b.el(g).iter(q("I-PDU-MAPPING")):
                src, dsts = b.ref(m, "SOURCE-I-PDU-REF"), b.refs(m, "TARGET-I-PDU-REF")
                if direction == CAN_TO_ETH:
                    others = dsts if src == can_pt else []
                else:
                    others = [src] if can_pt in dsts else []
                if any(b.ref(b.el(o), "I-PDU-REF") == eth_pdu for o in others if o and b.el(o) is not None):
                    return True
        return False

    def _signals(self, m: dbcread.Message, sig_pattern, sys_pattern, fields, channel) -> list[SignalSpec]:
        out = []
        sig_pkg, sys_pkg = self._package("I-SIGNAL"), self._package("SYSTEM-SIGNAL")
        for s in m.signals:
            f = dict(fields, sig=s.name)
            name = self._unique(sig_pkg, fmt(sig_pattern, **f), "Signal")
            sysname = self._unique(sys_pkg, fmt(sys_pattern, **f), "System signal")
            st = (s.send_type or "").replace(" ", "").lower()
            out.append(SignalSpec(name=name, system_signal=sysname, mapping=sanitize(s.name), start=s.start,
                                  length=s.length, little_endian=s.little_endian, signed=s.signed,
                                  is_float=s.is_float, initial=s.initial, transfer=_TRANSFER.get(st, "PENDING")))
        return out

    # ------------------------------------------------------------------ sockets
    def _resolve_sides(self):
        plan = self.plan
        need = {(r.direction, p) for r in plan.enabled_routes for p in r.peers}
        default_sides = self._peer_cfg[plan.default_peer]
        one = self.cfg.ethernet.one_socket
        for peer, sides in self._peer_cfg.items():          # default peer first, then in configuration order
            for k, direction in enumerate((CAN_TO_ETH, ETH_TO_CAN)):
                if (direction, peer) not in need:
                    continue
                k = 0 if one else k                     # one socket: ETH -> CAN uses the CAN -> ETH settings
                s = sides[k] if peer == plan.default_peer else _with_local(sides[k], default_sides[k])
                sp = self._side(direction, s, peer)
                if sp:
                    plan.sides[(direction, peer)] = sp

    def _side(self, direction: str, s: SocketSide, peer: str = "") -> SidePlan | None:
        b, plan, ch = self.base, self.plan, self._eth
        tag = "Tx" if direction == CAN_TO_ETH else "Rx"
        one = self.cfg.ethernet.one_socket
        # default socket names: SA_<ECU>_CanGw_Tx / _Rx, or SA_<ECU>_CanGw when both directions share one socket
        local_sfx = "" if one else f"_{tag}"
        remote_sfx = "" if one else ("_Rx" if tag == "Tx" else "_Tx")
        what = "CAN->ETH" if direction == CAN_TO_ETH else "ETH->CAN"
        if one:
            what = "Socket"
        extra = bool(peer) and peer != plan.default_peer
        if extra:
            what += f" ({peer})"
        sockets = {x.path: x for x in ch.sockets}
        # ---- local socket
        local_path, local_new, local_port = "", False, s.local_port
        if s.local_socket:
            local_path = self._find(s.local_socket, "SOCKET-ADDRESS", list(sockets)) or ""
            if not local_path:
                self.err(f"{what}: local socket '{s.local_socket}' is not in {ch.name}.")
                return None
            if not sockets[local_path].connector:
                self.err(f"{what}: socket {sockets[local_path].name} is not owned by an ECU connector.")
                return None
            if sockets[local_path].connector != plan.eth_connector:
                self.warn(f"{what}: socket {sockets[local_path].name} belongs to connector "
                          f"{sockets[local_path].connector.rsplit('/', 1)[-1]}.")
            local_port = sockets[local_path].port
        else:
            name = sanitize(s.local_name or f"SA_{plan.ecu_name}_CanGw{local_sfx}")
            existing = f"{ch.path}/{name}"
            if existing in sockets and sockets[existing].connector:
                local_path, local_port = existing, sockets[existing].port
                self.info(f"{what}: reusing local socket {name}.")
            else:
                if local_port is None:
                    self.err(f"{what}: enter the local {self.cfg.ethernet.protocol} port of {plan.ecu_name}.")
                    return None
                if not plan.local_endpoint:
                    self.err(f"{what}: the connector has no network endpoint on {ch.name}; select the local "
                             f"endpoint or enter the IP address of {plan.ecu_name}.")
                    return None
                key = ("socket", name)
                if key in self._planned:
                    local_path = self._planned[key]
                else:
                    name = self._unique(ch.path, name, "Socket address")
                    local_path = self._planned[key] = f"{ch.path}/{name}"
                local_new = True
        # ---- remote socket / endpoint
        remote_path, remote_new, remote_port = "", False, s.remote_port
        nep_path, nep_new, ip, mask = "", False, (s.remote_ip or "").strip(), s.remote_netmask or "255.255.0.0"
        if s.remote_socket:
            remote_path = self._find(s.remote_socket, "SOCKET-ADDRESS", list(sockets)) or ""
            if not remote_path:
                self.err(f"{what}: remote socket '{s.remote_socket}' is not in {ch.name}.")
                return None
            remote_port, nep_path = sockets[remote_path].port, sockets[remote_path].endpoint or ""
        else:
            if s.remote_endpoint:
                nep_path = self._find(s.remote_endpoint, "NETWORK-ENDPOINT", [e.path for e in ch.endpoints]) or ""
                if not nep_path:
                    self.err(f"{what}: network endpoint '{s.remote_endpoint}' is not in {ch.name}.")
                    return None
            elif ip:
                hits = [e for e in ch.endpoints if (e.ip or "").strip() == ip]
                if plan.local_endpoint_new and ip == plan.ecu_ip:
                    self.err(f"{what}: the remote IP {ip} is the IP address of {plan.ecu_name}; enter the IP "
                             f"address of the other node.")
                    return None
                if hits:
                    nep_path = hits[0].path
                elif ("endpoint", ip) in self._planned:
                    nep_path, nep_new = self._planned[("endpoint", ip)], True
                else:
                    if not _valid_ip(ip):
                        self.err(f"{what}: '{ip}' is not an IPv4 address.")
                        return None
                    nep_name = self._unique(ch.path, sanitize(s.remote_endpoint_name or (
                        f"NEP_{peer}" if extra else "NEP_Remote_" + ip.replace(".", "_"))), "Network endpoint")
                    nep_path, nep_new = f"{ch.path}/{nep_name}", True
                    self._planned[("endpoint", ip)] = nep_path
            else:
                self.err(f"{what}: enter the remote IP address (or choose a remote socket / endpoint).")
                return None
            if remote_port is None:
                self.err(f"{what}: enter the remote {self.cfg.ethernet.protocol} port.")
                return None
            own = {p for c in b.ecu_connectors(plan.ecu)
                   for p in b.refs(b.el(c).find(q("NETWORK-ENDPOINT-REFS")), "NETWORK-ENDPOINT-REF")}
            own.add(plan.local_endpoint)
            if nep_path in own:
                self.warn(f"{what}: the remote endpoint {nep_path.rsplit('/', 1)[-1]} is an address of "
                          f"{plan.ecu_name} itself; enter the IP address of the other node.")
            name = sanitize(s.remote_name or f"SA_{peer if extra else 'Remote'}_CanGw{remote_sfx}")
            existing = f"{ch.path}/{name}"
            if existing in sockets and not sockets[existing].connector:
                remote_path, remote_port = existing, sockets[existing].port
                self.info(f"{what}: reusing remote socket {name}.")
            elif ("socket", name) in self._planned:
                remote_path, remote_new = self._planned[("socket", name)], True
            else:
                name = self._unique(ch.path, name, "Socket address")
                remote_path, remote_new = f"{ch.path}/{name}", True
                self._planned[("socket", name)] = remote_path
        # ---- static socket connection
        conn_path, conn_new = "", True
        if not local_new:
            for c in sockets[local_path].connections:
                if remote_path in c.remotes and (not s.connection_name or c.name == s.connection_name):
                    conn_path, conn_new = c.path, False
                    break
        if conn_new and ("connection", local_path, remote_path) in self._planned:
            conn_path = self._planned[("connection", local_path, remote_path)]
        elif conn_new:
            lname = local_path.rsplit("/", 1)[-1]
            rname = remote_path.rsplit("/", 1)[-1]
            cname = sanitize(s.connection_name or f"{lname}_to_{rname}")
            cname = self._unique(local_path, cname, "Socket connection")
            conn_path = self._planned[("connection", local_path, remote_path)] = f"{local_path}/{cname}"
        if not local_new and not remote_new and conn_new:
            self.info(f"{what}: new socket connection {conn_path.rsplit('/', 1)[-1]}.")
        return SidePlan(direction, local_path, local_new, local_port, remote_path, remote_new, remote_port,
                        nep_path, nep_new, ip, mask, conn_path, conn_new, peer or plan.default_peer)

    def _resolve_id_set(self):
        b, plan, value = self.base, self.plan, self.cfg.ethernet.id_set
        sets = b.id_sets()
        if value:
            hit = self._find(value, "SOCKET-CONNECTION-IPDU-IDENTIFIER-SET", sets)
            if hit:
                plan.id_set = hit
                return
            pkg = self._package("SOCKET-CONNECTION-IPDU-IDENTIFIER-SET")
            plan.id_set = f"{pkg}/{self._unique(pkg, sanitize(value), 'Identifier set')}"
            plan.id_set_new = True
            return
        # auto: the set that holds most identifiers already used by the chosen socket connections
        cnt = collections.Counter()
        for sp in plan.sides.values():
            if not sp.connection_new:
                conn = b.el(sp.connection)
                for p in b.refs(conn, "SO-CON-I-PDU-IDENTIFIER-REF"):
                    cnt[p.rsplit("/", 1)[0]] += 1
        if cnt:
            plan.id_set = cnt.most_common(1)[0][0]
            return
        pkg = self._package("SOCKET-CONNECTION-IPDU-IDENTIFIER-SET")
        plan.id_set = f"{pkg}/{self._unique(pkg, 'CanEthGateway_Ids', 'Identifier set')}"
        plan.id_set_new = True

    # ------------------------------------------------------------------ header ids
    def _scopes(self) -> dict:
        """Header ids already used per socket: key -> {header id: owner (PDU triggering name)}."""
        b = self.base
        ids = b.header_ids()
        used = collections.defaultdict(dict)
        for key, by_id in b.header_id_scopes(self._eth.sockets).items():
            for hid, id_paths in by_id.items():
                h = ids[id_paths[0]]
                used[key][hid] = (h.pdu_triggering or id_paths[0]).rsplit("/", 1)[-1]
        return used

    def _assign_header_ids(self):
        plan, hcfg = self.plan, self.cfg.header
        used = self._scopes()
        own = [r for r in plan.enabled_routes if r.fanout_of is None]
        routes = [r for r in own if r.header_id >= 0] + \
                 [r for r in own if r.header_id < 0]     # fixed ids (user / kept) first
        for r in routes:
            sps = [plan.sides[(r.direction, p)] for p in r.peers if (r.direction, p) in plan.sides]
            if not sps:
                continue
            # one identifier (header id) for every destination: free on each local and remote socket involved
            keys = list(dict.fromkeys(
                [("tx", sp.local) for sp in sps] + [("rx", sp.remote) for sp in sps] if r.direction == CAN_TO_ETH
                else [("rx", sp.local) for sp in sps]))
            base_id = r.message.can_id | (0x80000000 if hcfg.extended_flag and r.message.extended else 0)
            if r.header_id >= 0 and r.locked:
                clash = [used[k][r.header_id] for k in keys if r.header_id in used[k]]
                if clash:
                    self.warn(f"{r.key}: the previous header id {r.header_text} is now used by {clash[0]} on the "
                              f"same socket; a new header id is assigned.")
                    r.header_id, r.locked, r.header_note = -1, False, ""
            if r.header_id >= 0:                    # set by the user / kept: never changed, only checked
                clash = [used[k][r.header_id] for k in keys if r.header_id in used[k]]
                if clash:
                    self.err(f"{r.key}: header id {r.header_text} is already used by {clash[0]} on the same socket.")
            else:
                cand, k = base_id, 0
                shift = hcfg.flag_shift
                max_flag = (0xFFFFFFFF >> shift) if not hcfg.extended_flag else ((0x7FFFFFFF >> shift))
                while any(cand in used[key] for key in keys):
                    k += 1
                    if k > max_flag:
                        cand = None
                        break
                    cand = base_id | (k << shift)
                if cand is None:
                    self.err(f"{r.key}: no free header id for CAN id {r.message.id_text}.")
                    continue
                if k:
                    owner = next(used[key][base_id] for key in keys if base_id in used[key])
                    r.header_note = f"flag {k} added (0x{base_id:08X} used by {owner})"
                    if r.fanout_reason:
                        r.header_note += f"; {r.fanout_reason}"
                    self.warn(f"Header id 0x{base_id:08X} of {r.key} is already used by {owner} on the same "
                              f"socket; using 0x{cand:08X} (flag {k} in bits {hcfg.flag_shift}..31).")
                r.header_id = cand
            for key in keys:
                used[key].setdefault(r.header_id, r.eth_pt_name)
            r.eth_id_name = self._unique(plan.id_set, fmt(self.cfg.naming.header_id, eth_pdu=r.eth_pdu,
                                                          pdu=r.eth_pdu, msg=r.message.name, bus=r.bus.name,
                                                          ecu=plan.ecu_name), "Header id")
        copy_fanout_ids(plan)

    def _resolve_gateway(self):
        b, plan = self.base, self.plan
        g = b.gateway_of(plan.ecu)
        if g:
            plan.gateway = g
        else:
            pkg = self._package("GATEWAY")
            name = self._unique(pkg, fmt(self.cfg.naming.gateway, ecu=plan.ecu_name), "Gateway")
            plan.gateway, plan.gateway_new = f"{pkg}/{name}", True


def copy_fanout_ids(plan: Plan):
    """ETH->CAN 1:N: the other buses use the header id and identifier of the route that owns the Ethernet PDU."""
    for r in plan.routes:
        f = r.fanout_of
        if f is not None:
            r.header_id, r.eth_id_name, r.locked = f.header_id, f.eth_id_name, f.locked
            r.header_note = f"same as {f.key} (1:N)"


PDU_HEADER_LEN = 8                  # SoAd PDU header: id + length
MAX_UDP_PAYLOAD = 1472              # 1500 byte MTU - IPv4 - UDP
ETH_OVERHEAD = 14 + 20 + 8 + 4      # Ethernet header, IPv4, UDP, FCS (+ 4 with a VLAN tag)
ETH_WIRE_EXTRA = 8 + 12             # preamble + inter frame gap
BURST_WARN = 0.5                    # share of the collection window a CAN burst may take


def _seconds(ms: float) -> str:
    """AUTOSAR time value (seconds) of *ms*: 5 -> '0.005'."""
    return f"{ms / 1000.0:.6g}"


def eth_send_label(r: Route, plan: Plan) -> str:
    """'collect <= 5 ms' / 'immediate (event message)' / '' (no PDU collection or not CAN -> ETH)."""
    if r.eth_send == "collect":
        t = plan.cfg.ethernet.collection.timeout_ms if plan.cfg is not None else 0
        return f"collect <= {t:g} ms" + (" (chosen)" if r.eth_send_why == "chosen" else "")
    if r.eth_send == "immediate":
        return "immediate" + (f" ({r.eth_send_why})" if r.eth_send_why else "")
    return ""


def can_frame_us(length: int, extended: bool, fd: bool, baud: int, data_baud: int) -> float:
    """Approximate duration of a CAN frame on the bus in microseconds (with ~20 % bit stuffing)."""
    head = 29 if extended else 11
    if not fd:
        bits = (head + 34 + 8 * length) * 1.2 + 10           # SOF..CRC, ACK, EOF, IFS
        return bits * 1e6 / baud
    arb = (head + 21) * 1.2                                   # SOF, id, control up to BRS, ACK, EOF, IFS
    data = (8 * length + (21 if length <= 16 else 25) + 8) * 1.2
    return arb * 1e6 / baud + data * 1e6 / data_baud


@dataclass
class LoadRow:
    """CAN -> ETH to one Ethernet node: estimated packets and bit rate with one UDP datagram per PDU (1:1) and with
    PDU collection (from the DBC cycle times; event messages are not counted)."""
    peer: str
    socket: str
    pdus: int
    immediate: int
    collected: int
    events: int
    pkts_1to1: float
    bits_1to1: float
    pkts_collected: float
    bits_collected: float
    worst_window: int               # bytes (PDU headers included) that can be collected in one timeout
    per_window: int                 # datagrams needed for that

    @property
    def text(self) -> str:
        return (f"{self.collected} collected, {self.immediate} immediate"
                + (f", {self.events} event (not counted)" if self.events else "")
                + f": 1:1 ~ {self.pkts_1to1:.0f} pkt/s, {self.bits_1to1 / 1e6:.2f} Mbit/s -> collected ~ "
                  f"{self.pkts_collected:.0f} pkt/s, {self.bits_collected / 1e6:.2f} Mbit/s "
                  f"(worst window {self.worst_window} bytes).")


@dataclass
class BurstRow:
    """ETH -> CAN on one bus when the Ethernet node collects its PDUs with the same timeout: frames put on CAN at
    once and how long they keep the bus busy."""
    bus: str
    pdus: int
    frames: int
    busy_us: float
    window_ms: float
    share: float
    baud_assumed: bool              # no baudrate in the DBC: 500 kbit/s assumed


def pair_problem(src: Route, dst: Route, src_label: str = "", dst_label: str = "") -> tuple[str, list[str]]:
    """Can the whole PDU received as *src* be sent unchanged as *dst*? (reason it cannot, notes)"""
    sl, dl = src_label or src.bus.name, dst_label or dst.bus.name
    if src.length != dst.length:
        return f"length differs ({sl} {src.length}, {dl} {dst.length})", []
    layout = lambda m: {(s.start, s.length, s.little_endian) for s in m.signals if not s.multiplexed}
    ls, ld = layout(src.message), layout(dst.message)
    if ls and ld and ls != ld:
        if ld < ls:
            return "", [f"{dl} defines {len(ld)} of the {len(ls)} signals (the whole PDU is forwarded)"]
        return "signal layout differs (a signal gateway would be needed)", []
    if ls and ld and {s.name for s in src.message.signals} != {s.name for s in dst.message.signals}:
        return "", ["same layout, different signal names"]
    return "", []


def _match_network(networks: list[str], names) -> str:
    """The network column named like one of *names* (case and _ - ignored), else the one equal to a single word of
    them; '' when there is none or more than one."""
    key = lambda x: re.sub(r"[^a-z0-9]", "", x.lower())
    cols = {key(n): n for n in networks}
    for name in names:
        if name and key(name) in cols:
            return cols[key(name)]
    words = {key(w) for name in names if name for w in re.split(r"[^A-Za-z0-9]+", name) if w}
    hits = [n for k, n in cols.items() if k in words]
    return hits[0] if len(hits) == 1 else ""


def _with_local(s: SocketSide, default: SocketSide) -> SocketSide:
    """Socket settings of another peer: without own local socket settings it shares the default peer's."""
    if s.local_socket or s.local_name or s.local_port is not None:
        return s
    return dataclasses.replace(s, local_socket=default.local_socket, local_name=default.local_name,
                               local_port=default.local_port)


def _valid_ip(ip: str) -> bool:
    parts = ip.split(".")
    return len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)


def make_plan(cfg: GatewayConfig, base: Base | None = None, dbc_cache: dict | None = None) -> Plan:
    return Planner(cfg, base, dbc_cache).run()
