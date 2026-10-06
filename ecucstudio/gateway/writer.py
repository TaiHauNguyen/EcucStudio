"""Apply a gateway :class:`~.planner.Plan` to the base system description and write the result.

The output is the base file plus the new elements (written in the same style as the base file, e.g.
PREEvision's network.arxml): untouched parts of the file stay byte-identical.
"""
from __future__ import annotations

import collections
import copy
import os
import shutil

from lxml import etree
from dataclasses import dataclass, field

from .. import arxml
from ..arxml import local, q
from . import xmlorder
from .base import Base
from .planner import CAN_TO_ETH, Plan, Route, SignalSpec, _seconds, choose_package, fmt, load_base
from .regen import owned_items, write_meta
from .xmlorder import E, R, T

# FIBEX-ELEMENTS of the SYSTEM get these new element types (others only if the base file lists the type)
FIBEX_TYPES = {"CAN-CLUSTER", "ETHERNET-CLUSTER", "CAN-FRAME", "I-SIGNAL-I-PDU", "I-SIGNAL", "GATEWAY",
               "SOCKET-CONNECTION-IPDU-IDENTIFIER-SET"}

_BASE_TYPES = {  # name: (size, encoding, native declaration)
    "uint8": (8, "NONE", "unsigned char"), "uint16": (16, "NONE", "unsigned short"),
    "uint32": (32, "NONE", "unsigned long"), "uint64": (64, "NONE", "unsigned long long"),
    "sint8": (8, "2C", "signed char"), "sint16": (16, "2C", "signed short"),
    "sint32": (32, "2C", "signed long"), "sint64": (64, "2C", "signed long long"),
    "float32": (32, "IEEE754", "float"), "float64": (64, "IEEE754", "double"),
}


@dataclass
class Result:
    output: str
    routes: list[Route]
    created: collections.Counter = field(default_factory=collections.Counter)
    warnings: list[str] = field(default_factory=list)
    can_routes: list = field(default_factory=list)      # CanRoute (CAN -> CAN)
    extension: str = ""                                 # .vsde file written next to the output ('' = none)


def base_type_name(spec: SignalSpec) -> str:
    if spec.is_float:
        return "float32" if spec.length <= 32 else "float64"
    size = 8 if spec.length <= 8 else 16 if spec.length <= 16 else 32 if spec.length <= 32 else 64
    return f"{'sint' if spec.signed else 'uint'}{size}"


def _num(v) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v)


