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
import os
import re
from dataclasses import dataclass, field

from .. import arxml
from ..arxml import local, q
from . import dbcread
from .base import Base, CanChannel
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

    @property
    def can_side_new(self) -> bool:
        return not self.can_ft

    @property
    def header_text(self) -> str:
        return f"0x{self.header_id:08X}"


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
    """Sockets for one direction; *_new* flags tell the writer what to create."""
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


@dataclass
class Plan:
    cfg: GatewayConfig
    ecu: str = ""
    ecu_name: str = ""
    system: str | None = None
    eth_channel: str = ""
    eth_connector: str = ""
    local_endpoint: str = ""
    id_set: str = ""
    id_set_new: bool = False
    gateway: str = ""
    gateway_new: bool = False
    packages: dict = field(default_factory=dict)
    buses: list[BusPlan] = field(default_factory=list)
    sides: dict = field(default_factory=dict)          # direction -> SidePlan
    routes: list[Route] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    infos: list[str] = field(default_factory=list)

    @property
    def enabled_routes(self) -> list[Route]:
        return [r for r in self.routes if r.enabled]

    @property
    def ok(self) -> bool:
        return not self.errors


# default package of each element type when the base file has none of that type yet
DEFAULT_PACKAGES = {
    "CAN-CLUSTER": "/Topology/Clusters",
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
        self.base = base or Base(cfg.base)
        self.dbc_cache = dbc_cache if dbc_cache is not None else {}
        self.plan = Plan(cfg)
        self._taken: set[str] = set()          # planned paths (to keep new names unique)
        self._planned: dict = {}               # new sockets / endpoints / connections shared by both directions

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

    def _unique(self, parent: str, name: str, what: str) -> str:
        free = self.base.free_name(parent, name, self._taken)
        if free != name:
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
        b, cfg, plan = self.base, self.cfg, self.plan
        if not self.base.of_type("ETHERNET-CLUSTER"):
            self.err("The base file has no ETHERNET-CLUSTER. The gateway is merged into an existing "
                     "Ethernet cluster, so the base file must contain one.")
            return plan
        self._resolve_ecu()
        if plan.errors:
            return plan
        self._resolve_ethernet()
        for bus_cfg in cfg.buses:
            try:
                self._plan_bus(bus_cfg)
            except (OSError, RuntimeError, ValueError) as exc:
                self.err(f"{bus_cfg.dbc or '(no DBC)'}: {exc}")
        if not cfg.buses:
            self.err("No DBC file selected.")
        if plan.errors:
            return plan
        self._resolve_sides()
        self._resolve_id_set()
        self._assign_header_ids()
        self._resolve_gateway()
        plan.system = self._find(cfg.system, "SYSTEM") if cfg.system else b.system_for(plan.ecu)
        if cfg.options.add_fibex and plan.system is None:
            self.warn("The base file has no SYSTEM; new elements are not added to FIBEX-ELEMENTS.")
        if not plan.enabled_routes:
            self.warn("No message is selected for routing.")
        return plan

    # ------------------------------------------------------------------ ECU / Ethernet
    def _resolve_ecu(self):
        b, cfg, plan = self.base, self.cfg, self.plan
        ecus = b.ecus()
        if cfg.ecu:
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
        b, eth, plan = self.base, self.cfg.ethernet, self.plan
        channels = b.eth_channels()
        mine = [c for c in channels if any(b.connector_ecu(x) == plan.ecu for x in c.connectors)]
        ch = None
        if eth.channel:
            hits = [c for c in channels if c.path == eth.channel or c.name == eth.channel or
                    (c.vlan is not None and eth.channel.strip().upper() in (f"VLAN{c.vlan}", str(c.vlan)))]
            if len(hits) != 1:
                self.err(f"Ethernet channel '{eth.channel}' not found (or ambiguous) in the base file.")
                return
            ch = hits[0]
        elif len(mine) == 1:
            ch = mine[0]
        else:
            self.err(f"Select the Ethernet channel (VLAN): {plan.ecu_name} is connected to {len(mine)} channels.")
            return
        plan.eth_channel = ch.path
        self._eth = ch
        conns = [c for c in ch.connectors if b.connector_ecu(c) == plan.ecu]
        if eth.connector:
            plan.eth_connector = self._find(eth.connector, "ETHERNET-COMMUNICATION-CONNECTOR", conns) or ""
            if not plan.eth_connector:
                self.err(f"Connector '{eth.connector}' of {plan.ecu_name} is not connected to {ch.name}.")
                return
        elif conns:
            plan.eth_connector = conns[0]
            if len(conns) > 1:
                self.warn(f"{plan.ecu_name} has {len(conns)} connectors on {ch.name}; using "
                          f"{conns[0].rsplit('/', 1)[-1]} (choose another in the Ethernet settings).")
        else:
            self.err(f"{plan.ecu_name} has no Ethernet connector on {ch.name}.")
            return
        if eth.local_endpoint:
            plan.local_endpoint = self._find(eth.local_endpoint, "NETWORK-ENDPOINT",
                                             [e.path for e in ch.endpoints]) or ""
            if not plan.local_endpoint:
                self.err(f"Network endpoint '{eth.local_endpoint}' is not in {ch.name}.")
        else:
            conn_el = b.el(plan.eth_connector)
            neps = [p for p in b.refs(conn_el.find(q("NETWORK-ENDPOINT-REFS")), "NETWORK-ENDPOINT-REF")
                    if p.startswith(ch.path + "/")]
            plan.local_endpoint = neps[0] if neps else ""

    # ------------------------------------------------------------------ buses
    def _plan_bus(self, bc: BusInput):
        b, plan, naming = self.base, self.plan, self.cfg.naming
        if not bc.dbc:
            raise ValueError("no DBC file selected")
        db = self.dbc(bc.dbc)
        if not bc.node:
            raise ValueError("select the gateway node of the DBC")
        if bc.node not in db.nodes and not any(bc.node in m.senders or bc.node in m.receivers for m in db.messages):
            raise ValueError(f"node '{bc.node}' is not in {db.name} (nodes: {', '.join(db.nodes)})")
        can_channels = b.can_channels()
        ch: CanChannel | None = None
        if bc.channel and not bc.new_channel:
            hits = [c for c in can_channels if bc.channel in (c.path, c.name, c.cluster_name)]
            if len(hits) != 1:
                raise ValueError(f"CAN channel '{bc.channel}' not found (or ambiguous) in the base file")
            ch = hits[0]
        busname = sanitize(bc.bus or (ch.name if ch else db.name))
        if ch is None and not bc.new_channel and not bc.channel:
            hits = [c for c in can_channels if busname.lower() in (c.name.lower(), c.cluster_name.lower())]
            if len(hits) == 1:
                ch = hits[0]
                self.info(f"{db.name}: using the existing CAN channel {ch.path} (same name as the bus).")
        fields = dict(bus=busname, ecu=plan.ecu_name, node=bc.node)
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
        rx, tx = db.node_messages(bc.node)
        todo = ([(m, CAN_TO_ETH) for m in rx] if bc.rx else []) + ([(m, ETH_TO_CAN) for m in tx] if bc.tx else [])
        if not todo:
            self.warn(f"{db.name}: node {bc.node} has no {'/'.join(x for x, f in (('RX', bc.rx), ('TX', bc.tx)) if f)} "
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
                r.reason = "deselected"
        fields = dict(bus=bp.name, msg=m.name, ecu=plan.ecu_name, node=bp.cfg.node,
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
        # ---------------------------------------------------------- Ethernet side
        pdu_pkg = self._package("I-SIGNAL-I-PDU")
        eth_name = sanitize(over["eth_pdu"]) if over.get("eth_pdu") else fmt(naming.eth_pdu, **fields)
        r.header_id = -1
        if r.can_pt and self._already_routed(r.can_pt, f"{pdu_pkg}/{eth_name}", direction):
            r.enabled, r.reason = False, f"already routed to {eth_name} in the base file"
            r.eth_pdu = eth_name
            plan.routes.append(r)
            return
        if r.can_pt:
            self._gateway_notes(r)
        r.eth_pdu = self._unique(pdu_pkg, eth_name, "Ethernet PDU")
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
        r.header_id = -1
        if over.get("header_id") not in (None, ""):
            hid = parse_int(over["header_id"])
            if hid is None or not 0 <= hid <= 0xFFFFFFFF:
                self.err(f"{r.key}: header id '{over['header_id']}' is not a 32-bit number.")
            else:
                r.header_id = hid
                r.header_note = "set by user"
        plan.routes.append(r)

    def _existing_can(self, r: Route, ft):
        """Route uses a frame that already exists in the base file."""
        b, plan = self.base, self.plan
        r.can_ft = b.path_of[ft]
        pts = b.refs(ft.find(q("PDU-TRIGGERINGS")), "PDU-TRIGGERING-REF")
        if not pts:
            r.enabled, r.reason = False, "existing frame has no PDU triggering"
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
                r.reason = (f"base file has {plan.ecu_name} {'sending' if have == 'OUT' else 'receiving'} this "
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
        need = {r.direction for r in plan.enabled_routes}
        for direction, side in ((CAN_TO_ETH, self.cfg.ethernet.can_to_eth), (ETH_TO_CAN, self.cfg.ethernet.eth_to_can)):
            if direction in need:
                sp = self._side(direction, side)
                if sp:
                    plan.sides[direction] = sp

    def _side(self, direction: str, s: SocketSide) -> SidePlan | None:
        b, plan, ch = self.base, self.plan, self._eth
        tag = "Tx" if direction == CAN_TO_ETH else "Rx"
        what = "CAN->ETH" if direction == CAN_TO_ETH else "ETH->CAN"
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
            name = sanitize(s.local_name or f"SA_{plan.ecu_name}_CanGw_{tag}")
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
                             f"endpoint (IP address of {plan.ecu_name}).")
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
                if hits:
                    nep_path = hits[0].path
                elif ("endpoint", ip) in self._planned:
                    nep_path, nep_new = self._planned[("endpoint", ip)], True
                else:
                    if not _valid_ip(ip):
                        self.err(f"{what}: '{ip}' is not an IPv4 address.")
                        return None
                    nep_name = self._unique(ch.path, sanitize(s.remote_endpoint_name or
                                                              "NEP_Remote_" + ip.replace(".", "_")),
                                            "Network endpoint")
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
            if nep_path in own:
                self.warn(f"{what}: the remote endpoint {nep_path.rsplit('/', 1)[-1]} is an address of "
                          f"{plan.ecu_name} itself; enter the IP address of the other node.")
            name = sanitize(s.remote_name or f"SA_Remote_CanGw_{'Rx' if tag == 'Tx' else 'Tx'}")
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
                        nep_path, nep_new, ip, mask, conn_path, conn_new)

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
        """Header ids already received per socket: key -> {header id: owner}."""
        b, plan = self.base, self.plan
        used = collections.defaultdict(dict)
        ids = b.header_ids()
        for s in self._eth.sockets:
            if not s.connector:
                continue
            ecu = b.connector_ecu(s.connector)
            for c in s.connections:
                for idp in c.ids:
                    h = ids.get(idp)
                    if h is None or h.header_id is None:
                        continue
                    d = b.pdu_direction(h.pdu_triggering, ecu) if h.pdu_triggering else None
                    owner = (h.pdu_triggering or idp).rsplit("/", 1)[-1]
                    if d in ("IN", None):
                        used[("rx", s.path)].setdefault(h.header_id, owner)
                    if d in ("OUT", None):
                        used[("tx", s.path)].setdefault(h.header_id, owner)
                        for rem in c.remotes:
                            used[("rx", rem)].setdefault(h.header_id, owner)
        return used

    def _assign_header_ids(self):
        plan, hcfg = self.plan, self.cfg.header
        used = self._scopes()
        for r in plan.enabled_routes:
            sp = plan.sides.get(r.direction)
            if sp is None:
                continue
            keys = ([("tx", sp.local), ("rx", sp.remote)] if r.direction == CAN_TO_ETH else [("rx", sp.local)])
            base_id = r.message.can_id | (0x80000000 if hcfg.extended_flag and r.message.extended else 0)
            if r.header_id >= 0:                    # set by the user: never changed, only checked
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
                    self.warn(f"Header id 0x{base_id:08X} of {r.key} is already used by {owner} on the same "
                              f"socket; using 0x{cand:08X} (flag {k} in bits {hcfg.flag_shift}..31).")
                r.header_id = cand
            for key in keys:
                used[key].setdefault(r.header_id, r.eth_pt_name)
            r.eth_id_name = self._unique(plan.id_set, fmt(self.cfg.naming.header_id, eth_pdu=r.eth_pdu,
                                                          pdu=r.eth_pdu, msg=r.message.name, bus=r.bus.name,
                                                          ecu=plan.ecu_name), "Header id")

    def _resolve_gateway(self):
        b, plan = self.base, self.plan
        g = b.gateway_of(plan.ecu)
        if g:
            plan.gateway = g
        else:
            pkg = self._package("GATEWAY")
            name = self._unique(pkg, fmt(self.cfg.naming.gateway, ecu=plan.ecu_name), "Gateway")
            plan.gateway, plan.gateway_new = f"{pkg}/{name}", True


def _valid_ip(ip: str) -> bool:
    parts = ip.split(".")
    return len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)


def make_plan(cfg: GatewayConfig, base: Base | None = None, dbc_cache: dict | None = None) -> Plan:
    return Planner(cfg, base, dbc_cache).run()
