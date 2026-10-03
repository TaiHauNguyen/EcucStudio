"""Inspect an existing system description (e.g. a network.arxml exported by PREEvision).

Nothing here is project specific: ECUs, clusters, channels, connectors, endpoints, sockets, header ids
and the package layout are all discovered from the file itself.
"""
from __future__ import annotations

import collections
import re
from dataclasses import dataclass, field

from .. import arxml
from ..arxml import local, q

PHYSICAL_CHANNELS = ("CAN-PHYSICAL-CHANNEL", "ETHERNET-PHYSICAL-CHANNEL", "LIN-PHYSICAL-CHANNEL",
                     "FLEXRAY-PHYSICAL-CHANNEL")


@dataclass
class Endpoint:
    path: str
    name: str
    ip: str | None
    mask: str | None = None


@dataclass
class SoConnection:
    path: str
    name: str
    remotes: list[str] = field(default_factory=list)      # remote SOCKET-ADDRESS paths
    ids: list[str] = field(default_factory=list)          # SO-CON-I-PDU-IDENTIFIER paths


@dataclass
class Socket:
    path: str
    name: str
    endpoint: str | None
    ip: str | None
    port: int | None
    protocol: str                                      # UDP / TCP
    connector: str | None                              # set for sockets owned by an ECU of this file
    connections: list[SoConnection] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.name}  ({self.ip or '?'}:{self.port if self.port is not None else '?'} {self.protocol})"


@dataclass
class EthChannel:
    path: str
    name: str
    cluster: str
    vlan: int | None
    connectors: list[str] = field(default_factory=list)
    endpoints: list[Endpoint] = field(default_factory=list)
    sockets: list[Socket] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.name}" + (f"  (VLAN {self.vlan})" if self.vlan is not None else "  (untagged)")


@dataclass
class CanChannel:
    path: str
    name: str
    cluster: str
    baudrate: int | None
    fd_baudrate: int | None
    connectors: list[str] = field(default_factory=list)

    @property
    def cluster_name(self) -> str:
        return self.cluster.rsplit("/", 1)[-1]


@dataclass
class HeaderId:
    path: str
    header_id: int | None
    pdu_triggering: str | None


def _int(text):
    try:
        return int(str(text).strip(), 0)
    except (TypeError, ValueError):
        try:
            return int(float(str(text).strip()))
        except (TypeError, ValueError):
            return None


# schemas offered for new files: DaVinci 5.24 reads up to AUTOSAR_00049, DaVinci 5.31 up to AUTOSAR_00053
SCHEMAS = ("AUTOSAR_00046", "AUTOSAR_00047", "AUTOSAR_00048", "AUTOSAR_00049", "AUTOSAR_00050",
           "AUTOSAR_00051", "AUTOSAR_00052", "AUTOSAR_00053")
DEFAULT_SCHEMA = "AUTOSAR_00052"

_NEW_FILE = """<?xml version="1.0" encoding="UTF-8"?>
<AUTOSAR xmlns="http://autosar.org/schema/r4.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xsi:schemaLocation="http://autosar.org/schema/r4.0 {schema}.xsd">
  <AR-PACKAGES>
    <AR-PACKAGE UUID="{u_top}">
      <SHORT-NAME>Topology</SHORT-NAME>
      <AR-PACKAGES>
        <AR-PACKAGE UUID="{u_hw}">
          <SHORT-NAME>HardwareComponents</SHORT-NAME>
          <ELEMENTS>
            <ECU-INSTANCE UUID="{u_ecu}">
              <SHORT-NAME>{ecu}</SHORT-NAME>
              <SLEEP-MODE-SUPPORTED>false</SLEEP-MODE-SUPPORTED>
              <WAKE-UP-OVER-BUS-SUPPORTED>false</WAKE-UP-OVER-BUS-SUPPORTED>
            </ECU-INSTANCE>
          </ELEMENTS>
        </AR-PACKAGE>
      </AR-PACKAGES>
    </AR-PACKAGE>
    <AR-PACKAGE UUID="{u_syspkg}">
      <SHORT-NAME>System</SHORT-NAME>
      <ELEMENTS>
        <SYSTEM UUID="{u_sys}">
          <SHORT-NAME>{system}</SHORT-NAME>
          <CATEGORY>ECU_EXTRACT</CATEGORY>
          <FIBEX-ELEMENTS>
            <FIBEX-ELEMENT-REF-CONDITIONAL>
              <FIBEX-ELEMENT-REF DEST="ECU-INSTANCE">/Topology/HardwareComponents/{ecu}</FIBEX-ELEMENT-REF>
            </FIBEX-ELEMENT-REF-CONDITIONAL>
          </FIBEX-ELEMENTS>
        </SYSTEM>
      </ELEMENTS>
    </AR-PACKAGE>
  </AR-PACKAGES>
</AUTOSAR>
"""