class Writer:
    def __init__(self, plan: Plan, base: Base):
        self.p, self.b, self.cfg = plan, base, plan.cfg
        # copy the base file's style: UUIDs on the element types that carry one there; types the base file
        # does not contain get one too when the file uses UUIDs at all
        with_uuid, present = set(), set()
        for e in base.root.iter():
            if isinstance(e.tag, str) and e.find(q("SHORT-NAME")) is not None:
                present.add(local(e))
                if e.get("UUID"):
                    with_uuid.add(local(e))
        self.uuid_tags = with_uuid | ({t for t in xmlorder.IDENTIFIABLE if t not in present} if with_uuid else set())
        self.created = collections.Counter()
        self.added: list = []          # every new element / entry, in creation order (for the delta file)
        self._can_pts: dict = {}       # id(route) -> CAN PDU triggering path (CAN side created once)
        self._eth_pts: dict = {}       # id(route) -> Ethernet PDU triggering path (ETH -> CAN 1:N)
        self.fibex: list[tuple[str, str]] = []
        self.warnings: list[str] = []
        self._sys_signals: set[str] = set()

    # ------------------------------------------------------------------ primitives
    def ident(self, tag: str, path: str, *children):
        """Identifiable element whose AUTOSAR path will be *path*."""
        el = E(tag, T("SHORT-NAME", path.rsplit("/", 1)[-1]), *children)
        if tag in self.uuid_tags:
            el.set("UUID", xmlorder.stable_uuid(path))
        return el

    def attach(self, parent, el, parent_path: str):
        """Insert *el* into *parent* (schema order) and index it."""
        xmlorder.insert(parent, el)
        self.added.append(el)
        self.b.register_tree(el, parent_path)
        if el.find(q("SHORT-NAME")) is not None:
            self.created[local(el)] += 1
        return el

    def lst(self, owner_path: str, *tags):
        """Child list container (e.g. FRAME-TRIGGERINGS) of an identifiable, created on demand."""
        el = self.b.el(owner_path)
        for t in tags:
            el = xmlorder.ensure(el, t)
        return el

    def package(self, path: str):
        """AR-PACKAGE for *path*, created (with its parents) when missing."""
        el = self.b.el(path)
        if el is not None:
            return el
        parent_path, name = path.rsplit("/", 1)
        container = (xmlorder.ensure(self.root_or(parent_path), "AR-PACKAGES"))
        pkg = self.ident("AR-PACKAGE", path)
        self.attach(container, pkg, parent_path)
        return pkg

    def root_or(self, path: str):
        return self.b.root if not path else self.package(path)

    def element(self, pkg_path: str, el):
        """Add a packageable element (its SHORT-NAME is set) to package *pkg_path*."""
        pkg = self.package(pkg_path)
        self.attach(xmlorder.ensure(pkg, "ELEMENTS"), el, pkg_path)
        return el

    def add_fibex(self, dest: str, path: str):
        self.fibex.append((dest, path))

    # ------------------------------------------------------------------ run
    def apply(self) -> None:
        p = self.p
        for bp in p.buses:
            self.bus(bp)
        if p.enabled_routes:
            self.ethernet()
        for sp in p.sides.values():
            self.side(sp)
        if p.enabled_routes:
            self.id_set()
        if p.enabled_routes or any(not cr.vsde for cr in p.enabled_can_routes):
            self.gateway()
        for r in p.enabled_routes:
            if r.fanout_of is None:
                self.route(r)
        for r in p.enabled_routes:
            if r.fanout_of is not None:
                self.fanout(r)
        for cr in p.enabled_can_routes:
            if not cr.vsde:                 # the DBC converter makes the others (extension file)
                self.can_route(cr)
        self.fibex_refs()

    # ------------------------------------------------------------------ CAN bus
    def bus(self, bp):
        p, b = self.p, self.b
        used = any(r.enabled and r.bus is bp for r in p.routes) or \
            any(cr.enabled and bp in (cr.src.bus, cr.dst.bus) for cr in p.can_routes)
        if not used:
            return
        if bp.new_cluster:
            pkg, cname = bp.cluster.rsplit("/", 1)
            channel = self.ident("CAN-PHYSICAL-CHANNEL", bp.channel)
            cond = E("CAN-CLUSTER-CONDITIONAL", T("BAUDRATE", bp.baudrate),
                     E("PHYSICAL-CHANNELS", channel), T("CAN-FD-BAUDRATE", bp.fd_baudrate))
            cluster = self.ident("CAN-CLUSTER", bp.cluster, E("CAN-CLUSTER-VARIANTS", cond))
            self.element(pkg, cluster)
            self.add_fibex("CAN-CLUSTER", bp.cluster)
        if bp.new_connector:
            ctrl = self.ident("CAN-COMMUNICATION-CONTROLLER", bp.controller)
            self.attach(self.lst(p.ecu, "COMM-CONTROLLERS"), ctrl, p.ecu)
            conn = self.ident("CAN-COMMUNICATION-CONNECTOR", bp.connector,
                              R("COMM-CONTROLLER-REF", "CAN-COMMUNICATION-CONTROLLER", bp.controller))
            self.attach(self.lst(p.ecu, "CONNECTORS"), conn, p.ecu)
            self.attach_ref(self.lst(bp.channel, "COMM-CONNECTORS"), E(
                "COMMUNICATION-CONNECTOR-REF-CONDITIONAL",
                R("COMMUNICATION-CONNECTOR-REF", "CAN-COMMUNICATION-CONNECTOR", bp.connector)))

    def port(self, connector: str, tag: str, name: str, direction: str) -> str:
        name = self.b.free_name(connector, name)
        path = f"{connector}/{name}"
        el = self.ident(tag, path, T("COMMUNICATION-DIRECTION", direction))
        self.attach(self.lst(connector, "ECU-COMM-PORT-INSTANCES"), el, connector)
        return path

    # ------------------------------------------------------------------ Ethernet topology
    def ethernet(self):
        """New Ethernet cluster / channel (VLAN) / ECU endpoint, controller and connector, when planned."""
        p, b = self.p, self.b
        ch = p.eth_channel
        vlan = p.eth_vlan
        if p.eth_channel_new:
            vlan_el = self.ident("VLAN", f"{ch}/VLAN{vlan}", T("VLAN-IDENTIFIER", vlan)) if vlan is not None else None
            channel = self.ident("ETHERNET-PHYSICAL-CHANNEL", ch, T("CATEGORY", "WIRED"), vlan_el)
            if p.eth_cluster_new:
                pkg = p.eth_cluster.rsplit("/", 1)[0]
                cluster = self.ident("ETHERNET-CLUSTER", p.eth_cluster, E(
                    "ETHERNET-CLUSTER-VARIANTS", E("ETHERNET-CLUSTER-CONDITIONAL", E("PHYSICAL-CHANNELS", channel))))
                self.element(pkg, cluster)
                self.add_fibex("ETHERNET-CLUSTER", p.eth_cluster)
            else:
                self.attach(self.lst(p.eth_cluster, "ETHERNET-CLUSTER-VARIANTS", "ETHERNET-CLUSTER-CONDITIONAL",
                                     "PHYSICAL-CHANNELS"), channel, p.eth_cluster)
        if p.local_endpoint_new and p.local_endpoint not in b.by_path:
            nep = self.ident("NETWORK-ENDPOINT", p.local_endpoint, E(
                "NETWORK-ENDPOINT-ADDRESSES", E("IPV-4-CONFIGURATION", T("IPV-4-ADDRESS", p.ecu_ip),
                                                T("IPV-4-ADDRESS-SOURCE", "FIXED"),
                                                T("NETWORK-MASK", p.ecu_netmask))))
            self.attach(self.lst(ch, "NETWORK-ENDPOINTS"), nep, ch)
        if p.eth_controller_new:
            name = p.eth_controller.rsplit("/", 1)[-1]
            port = self.ident("COUPLING-PORT", f"{p.eth_controller}/{name}_Port",
                              E("VLAN-MEMBERSHIPS", self.vlan_membership()))
            ctrl = self.ident("ETHERNET-COMMUNICATION-CONTROLLER", p.eth_controller, T("CATEGORY", "WIRED"), E(
                "ETHERNET-COMMUNICATION-CONTROLLER-VARIANTS", E(
                    "ETHERNET-COMMUNICATION-CONTROLLER-CONDITIONAL", E("COUPLING-PORTS", port),
                    T("MAC-UNICAST-ADDRESS", self.cfg.ethernet.mac or None))))
            self.attach(self.lst(p.ecu, "COMM-CONTROLLERS"), ctrl, p.ecu)
        elif p.eth_connector_new:
            # existing controller: make its (first) coupling port a member of the channel
            cport = b.el(p.eth_controller).find(".//" + q("COUPLING-PORT"))
            if cport is not None and ch not in b.refs(cport, "VLAN-REF"):
                self.attach_ref(xmlorder.ensure(cport, "VLAN-MEMBERSHIPS"), self.vlan_membership())
        if p.eth_connector_new:
            conn = self.ident("ETHERNET-COMMUNICATION-CONNECTOR", p.eth_connector, T("CATEGORY", "WIRED"),
                              R("COMM-CONTROLLER-REF", "ETHERNET-COMMUNICATION-CONTROLLER", p.eth_controller),
                              E("NETWORK-ENDPOINT-REFS", R("NETWORK-ENDPOINT-REF", "NETWORK-ENDPOINT",
                                                           p.local_endpoint)) if p.local_endpoint else None)
            self.attach(self.lst(p.ecu, "CONNECTORS"), conn, p.ecu)
            self.attach_ref(self.lst(ch, "COMM-CONNECTORS"), E(
                "COMMUNICATION-CONNECTOR-REF-CONDITIONAL",
                R("COMMUNICATION-CONNECTOR-REF", "ETHERNET-COMMUNICATION-CONNECTOR", p.eth_connector)))
        elif p.local_endpoint_new:
            # existing connector without an endpoint on this channel
            self.attach_ref(self.lst(p.eth_connector, "NETWORK-ENDPOINT-REFS"),
                            R("NETWORK-ENDPOINT-REF", "NETWORK-ENDPOINT", p.local_endpoint))

    def vlan_membership(self):
        tagged = self.p.eth_vlan is not None
        return E("VLAN-MEMBERSHIP", T("DEFAULT-PRIORITY", 0) if tagged else None,
                 T("SEND-ACTIVITY", "SENT-TAGGED" if tagged else "SENT-UNTAGGED"),
                 R("VLAN-REF", "ETHERNET-PHYSICAL-CHANNEL", self.p.eth_channel))

    # ------------------------------------------------------------------ sockets
    def side(self, sp):
        b, p, eth = self.b, self.p, self.cfg.ethernet
        ch = p.eth_channel
        proto = (eth.protocol or "UDP").upper()
        col = eth.collection
        # PDU collection: the local socket CAN -> ETH PDUs are sent from (SoAd nPdu buffer and its timeout)
        collect = col.enabled and any(d == CAN_TO_ETH and s.local == sp.local for (d, _p), s in p.sides.items()) and             any(r.eth_send for r in p.enabled_routes)

        def tp(port):
            if proto == "TCP":
                return E("TP-CONFIGURATION", E("TCP-TP", E("TCP-TP-PORT", T("PORT-NUMBER", port))))
            return E("TP-CONFIGURATION", E("UDP-TP", E("UDP-TP-PORT", T("PORT-NUMBER", port))))

        def aep_name(sock_name):
            return "AEP_" + sock_name[3:] if sock_name.startswith("SA_") else sock_name + "_AEP"

        if sp.remote_endpoint_new and sp.remote_endpoint not in b.by_path:
            nep = self.ident("NETWORK-ENDPOINT", sp.remote_endpoint,
                             E("NETWORK-ENDPOINT-ADDRESSES",
                               E("IPV-4-CONFIGURATION", T("IPV-4-ADDRESS", sp.remote_ip),
                                 T("IPV-4-ADDRESS-SOURCE", "FIXED"), T("NETWORK-MASK", sp.remote_netmask))))
            self.attach(self.lst(ch, "NETWORK-ENDPOINTS"), nep, ch)
        if sp.remote_new and sp.remote not in b.by_path:
            name = sp.remote.rsplit("/", 1)[-1]
            sa = self.ident("SOCKET-ADDRESS", sp.remote,
                            self.ident("APPLICATION-ENDPOINT", f"{sp.remote}/{aep_name(name)}",
                                       R("NETWORK-ENDPOINT-REF", "NETWORK-ENDPOINT", sp.remote_endpoint),
                                       tp(sp.remote_port)))
            self.attach(self.lst(ch, "SO-AD-CONFIG", "SOCKET-ADDRESSS"), sa, ch)
        if sp.local_new and sp.local not in b.by_path:
            name = sp.local.rsplit("/", 1)[-1]
            sa = self.ident("SOCKET-ADDRESS", sp.local,
                            self.ident("APPLICATION-ENDPOINT", f"{sp.local}/{aep_name(name)}",
                                       R("NETWORK-ENDPOINT-REF", "NETWORK-ENDPOINT", p.local_endpoint),
                                       tp(sp.local_port)),
                            R("CONNECTOR-REF", "ETHERNET-COMMUNICATION-CONNECTOR", p.eth_connector),
                            T("PDU-COLLECTION-MAX-BUFFER-SIZE", col.buffer) if collect else None,
                            T("PDU-COLLECTION-TIMEOUT", _seconds(col.timeout_ms)) if collect else None)
            self.attach(self.lst(ch, "SO-AD-CONFIG", "SOCKET-ADDRESSS"), sa, ch)
        if sp.connection_new and sp.connection not in b.by_path:
            conn = self.ident("STATIC-SOCKET-CONNECTION", sp.connection,
                              E("REMOTE-ADDRESSS", E("SOCKET-ADDRESS-REF-CONDITIONAL",
                                                     R("SOCKET-ADDRESS-REF", "SOCKET-ADDRESS", sp.remote))),
                              T("TCP-ROLE", (eth.tcp_role or "CONNECT").upper()) if proto == "TCP" else None)
            self.attach(self.lst(sp.local, "STATIC-SOCKET-CONNECTIONS"), conn, sp.local)

    def id_set(self):
        p = self.p
        if p.id_set_new and p.id_set not in self.b.by_path:
            pkg = p.id_set.rsplit("/", 1)[0]
            self.element(pkg, self.ident("SOCKET-CONNECTION-IPDU-IDENTIFIER-SET", p.id_set))
            self.add_fibex("SOCKET-CONNECTION-IPDU-IDENTIFIER-SET", p.id_set)

    def gateway(self):
        p = self.p
        if p.gateway_new and p.gateway not in self.b.by_path:
            pkg = p.gateway.rsplit("/", 1)[0]
            self.element(pkg, self.ident("GATEWAY", p.gateway, R("ECU-REF", "ECU-INSTANCE", p.ecu)))
            self.add_fibex("GATEWAY", p.gateway)

    # ------------------------------------------------------------------ signals and PDUs
    def base_type(self, spec: SignalSpec) -> str:
        name = base_type_name(spec)
        size, enc, native = _BASE_TYPES[name]
        found = self.b.base_type(name, size, enc)
        if found:
            return found
        pkg = choose_package(self.b, "SW-BASE-TYPE")
        path = f"{pkg}/{name}"
        self.element(pkg, self.ident("SW-BASE-TYPE", path, T("CATEGORY", "FIXED_LENGTH"),
                                     T("BASE-TYPE-SIZE", size), T("BASE-TYPE-ENCODING", enc),
                                     T("MEM-ALIGNMENT", size), T("NATIVE-DECLARATION", native)))
        return path

    def signal(self, spec: SignalSpec) -> str:
        """I-SIGNAL (and its SYSTEM-SIGNAL on first use); returns the I-SIGNAL path."""
        sys_pkg = self.p.packages.get("SYSTEM-SIGNAL") or choose_package(self.b, "SYSTEM-SIGNAL")
        sig_pkg = self.p.packages.get("I-SIGNAL") or choose_package(self.b, "I-SIGNAL")
        sys_path = f"{sys_pkg}/{spec.system_signal}"
        if sys_path not in self.b.by_path:
            self.element(sys_pkg, self.ident("SYSTEM-SIGNAL", sys_path, T("DYNAMIC-LENGTH", "false")))
        path = f"{sig_pkg}/{spec.name}"
        el = self.ident(
            "I-SIGNAL", path, T("DATA-TYPE-POLICY", "OVERRIDE"),
            E("INIT-VALUE", E("NUMERICAL-VALUE-SPECIFICATION", T("VALUE", _num(spec.initial)))),
            T("LENGTH", spec.length),
            E("NETWORK-REPRESENTATION-PROPS", E("SW-DATA-DEF-PROPS-VARIANTS", E(
                "SW-DATA-DEF-PROPS-CONDITIONAL", R("BASE-TYPE-REF", "SW-BASE-TYPE", self.base_type(spec))))),
            R("SYSTEM-SIGNAL-REF", "SYSTEM-SIGNAL", sys_path))
        self.element(sig_pkg, el)
        self.add_fibex("I-SIGNAL", path)
        return path

    @staticmethod
    def timing(cycle_ms: int | None):
        if cycle_ms:
            true_timing = E("TRANSMISSION-MODE-TRUE-TIMING", E("CYCLIC-TIMING", E(
                "TIME-PERIOD", T("VALUE", _num(round(cycle_ms / 1000.0, 6))))))
        else:
            true_timing = E("TRANSMISSION-MODE-TRUE-TIMING", E("EVENT-CONTROLLED-TIMING",
                                                              T("NUMBER-OF-REPETITIONS", 0)))
        return E("I-PDU-TIMING-SPECIFICATIONS", E("I-PDU-TIMING", E("TRANSMISSION-MODE-DECLARATION", true_timing)))

    def ipdu(self, name: str, length: int, cycle_ms: int | None, specs: list[SignalSpec]) -> tuple[str, list[str]]:
        """I-SIGNAL-I-PDU with its signals; returns (pdu path, I-SIGNAL paths)."""
        pkg = self.p.packages.get("I-SIGNAL-I-PDU") or choose_package(self.b, "I-SIGNAL-I-PDU")
        path = f"{pkg}/{name}"
        sigs, maps = [], []
        for s in specs:
            sp = self.signal(s)
            sigs.append(sp)
            maps.append(self.ident("I-SIGNAL-TO-I-PDU-MAPPING", f"{path}/{s.mapping}",
                                   R("I-SIGNAL-REF", "I-SIGNAL", sp),
                                   T("PACKING-BYTE-ORDER", "MOST-SIGNIFICANT-BYTE-LAST" if s.little_endian
                                     else "MOST-SIGNIFICANT-BYTE-FIRST"),
                                   T("START-POSITION", s.start), T("TRANSFER-PROPERTY", s.transfer)))
        el = self.ident("I-SIGNAL-I-PDU", path, T("LENGTH", length), self.timing(cycle_ms),
                        E("I-SIGNAL-TO-PDU-MAPPINGS", *maps) if maps else None)
        self.element(pkg, el)
        self.add_fibex("I-SIGNAL-I-PDU", path)
        return path, sigs

    def copy_signals(self, src_pdu: str, r: Route) -> list[tuple[object, str]]:
        """Copies of the signal mappings of an existing PDU: [(mapping element, new I-SIGNAL path)]."""
        b, naming = self.b, self.cfg.naming
        src = b.el(src_pdu)
        sig_pkg = self.p.packages.get("I-SIGNAL") or choose_package(self.b, "I-SIGNAL")
        out = []
        skipped = 0
        for m in src.iter(q("I-SIGNAL-TO-I-PDU-MAPPING")):
            sref = m.find(q("I-SIGNAL-REF"))
            sig = b.el(sref.text.strip()) if sref is not None and sref.text else None
            if sig is None:
                skipped += 1
                continue
            mname = arxml.short_name(m)
            name = b.free_name(sig_pkg, fmt(naming.eth_signal, sig=mname, eth_pdu=r.eth_pdu, msg=r.message.name,
                                            bus=r.bus.name, ecu=self.p.ecu_name))
            path = f"{sig_pkg}/{name}"
            new_sig = copy.deepcopy(sig)
            _strip_ws(new_sig)
            new_sig.find(q("SHORT-NAME")).text = name
            _restamp(new_sig, path, self.uuid_tags)
            self.element(sig_pkg, new_sig)
            self.add_fibex("I-SIGNAL", path)
            new_map = copy.deepcopy(m)
            _strip_ws(new_map)
            new_map.find(q("I-SIGNAL-REF")).text = path
            out.append((new_map, path))
        if skipped:
            self.warnings.append(f"{r.key}: {skipped} signal group mapping(s) of {src_pdu.rsplit('/', 1)[-1]} "
                                 f"are not copied to the Ethernet PDU.")
        return out

    def triggerings(self, channel: str, pdu_path: str, pt_name: str, sig_paths: list[str], port: str) -> str:
        naming = self.cfg.naming
        st_paths = []
        for sp in sig_paths:
            stname = self.b.free_name(channel, fmt(naming.signal_triggering, signal=sp.rsplit("/", 1)[-1]))
            st = self.ident("I-SIGNAL-TRIGGERING", f"{channel}/{stname}", R("I-SIGNAL-REF", "I-SIGNAL", sp))
            self.attach(self.lst(channel, "I-SIGNAL-TRIGGERINGS"), st, channel)
            st_paths.append(f"{channel}/{stname}")
        pt_path = f"{channel}/{pt_name}"
        pt = self.ident(
            "PDU-TRIGGERING", pt_path, E("I-PDU-PORT-REFS", R("I-PDU-PORT-REF", "I-PDU-PORT", port)),
            R("I-PDU-REF", local(self.b.el(pdu_path)), pdu_path),
            E("I-SIGNAL-TRIGGERINGS", *[E("I-SIGNAL-TRIGGERING-REF-CONDITIONAL",
                                          R("I-SIGNAL-TRIGGERING-REF", "I-SIGNAL-TRIGGERING", x))
                                        for x in st_paths]) if st_paths else None)
        self.attach(self.lst(channel, "PDU-TRIGGERINGS"), pt, channel)
        return pt_path

    # ------------------------------------------------------------------ one route
    def can_side(self, r: Route) -> str:
        """CAN frame / PDU / triggerings / ports of the message of *r* (created once, also when several routes use
        it: CAN -> Ethernet and CAN -> CAN). Returns the CAN PDU triggering path."""
        key = id(r)
        if key in self._can_pts:
            return self._can_pts[key]
        p, naming = self.p, self.cfg.naming
        bp = r.bus
        can_dir = "IN" if r.direction == CAN_TO_ETH else "OUT"
        m = r.message
        if r.can_side_new:
            frame_pkg = self.p.packages.get("CAN-FRAME") or choose_package(self.b, "CAN-FRAME")
            cycle = m.cycle_ms if (r.direction == CAN_TO_ETH or self.cfg.options.can_tx_timing == "dbc") else None
            pdu_path, sigs = self.ipdu(r.can_pdu, r.length, cycle, r.can_signals)
            frame_path = f"{frame_pkg}/{r.can_frame}"
            self.element(frame_pkg, self.ident(
                "CAN-FRAME", frame_path, T("FRAME-LENGTH", r.length),
                E("PDU-TO-FRAME-MAPPINGS", self.ident(
                    "PDU-TO-FRAME-MAPPING", f"{frame_path}/{r.can_pdu}",
                    T("PACKING-BYTE-ORDER", "MOST-SIGNIFICANT-BYTE-LAST"),
                    R("PDU-REF", "I-SIGNAL-I-PDU", pdu_path), T("START-POSITION", 0)))))
            self.add_fibex("CAN-FRAME", frame_path)
            ft_name = r.can_ft_name
            fport = self.port(bp.connector, "FRAME-PORT",
                              fmt(naming.frame_port, triggering=ft_name, frame=r.can_frame, ecu=p.ecu_name,
                                  connector=bp.connector.rsplit("/", 1)[-1]), can_dir)
            pport = self.port(bp.connector, "I-PDU-PORT",
                              fmt(naming.pdu_port, pdu=r.can_pdu, ecu=p.ecu_name,
                                  connector=bp.connector.rsplit("/", 1)[-1]), can_dir)
            can_pt = self.triggerings(bp.channel, pdu_path, r.can_pt_name, sigs, pport)
            fd = m.fd
            ft = self.ident(
                "CAN-FRAME-TRIGGERING", f"{bp.channel}/{ft_name}",
                E("FRAME-PORT-REFS", R("FRAME-PORT-REF", "FRAME-PORT", fport)),
                R("FRAME-REF", "CAN-FRAME", frame_path),
                E("PDU-TRIGGERINGS", E("PDU-TRIGGERING-REF-CONDITIONAL",
                                       R("PDU-TRIGGERING-REF", "PDU-TRIGGERING", can_pt))),
                T("CAN-ADDRESSING-MODE", "EXTENDED" if m.extended else "STANDARD"),
                T("CAN-FRAME-RX-BEHAVIOR", "CAN-FD" if fd else "CAN-20"),
                T("CAN-FRAME-TX-BEHAVIOR", "CAN-FD" if fd else "CAN-20"),
                T("IDENTIFIER", m.can_id))
            self.attach(self.lst(bp.channel, "FRAME-TRIGGERINGS"), ft, bp.channel)
        else:
            can_pt = r.can_pt
            self.ensure_port(r.can_ft, "FRAME-PORT-REFS", "FRAME-PORT-REF", "FRAME-PORT", bp.connector,
                             fmt(naming.frame_port, triggering=r.can_ft.rsplit("/", 1)[-1],
                                 frame=r.can_frame.rsplit("/", 1)[-1], ecu=p.ecu_name,
                                 connector=bp.connector.rsplit("/", 1)[-1]), can_dir)
            self.ensure_port(r.can_pt, "I-PDU-PORT-REFS", "I-PDU-PORT-REF", "I-PDU-PORT", bp.connector,
                             fmt(naming.pdu_port, pdu=(r.can_pdu or r.can_pt).rsplit("/", 1)[-1], ecu=p.ecu_name,
                                 connector=bp.connector.rsplit("/", 1)[-1]), can_dir)
        self._can_pts[key] = can_pt
        return can_pt

    def can_route(self, cr):
        """PDU gateway between two CAN buses: I-PDU-MAPPING from the received to the sent CAN PDU triggering."""
        src, dst = self.can_side(cr.src), self.can_side(cr.dst)
        mapping = E("I-PDU-MAPPING", R("SOURCE-I-PDU-REF", "PDU-TRIGGERING", src),
                    E("TARGET-I-PDU", R("TARGET-I-PDU-REF", "PDU-TRIGGERING", dst)))
        self.attach_ref(self.lst(self.p.gateway, "I-PDU-MAPPINGS"), mapping)
        self.created["I-PDU-MAPPING"] += 1

    def fanout(self, r: Route):
        """ETH->CAN 1:N: the Ethernet PDU of r.fanout_of is also sent on r's bus (one more I-PDU-MAPPING)."""
        can_pt = self.can_side(r)
        mapping = E("I-PDU-MAPPING", R("SOURCE-I-PDU-REF", "PDU-TRIGGERING", self._eth_pts[id(r.fanout_of)]),
                    E("TARGET-I-PDU", R("TARGET-I-PDU-REF", "PDU-TRIGGERING", can_pt)))
        self.attach_ref(self.lst(self.p.gateway, "I-PDU-MAPPINGS"), mapping)
        self.created["I-PDU-MAPPING"] += 1

    def route(self, r: Route):
        p, naming = self.p, self.cfg.naming
        eth_dir = "OUT" if r.direction == CAN_TO_ETH else "IN"
        can_pt = self.can_side(r)
        # ---------------------------------------------------------- Ethernet side
        if r.eth_signals:
            eth_pdu, eth_sigs = self.ipdu(r.eth_pdu, r.length, None, r.eth_signals)
        elif r.copy_signals_from and self.cfg.options.eth_signals == "copy":
            copies = self.copy_signals(r.copy_signals_from, r)
            eth_pdu, _ = self.ipdu(r.eth_pdu, r.length, None, [])
            if copies:
                pdu_el = self.b.el(eth_pdu)
                maps = xmlorder.ensure(pdu_el, "I-SIGNAL-TO-PDU-MAPPINGS")
                for mel, _sp in copies:
                    mpath = f"{eth_pdu}/{arxml.short_name(mel)}"
                    _restamp(mel, mpath, self.uuid_tags)
                    self.attach(maps, mel, eth_pdu)
            eth_sigs = [sp for _m, sp in copies]
        else:
            eth_pdu, eth_sigs = self.ipdu(r.eth_pdu, r.length, None, [])
        eport = self.port(p.eth_connector, "I-PDU-PORT",
                          fmt(naming.pdu_port, pdu=r.eth_pdu, ecu=p.ecu_name,
                              connector=p.eth_connector.rsplit("/", 1)[-1]), eth_dir)
        eth_pt = self.triggerings(p.eth_channel, eth_pdu, r.eth_pt_name, eth_sigs, eport)
        self._eth_pts[id(r)] = eth_pt
        id_path = f"{p.id_set}/{r.eth_id_name}"
        # PDU collection (CAN -> ETH): QUEUED = SoAd copies every instance into the nPdu buffer (no TriggerTransmit)
        col = self.cfg.ethernet.collection
        ident = self.ident("SO-CON-I-PDU-IDENTIFIER", id_path, T("HEADER-ID", r.header_id),
                           T("PDU-COLLECTION-PDU-TIMEOUT", _seconds(col.timeout_ms)) if r.eth_send == "collect"
                           else None,
                           T("PDU-COLLECTION-SEMANTICS", "QUEUED") if r.eth_send else None,
                           T("PDU-COLLECTION-TRIGGER", "NEVER" if r.eth_send == "collect" else "ALWAYS")
                           if r.eth_send else None,
                           R("PDU-TRIGGERING-REF", "PDU-TRIGGERING", eth_pt))
        self.attach(self.lst(p.id_set, "I-PDU-IDENTIFIERS"), ident, p.id_set)
        # one identifier, referenced by the socket connection of every peer (CAN -> ETH to several nodes: 1:N)
        for peer in r.peers:
            sp = p.sides[(r.direction, peer)]
            ids = self.lst(sp.connection, "I-PDU-IDENTIFIERS")
            self.attach_ref(ids, E("SO-CON-I-PDU-IDENTIFIER-REF-CONDITIONAL",
                                   R("SO-CON-I-PDU-IDENTIFIER-REF", "SO-CON-I-PDU-IDENTIFIER", id_path)))
        # ---------------------------------------------------------- gateway
        src, dst = (can_pt, eth_pt) if r.direction == CAN_TO_ETH else (eth_pt, can_pt)
        mapping = E("I-PDU-MAPPING", R("SOURCE-I-PDU-REF", "PDU-TRIGGERING", src),
                    E("TARGET-I-PDU", R("TARGET-I-PDU-REF", "PDU-TRIGGERING", dst)))
        self.attach_ref(self.lst(p.gateway, "I-PDU-MAPPINGS"), mapping)
        self.created["I-PDU-MAPPING"] += 1

    def attach_ref(self, parent, el):
        """Insert a non-identifiable entry (reference wrapper, mapping) into a list container."""
        xmlorder.insert(parent, el)
        self.added.append(el)

    def ensure_port(self, trig_path: str, refs_tag: str, ref_tag: str, port_tag: str, connector: str,
                    name: str, direction: str):
        trig = self.b.el(trig_path)
        port, _have = self.b.port_of(trig, connector, ref_tag)
        if port is not None:
            return
        path = self.port(connector, port_tag, name, direction)
        refs = xmlorder.ensure(trig, refs_tag)
        self.attach_ref(refs, R(ref_tag, port_tag, path))

    # ------------------------------------------------------------------ system
    def fibex_refs(self):
        p = self.p
        # an additional input file of a DaVinci project must not contain a SYSTEM: DaVinci builds the
        # system of the project from all input files ("Duplicate shortname 'System'" otherwise)
        if p.delta or not self.cfg.options.add_fibex or not p.system or not self.fibex:
            return
        sys_el = self.b.el(p.system)
        allowed = FIBEX_TYPES | self.b.fibex_types(p.system)
        have = set(self.b.refs(sys_el, "FIBEX-ELEMENT-REF"))
        lst = xmlorder.ensure(sys_el, "FIBEX-ELEMENTS")
        for dest, path in self.fibex:
            if dest in allowed and path not in have:
                self.attach_ref(lst, E("FIBEX-ELEMENT-REF-CONDITIONAL", R("FIBEX-ELEMENT-REF", dest, path)))
                have.add(path)