def new_document(path: str, ecu: str, schema: str = DEFAULT_SCHEMA, system: str = "System") -> "Base":
    """A new, nearly empty system description (ECU-INSTANCE + SYSTEM) that the generator fills; it is
    written to *path*. *ecu* and *system* must be valid short names."""
    from .xmlorder import stable_uuid
    if schema not in SCHEMAS:
        raise ValueError(f"Unknown schema '{schema}' (use one of {', '.join(SCHEMAS)})")
    text = _NEW_FILE.format(
        schema=schema, ecu=ecu, system=system, u_top=stable_uuid("/Topology"),
        u_hw=stable_uuid("/Topology/HardwareComponents"), u_ecu=stable_uuid(f"/Topology/HardwareComponents/{ecu}"),
        u_syspkg=stable_uuid("/System"), u_sys=stable_uuid(f"/System/{system}"))
    return Base(xml=arxml.XmlFile(path, text.encode("utf-8")))


class Base:
    """Read-only view of a system description file (plus helpers to keep the index current)."""

    def __init__(self, path: str | None = None, xml: arxml.XmlFile | None = None):
        self.xf = xml or arxml.XmlFile(path)
        self.root = self.xf.root
        self.reindex()

    # ------------------------------------------------------------------ index
    def reindex(self):
        self.by_path: dict[str, object] = {}
        self.path_of: dict[object, str] = {}
        self.by_tag: dict[str, list[str]] = collections.defaultdict(list)
        stack = [(self.root, "")]
        while stack:
            el, path = stack.pop()
            sn = el.find(q("SHORT-NAME"))
            if sn is not None and sn.text:
                path = path + "/" + sn.text.strip()
                self.register(el, path)
            for c in reversed(el):
                if isinstance(c.tag, str) and c.tag != q("SHORT-NAME"):
                    stack.append((c, path))
        self._ft_index = None

    def register(self, el, path: str):
        self.by_path[path] = el
        self.path_of[el] = path
        self.by_tag[local(el)].append(path)

    def register_tree(self, el, parent_path: str):
        """Index *el* (new element below the identifiable *parent_path*) and its descendants."""
        stack = [(el, parent_path)]
        while stack:
            e, path = stack.pop()
            sn = e.find(q("SHORT-NAME"))
            if sn is not None and sn.text:
                path = path + "/" + sn.text.strip()
                self.register(e, path)
            for c in e:
                if isinstance(c.tag, str) and c.tag != q("SHORT-NAME"):
                    stack.append((c, path))
        self._ft_index = None

    def el(self, path: str | None):
        return self.by_path.get(path) if path else None

    def of_type(self, tag: str) -> list[str]:
        return list(self.by_tag.get(tag, []))

    def owner_path(self, el) -> str | None:
        """Path of the nearest identifiable ancestor (or the element itself)."""
        while el is not None:
            if el in self.path_of:
                return self.path_of[el]
            el = el.getparent()
        return None

    def ref(self, el, tag: str) -> str | None:
        r = el.find(".//" + q(tag)) if el is not None else None
        return r.text.strip() if r is not None and r.text else None

    def refs(self, el, tag: str) -> list[str]:
        return [r.text.strip() for r in el.iter(q(tag)) if r.text] if el is not None else []

    # ------------------------------------------------------------------ file facts
    @property
    def schema(self) -> str:
        loc = self.root.get("{http://www.w3.org/2001/XMLSchema-instance}schemaLocation") or ""
        m = re.search(r"(AUTOSAR_[\w.-]+?)\.xsd", loc)
        return m.group(1) if m else ""

    def package_for(self, tag: str) -> str | None:
        """AR-PACKAGE that holds most elements of *tag* (None when the file has none)."""
        cnt = collections.Counter(p.rsplit("/", 1)[0] for p in self.by_tag.get(tag, [])
                                  if local(self.by_path[p].getparent()) == "ELEMENTS")
        return cnt.most_common(1)[0][0] if cnt else None

    def packages(self) -> list[str]:
        return self.of_type("AR-PACKAGE")

    # ------------------------------------------------------------------ topology
    def ecus(self) -> list[str]:
        return self.of_type("ECU-INSTANCE")

    def connector_ecu(self, connector: str) -> str | None:
        return connector.rsplit("/", 1)[0] if connector and connector.rsplit("/", 1)[0] in self.by_path else None

    def ecu_connectors(self, ecu: str, tag: str | None = None) -> list[str]:
        el = self.el(ecu)
        if el is None:
            return []
        conns = el.find(q("CONNECTORS"))
        out = []
        for c in conns if conns is not None else []:
            if isinstance(c.tag, str) and (tag is None or local(c) == tag) and c in self.path_of:
                out.append(self.path_of[c])
        return out

    def ecu_controllers(self, ecu: str, tag: str | None = None) -> list[str]:
        el = self.el(ecu)
        ctrls = el.find(q("COMM-CONTROLLERS")) if el is not None else None
        return [self.path_of[c] for c in (ctrls if ctrls is not None else [])
                if isinstance(c.tag, str) and (tag is None or local(c) == tag) and c in self.path_of]

    def eth_clusters(self) -> list[str]:
        return self.of_type("ETHERNET-CLUSTER")

    def channel_of(self, el):
        while el is not None and local(el) not in PHYSICAL_CHANNELS:
            el = el.getparent()
        return el

    def gateways(self) -> list[str]:
        return self.of_type("GATEWAY")

    def gateway_of(self, ecu: str) -> str | None:
        for g in self.gateways():
            if self.ref(self.by_path[g], "ECU-REF") == ecu:
                return g
        return None

    def systems(self) -> list[str]:
        return self.of_type("SYSTEM")

    def system_for(self, ecu: str | None) -> str | None:
        systems = self.systems()
        if not systems:
            return None
        if ecu:
            for s in systems:
                if ecu in self.refs(self.by_path[s], "FIBEX-ELEMENT-REF"):
                    return s
        for s in systems:
            cat = self.by_path[s].find(q("CATEGORY"))
            if cat is not None and (cat.text or "").strip() == "ECU_EXTRACT":
                return s
        return systems[0]

    def fibex_types(self, system: str) -> set[str]:
        el = self.el(system)
        return {r.get("DEST") for r in el.iter(q("FIBEX-ELEMENT-REF"))} if el is not None else set()

    # ------------------------------------------------------------------ CAN
    def can_channels(self) -> list[CanChannel]:
        out = []
        for p in self.of_type("CAN-PHYSICAL-CHANNEL"):
            ch = self.by_path[p]
            cond = ch.getparent().getparent()          # CAN-CLUSTER-CONDITIONAL
            cluster = self.owner_path(cond)
            out.append(CanChannel(
                path=p, name=p.rsplit("/", 1)[-1], cluster=cluster,
                baudrate=_int(arxml.text(cond, "BAUDRATE")), fd_baudrate=_int(arxml.text(cond, "CAN-FD-BAUDRATE")),
                connectors=self.refs(ch.find(q("COMM-CONNECTORS")), "COMMUNICATION-CONNECTOR-REF")))
        return out

    def frame_triggering(self, channel: str, can_id: int, extended: bool):
        """CAN-FRAME-TRIGGERING of *channel* with this identifier (None when there is none)."""
        if self._ft_index is None:
            self._ft_index = {}
            for p in self.of_type("CAN-FRAME-TRIGGERING"):
                ft = self.by_path[p]
                ch = self.channel_of(ft)
                ident = _int(arxml.text(ft, "IDENTIFIER"))
                mode = (arxml.text(ft, "CAN-ADDRESSING-MODE") or "STANDARD").upper()
                if ch is not None and ident is not None:
                    self._ft_index[(self.path_of[ch], ident, mode == "EXTENDED")] = p
        p = self._ft_index.get((channel, can_id, extended))
        return self.by_path.get(p) if p else None

    def ecu_connector_on(self, ecu: str, channel: str) -> str | None:
        ch = self.el(channel)
        if ch is None:
            return None
        for c in self.refs(ch.find(q("COMM-CONNECTORS")), "COMMUNICATION-CONNECTOR-REF"):
            if self.connector_ecu(c) == ecu:
                return c
        return None

    def port_of(self, triggering, connector: str, ref_tag: str):
        """(port element, direction) of *connector* used by a frame/PDU triggering, or (None, None)."""
        for r in triggering.iter(q(ref_tag)):
            path = (r.text or "").strip()
            if path.rsplit("/", 1)[0] == connector:
                port = self.el(path)
                if port is not None:
                    return port, (arxml.text(port, "COMMUNICATION-DIRECTION") or "").upper()
        return None, None

    # ------------------------------------------------------------------ Ethernet
    def eth_channels(self) -> list[EthChannel]:
        out = []
        for p in self.of_type("ETHERNET-PHYSICAL-CHANNEL"):
            ch = self.by_path[p]
            cond = ch.getparent().getparent()
            vlan = ch.find(q("VLAN"))
            e = EthChannel(path=p, name=p.rsplit("/", 1)[-1], cluster=self.owner_path(cond),
                           vlan=_int(arxml.text(vlan, "VLAN-IDENTIFIER")) if vlan is not None else None,
                           connectors=self.refs(ch.find(q("COMM-CONNECTORS")), "COMMUNICATION-CONNECTOR-REF"))
            for nep in ch.iter(q("NETWORK-ENDPOINT")):
                cfg = nep.find(".//" + q("IPV-4-CONFIGURATION"))
                ip = arxml.text(cfg, "IPV-4-ADDRESS") if cfg is not None else None
                if ip is None:
                    cfg6 = nep.find(".//" + q("IPV-6-CONFIGURATION"))
                    ip = arxml.text(cfg6, "IPV-6-ADDRESS") if cfg6 is not None else None
                e.endpoints.append(Endpoint(self.path_of[nep], arxml.short_name(nep), ip,
                                            arxml.text(cfg, "NETWORK-MASK") if cfg is not None else None))
            for sa in ch.iter(q("SOCKET-ADDRESS")):
                e.sockets.append(self._socket(sa))
            out.append(e)
        return out

    def _socket(self, sa) -> Socket:
        aep = sa.find(q("APPLICATION-ENDPOINT"))
        nep = self.ref(aep, "NETWORK-ENDPOINT-REF") if aep is not None else None
        ip = None
        if nep and self.el(nep) is not None:
            ip = (self.el(nep).findtext(".//" + q("IPV-4-ADDRESS")) or
                  self.el(nep).findtext(".//" + q("IPV-6-ADDRESS")))
        tcp = aep is not None and aep.find(".//" + q("TCP-TP")) is not None
        port = _int(aep.findtext(".//" + q("PORT-NUMBER"))) if aep is not None else None
        conns = []
        for ssc in sa.iter(q("STATIC-SOCKET-CONNECTION")):
            conns.append(SoConnection(self.path_of.get(ssc, ""), arxml.short_name(ssc) or "",
                                      self.refs(ssc.find(q("REMOTE-ADDRESSS")), "SOCKET-ADDRESS-REF"),
                                      self.refs(ssc.find(q("I-PDU-IDENTIFIERS")), "SO-CON-I-PDU-IDENTIFIER-REF")))
        return Socket(self.path_of[sa], arxml.short_name(sa), nep, (ip or "").strip() or None, port,
                      "TCP" if tcp else "UDP", self.ref(sa, "CONNECTOR-REF"), conns)

    def socket(self, path: str) -> Socket | None:
        el = self.el(path)
        return self._socket(el) if el is not None and local(el) == "SOCKET-ADDRESS" else None

    def header_ids(self) -> dict[str, HeaderId]:
        out = {}
        for p in self.of_type("SO-CON-I-PDU-IDENTIFIER"):
            el = self.by_path[p]
            out[p] = HeaderId(p, _int(arxml.text(el, "HEADER-ID")), self.ref(el, "PDU-TRIGGERING-REF"))
        return out

    def id_sets(self) -> list[str]:
        return self.of_type("SOCKET-CONNECTION-IPDU-IDENTIFIER-SET")

    def com_direction(self, pt_path: str, connector: str) -> str | None:
        """IN / OUT when the ECU (*connector*) processes signals of the PDU triggering itself (I-SIGNAL-PORTs,
        i.e. Com sends / receives the PDU), else None."""
        pt = self.el(pt_path)
        if pt is None:
            return None
        for st_path in self.refs(pt.find(q("I-SIGNAL-TRIGGERINGS")), "I-SIGNAL-TRIGGERING-REF"):
            st = self.el(st_path)
            if st is None:
                continue
            for r in st.iter(q("I-SIGNAL-PORT-REF")):
                path = (r.text or "").strip()
                if path.rsplit("/", 1)[0] == connector and self.el(path) is not None:
                    d = (arxml.text(self.el(path), "COMMUNICATION-DIRECTION") or "").upper()
                    if d:
                        return d
        return None

    def header_id_keys(self, socket: "Socket", connection: "SoConnection", pdu_triggering: str | None,
                       ecu: str) -> list[tuple[str, str]]:
        """Scopes in which a header id sent / received over *connection* of the ECU's *socket* must be unique:
        ("rx", socket) for PDUs the ECU receives, ("tx", socket) plus ("rx", remote socket) for PDUs it sends
        (unknown direction: both)."""
        d = self.pdu_direction(pdu_triggering, ecu) if pdu_triggering else None
        keys = []
        if d in ("IN", None):
            keys.append(("rx", socket.path))
        if d in ("OUT", None):
            keys.append(("tx", socket.path))
            keys += [("rx", r) for r in connection.remotes]
        return keys

    def header_id_scopes(self, sockets: list | None = None) -> dict:
        """{scope key: {header id: [SO-CON-I-PDU-IDENTIFIER paths]}} of the sockets owned by an ECU connector
        (all Ethernet channels when *sockets* is None)."""
        ids = self.header_ids()
        if sockets is None:
            sockets = [s for ch in self.eth_channels() for s in ch.sockets]
        used = collections.defaultdict(lambda: collections.defaultdict(list))
        for s in sockets:
            if not s.connector:
                continue
            ecu = self.connector_ecu(s.connector)
            for c in s.connections:
                for idp in c.ids:
                    h = ids.get(idp)
                    if h is None or h.header_id is None:
                        continue
                    for key in self.header_id_keys(s, c, h.pdu_triggering, ecu):
                        if idp not in used[key][h.header_id]:
                            used[key][h.header_id].append(idp)
        return used

    def pdu_direction(self, pt_path: str, ecu: str) -> str | None:
        """IN / OUT of a PDU triggering seen from *ecu* (via its I-PDU-PORTs)."""
        pt = self.el(pt_path)
        if pt is None:
            return None
        for r in pt.iter(q("I-PDU-PORT-REF")):
            path = (r.text or "").strip()
            if path.startswith(ecu + "/"):
                port = self.el(path)
                if port is not None:
                    return (arxml.text(port, "COMMUNICATION-DIRECTION") or "").upper() or None
        return None

    # ------------------------------------------------------------------ data types
    def base_type(self, name: str, size: int, encoding: str) -> str | None:
        """SW-BASE-TYPE with this short name, else one with the same size and encoding."""
        same = None
        for p in self.of_type("SW-BASE-TYPE"):
            el = self.by_path[p]
            if p.rsplit("/", 1)[-1] == name:
                return p
            if same is None and _int(arxml.text(el, "BASE-TYPE-SIZE")) == size and \
                    (arxml.text(el, "BASE-TYPE-ENCODING") or "NONE").upper() == encoding:
                same = p
        return same

    def free_name(self, parent_path: str, name: str, taken: set[str] | None = None) -> str:
        """*name*, or *name*_1, _2 ... when the path already exists."""
        cand, i = name, 0
        while f"{parent_path}/{cand}" in self.by_path or (taken and f"{parent_path}/{cand}" in taken):
            i += 1
            cand = f"{name}_{i}"
        return cand