def _strip_ws(el):
    """Drop the source indentation of a copied subtree (insert_child re-indents it)."""
    for e in el.iter():
        if isinstance(e.tag, str):
            if e.text is not None and not e.text.strip():
                e.text = None
            e.tail = None


def _restamp(el, path: str, uuid_tags: set[str]):
    if el.get("UUID") is not None or local(el) in uuid_tags:
        el.set("UUID", xmlorder.stable_uuid(path))
    for sub in el.iter():
        if sub is not el and sub.get("UUID") is not None:
            sub.attrib.pop("UUID")


def extract_delta(base: Base, added: list, path: str, meta: tuple | None = None) -> arxml.XmlFile:
    """A file with only the *added* elements of *base*. Their existing ancestors are written as skeletons
    (SHORT-NAME only) so that DaVinci merges the file with the other input files of the project by path."""
    added_set = set(added)
    src_root = base.root
    droot = etree.Element(src_root.tag, dict(src_root.attrib), nsmap=src_root.nsmap)
    mapping = {src_root: droot}

    def place(dparent, child):
        kids = [c for c in dparent if isinstance(c.tag, str)]
        idx = xmlorder.position(local(dparent), local(child), [local(c) for c in kids])
        if idx is None:
            dparent.append(child)
        else:
            kids[idx].addprevious(child)

    def skeleton(el):
        # no UUID: DaVinci rejects a UUID that appears in two files of the input file set
        sk = etree.Element(el.tag)
        sn = el.find(q("SHORT-NAME"))
        if sn is not None:
            etree.SubElement(sk, sn.tag).text = sn.text
        return sk

    for el in added:
        ancestors = list(el.iterancestors())[::-1]          # root first
        if any(a in added_set for a in ancestors):
            continue                                       # copied with its new ancestor
        for anc in ancestors[1:]:
            if anc not in mapping:
                mapping[anc] = skeleton(anc)
                place(mapping[anc.getparent()], mapping[anc])
        dup = copy.deepcopy(el)
        _strip_ws(dup)
        place(mapping[el.getparent()], dup)
    if meta is not None:
        write_meta(droot, *meta)
    for e in droot.iter():
        e.tail = None
        if len(e) and not (e.text or "").strip():
            e.text = None
    etree.indent(droot, space="  ")
    raw = b'<?xml version="1.0" encoding="UTF-8"?>\n' + etree.tostring(droot, encoding="UTF-8") + b"\n"
    return arxml.XmlFile(path, raw)


def generate(plan: Plan, base: Base | None = None, output: str | None = None) -> Result:
    """Apply *plan* to a fresh copy of the base file and write *output* (default: plan.cfg.output)."""
    if not plan.ok:
        raise ValueError("The plan has errors:\n" + "\n".join(plan.errors))
    cfg = plan.cfg
    out = os.path.abspath(output or cfg.output)
    if not out:
        raise ValueError("No output file given.")
    base = base or load_base(cfg)
    w = Writer(plan, base)
    w.apply()
    # the file carries its configuration and the list of what this generation added (to regenerate it later)
    conf = cfg.to_dict(os.path.dirname(out))
    conf["output"] = conf["previous"] = ""
    meta = (conf, owned_items(base, w.added))
    if plan.delta:
        # DaVinci project: only the new elements, as an additional input file next to the DBC files
        doc = extract_delta(base, w.added, out, meta)
        doc.save(backup=os.path.exists(out))
    else:
        write_meta(base.root, *meta)
        base.xf.path = out
        base.xf.save(backup=os.path.exists(out))
    ext = write_extension(plan, out)
    return Result(out, plan.enabled_routes, w.created, plan.warnings + w.warnings, plan.enabled_can_routes, ext)


def write_extension(plan: Plan, out: str) -> str:
    """The .vsde file for the DBC converter ('' = none): CAN -> CAN routes DaVinci makes, CAN PDUs fed from Ethernet
    that Com does not send. An existing one is rewritten also without routes (the project may list it)."""
    from . import vsde
    path = vsde.path_for(out)
    routes = [cr for cr in plan.enabled_can_routes if cr.vsde]
    tx = [r for r in plan.enabled_routes if r.no_com]
    if not routes and not tx and not os.path.isfile(path):
        return ""
    data = vsde.build(routes, tx)
    if os.path.isfile(path):
        with open(path, "rb") as fh:
            if fh.read() == data:
                return path
        shutil.copyfile(path, path + ".bak")
    with open(path, "wb") as fh:
        fh.write(data)
    return path
