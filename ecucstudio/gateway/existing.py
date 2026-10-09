"""Open a system description that already contains a CAN <-> Ethernet gateway and edit it.

The model lists the PDU routes of the GATEWAY elements (CAN frame and SoAd header ids of every route), the
sockets and the network endpoints, and changes them in place:

* header id of a route (checked against the other header ids of the same socket)
* socket connection that carries a header id
* port of a socket, IP address / netmask of a network endpoint
* deleting routes, together with the Ethernet elements that only those routes used (PDU triggering, PDU,
  signals, ports, header id); the CAN side belongs to the bus database and is kept

Untouched parts of the file stay byte-identical.
"""
from __future__ import annotations

import collections
import ipaddress
import os
from dataclasses import dataclass, field

from .. import arxml
from ..arxml import local, q
from . import xmlorder
from .base import ANY, Base, link_owners
from .xmlorder import E, R, T

BUS_KIND = {"CAN-PHYSICAL-CHANNEL": "CAN", "ETHERNET-PHYSICAL-CHANNEL": "ETH", "LIN-PHYSICAL-CHANNEL": "LIN",
            "FLEXRAY-PHYSICAL-CHANNEL": "FR"}

# elements that are deleted with a route when nothing else uses them
GC_TYPES = {"PDU-TRIGGERING", "I-SIGNAL-TRIGGERING", "I-PDU-PORT", "I-SIGNAL-PORT", "I-SIGNAL-I-PDU",
            "SECURED-I-PDU", "I-SIGNAL", "I-SIGNAL-GROUP", "SYSTEM-SIGNAL", "SYSTEM-SIGNAL-GROUP",
            "SO-CON-I-PDU-IDENTIFIER"}
# references that do not keep their target alive (membership lists)
WEAK_REFS = {"FIBEX-ELEMENT-REF"}
WEAK_OWNERS = {"I-SIGNAL-I-PDU-GROUP"}
# list containers removed when they become empty
PRUNE = {"I-PDU-MAPPINGS", "PDU-TRIGGERINGS", "I-SIGNAL-TRIGGERINGS", "FRAME-TRIGGERINGS", "ECU-COMM-PORT-INSTANCES",
         "I-PDU-IDENTIFIERS", "I-PDU-PORT-REFS", "I-SIGNAL-PORT-REFS", "FRAME-PORT-REFS", "FIBEX-ELEMENTS",
         "I-SIGNAL-I-PDUS", "ELEMENTS", "NETWORK-ENDPOINT-REFS"}


class EditError(ValueError):
    """An edit that would make the configuration inconsistent; nothing was changed."""


@dataclass
class HeaderId:
    path: str
    header_id: int | None
    connections: list[str] = field(default_factory=list)   # STATIC-SOCKET-CONNECTION paths

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    @property
    def text(self) -> str:
        return "-" if self.header_id is None else f"0x{self.header_id:08X}"


@dataclass
class RouteEnd:
    pt: str                         # PDU-TRIGGERING path
    kind: str                       # CAN / ETH / LIN / FR; "?" = not in this file
    channel: str = ""
    pdu: str = ""
    pdu_type: str = ""
    length: int | None = None
    frame: str = ""                 # CAN
    can_id: int | None = None
    extended: bool = False
    fd: bool = False
    secured: str = ""               # CAN: Secured-I-PDU that carries this (authentic) PDU
    ids: list[HeaderId] = field(default_factory=list)   # ETH

    @property
    def name(self) -> str:
        return (self.pdu or self.pt).rsplit("/", 1)[-1]

    @property
    def channel_name(self) -> str:
        parts = self.channel.split("/")
        if len(parts) > 2 and parts[-1].upper() in ("CHNL", "CHANNEL", "CH"):
            return parts[-2]                       # Vector converter: /Cluster/<Bus>/CHNL
        return parts[-1] if self.channel else "?"

    @property
    def can_id_text(self) -> str:
        if self.can_id is None:
            return ""
        return f"0x{self.can_id:08X}" if self.extended else f"0x{self.can_id:03X}"


@dataclass
class ExistingRoute:
    gateway: str
    mapping: object                 # I-PDU-MAPPING element
    target: object                  # its TARGET-I-PDU-REF element
    src: RouteEnd
    dst: RouteEnd
    notes: list[str] = field(default_factory=list)

    @property
    def direction(self) -> str:
        return f"{self.src.kind}->{self.dst.kind}"

    @property
    def can(self) -> RouteEnd | None:
        return next((e for e in (self.src, self.dst) if e.kind == "CAN"), None)

    @property
    def eth(self) -> RouteEnd | None:
        return next((e for e in (self.src, self.dst) if e.kind == "ETH"), None)

    @property
    def key(self) -> str:
        return f"{self.src.pt} -> {self.dst.pt}"

    def names(self) -> set[str]:
        out = set()
        for e in (self.src, self.dst):
            out |= {e.pt, e.pt.rsplit("/", 1)[-1], e.name, e.pdu}
            if e.frame:
                out.add(e.frame)
            out |= {h.name for h in e.ids} | {h.path for h in e.ids}
        return {x for x in out if x}


@dataclass
class EndpointInfo:
    path: str
    channel: str
    ip: str | None
    mask: str | None
    owner: str                      # ECU short name, "" = other node

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]


@dataclass
class SocketInfo:
    path: str
    channel: str
    ip: str | None
    port: int | None
    protocol: str
    owner: str                      # ECU short name of the connector, "" = remote socket
    connections: list[str] = field(default_factory=list)
    header_ids: int = 0

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]


def _int(text):
    try:
        return int(str(text).strip(), 0)
    except (TypeError, ValueError):
        return None


def parse_number(value) -> int | None:
    """User input: 0x1A2B, 6699 or 00006699 (leading zeros allowed)."""
    if isinstance(value, int):
        return value
    t = str(value or "").strip().lower().replace("_", "")
    try:
        return int(t, 16) if t.startswith("0x") else int(t, 10)
    except ValueError:
        return None


def _ancestor(el, tag):
    while el is not None and local(el) != tag:
        el = el.getparent()
    return el


class GatewayModel:
    """Gateway configuration of one system description file."""

    def __init__(self, path: str | None = None, base: Base | None = None):
        self.base = base or Base(path)
        self.changes: list[str] = []
        self.refresh()

    # ------------------------------------------------------------------ file
    @property
    def path(self) -> str:
        return self.base.xf.path

    @property
    def dirty(self) -> bool:
        return bool(self.base.xf.dirty)

    def save(self, path: str | None = None, backup: bool = True) -> str:
        xf = self.base.xf
        if path:
            xf.path = os.path.abspath(path)
        xf.save(backup=backup and os.path.exists(xf.path))
        self.changes.append(f"saved {xf.path}")
        return xf.path

    def _changed(self, text: str, structure: bool = False):
        self.base.xf.dirty = True
        self.changes.append(text)
        if structure:
            self.base.reindex()
        self.refresh()

    # ------------------------------------------------------------------ read
    def refresh(self):
        b = self.base
        self.refs_by_target = collections.defaultdict(list)
        for r in b.root.iter():
            if isinstance(r.tag, str) and r.get("DEST") and r.text:
                self.refs_by_target[r.text.strip()].append(r)
        self._ft_by_pt = {}
        for ftp in b.of_type("CAN-FRAME-TRIGGERING"):
            ft = b.el(ftp)
            for pt in b.refs(ft.find(q("PDU-TRIGGERINGS")), "PDU-TRIGGERING-REF"):
                self._ft_by_pt.setdefault(pt, ft)
        self.ids = b.header_ids()
        self.channels = b.eth_channels()
        self.routes = self._routes()

    def _referrers(self, path: str, tag: str, owner: str):
        """Elements of type *owner* that reference *path* with a *tag* reference."""
        out = []
        for r in self.refs_by_target.get(path, []):
            if local(r) == tag:
                o = _ancestor(r, owner)
                if o is not None and o not in out:
                    out.append(o)
        return out

    def connections_of(self, id_path: str) -> list[str]:
        out = []
        for c in self._referrers(id_path, "SO-CON-I-PDU-IDENTIFIER-REF", "STATIC-SOCKET-CONNECTION"):
            p = self.base.path_of.get(c)
            if p:
                out.append(p)
        return out

    def _end(self, pt_path: str) -> RouteEnd:
        b = self.base
        pt = b.el(pt_path)
        if pt is None:
            return RouteEnd(pt=pt_path, kind="?")
        ch = b.channel_of(pt)
        end = RouteEnd(pt=pt_path, kind=BUS_KIND.get(local(ch), "?") if ch is not None else "?",
                       channel=b.path_of.get(ch, "") if ch is not None else "")
        pdu_ref = pt.find(q("I-PDU-REF"))
        end.pdu = (pdu_ref.text or "").strip() if pdu_ref is not None else ""
        pdu = b.el(end.pdu)
        if pdu is not None:
            end.pdu_type = local(pdu)
            end.length = _int(arxml.text(pdu, "LENGTH"))
        if end.kind == "CAN":
            self._can_info(end, pt, ch)
        elif end.kind == "ETH":
            for o in self._referrers(pt_path, "PDU-TRIGGERING-REF", "SO-CON-I-PDU-IDENTIFIER"):
                p = b.path_of.get(o)
                if p:
                    h = self.ids.get(p)
                    end.ids.append(HeaderId(p, h.header_id if h else None, self.connections_of(p)))
        return end

    def _can_info(self, end: RouteEnd, pt, ch):
        b = self.base
        ft = self._ft_by_pt.get(end.pt)
        if ft is None:
            # authentic PDU of SecOC: the frame carries the Secured-I-PDU whose payload is this triggering
            for sp in self._referrers(end.pt, "PAYLOAD-REF", "SECURED-I-PDU"):
                sp_path = b.path_of.get(sp, "")
                for spt in self._referrers(sp_path, "I-PDU-REF", "PDU-TRIGGERING"):
                    if b.channel_of(spt) is ch and b.path_of.get(spt) in self._ft_by_pt:
                        ft = self._ft_by_pt[b.path_of[spt]]
                        end.secured = sp_path.rsplit("/", 1)[-1]
                        break
                if ft is not None:
                    break
        if ft is None:
            return
        end.frame = (b.ref(ft, "FRAME-REF") or "").rsplit("/", 1)[-1]
        end.can_id = _int(arxml.text(ft, "IDENTIFIER"))
        end.extended = (arxml.text(ft, "CAN-ADDRESSING-MODE") or "").upper() == "EXTENDED"
        behavior = (arxml.text(ft, "CAN-FRAME-RX-BEHAVIOR") or "") + (arxml.text(ft, "CAN-FRAME-TX-BEHAVIOR") or "")
        end.fd = "CAN-FD" in behavior or (arxml.text(ft, "CAN-FD-FRAME-SUPPORT") or "").lower() == "true"

    def _routes(self) -> list[ExistingRoute]:
        b = self.base
        routes = []
        by_src, by_dst = collections.Counter(), collections.Counter()
        for g in b.gateways():
            for m in b.el(g).iter(q("I-PDU-MAPPING")):
                src = (m.findtext(q("SOURCE-I-PDU-REF")) or "").strip()
                for t in m.iter(q("TARGET-I-PDU-REF")):
                    dst = (t.text or "").strip()
                    routes.append(ExistingRoute(g, m, t, self._end(src), self._end(dst)))
                    by_src[src] += 1
                    by_dst[dst] += 1
        for r in routes:
            if by_src[r.src.pt] > 1:
                r.notes.append(f"1:{by_src[r.src.pt]} (source routed to {by_src[r.src.pt]} destinations)")
            if by_dst[r.dst.pt] > 1:
                r.notes.append(f"{by_dst[r.dst.pt]}:1 (destination has {by_dst[r.dst.pt]} sources)")
            for e in (r.src, r.dst):
                if e.kind == "?":
                    r.notes.append(f"{e.pt.rsplit('/', 1)[-1]} is not in this file")
                if e.secured:
                    r.notes.append(f"SecOC ({e.secured})")
            eth = r.eth
            if eth is not None and not eth.ids:
                r.notes.append("Ethernet PDU without SoAd header id")
        return routes

    def find_routes(self, name: str) -> list[ExistingRoute]:
        return [r for r in self.routes if name in r.names()]

    def endpoints(self) -> list[EndpointInfo]:
        b = self.base
        owner = {}
        for ecu in b.ecus():
            for c in b.ecu_connectors(ecu):
                for p in b.refs(b.el(c).find(q("NETWORK-ENDPOINT-REFS")), "NETWORK-ENDPOINT-REF"):
                    owner[p] = ecu.rsplit("/", 1)[-1]
        return [EndpointInfo(e.path, ch.path, e.ip, e.mask, owner.get(e.path, ""))
                for ch in self.channels for e in ch.endpoints]

    def sockets(self) -> list[SocketInfo]:
        b = self.base
        out = []
        for ch in self.channels:
            for s in ch.sockets:
                ecu = b.connector_ecu(s.connector) if s.connector else ""
                out.append(SocketInfo(s.path, ch.path, s.ip, s.port, s.protocol,
                                      ecu.rsplit("/", 1)[-1] if ecu else "", [c.path for c in s.connections],
                                      sum(len(c.ids) for c in s.connections)))
        return out

    def connection_choices(self, id_path: str) -> list[str]:
        """Static socket connections that may carry the header id: connections of the ECU sockets on the
        connector where the PDU triggering has its port."""
        b = self.base
        h = self.ids.get(id_path)
        pt = b.el(h.pdu_triggering) if h and h.pdu_triggering else None
        if pt is None:
            return []
        conns = {(r.text or "").strip().rsplit("/", 1)[0] for r in pt.iter(q("I-PDU-PORT-REF"))}
        out = []
        for ch in self.channels:
            for s in ch.sockets:
                if s.connector in conns:
                    out += [c.path for c in s.connections]
        return out

    # ------------------------------------------------------------------ checks
    def check(self) -> list[str]:
        """Inconsistencies of the gateway configuration in the file."""
        b = self.base
        out = []
        sock = lambda p: "any node" if p == ANY else p.rsplit("/", 1)[-1]
        for key, by_id in b.header_id_scopes([s for ch in self.channels for s in ch.sockets]).items():
            for hid, id_paths in by_id.items():
                pts = {self.ids[p].pdu_triggering for p in id_paths}
                if len(pts) > 1:
                    names = ", ".join(sorted(p.rsplit("/", 1)[-1] for p in pts if p))
                    out.append(f"Header id 0x{hid:08X} is used by {len(pts)} PDUs sent from socket {sock(key[0])} "
                               f"to {sock(key[1])}: {names}")
        for p, h in self.ids.items():
            if not self.connections_of(p):
                out.append(f"Header id {p.rsplit('/', 1)[-1]} ({h.header_id if h.header_id is not None else '-'}) "
                           f"is not used by any socket connection")
            if h.pdu_triggering and b.el(h.pdu_triggering) is None:
                out.append(f"Header id {p.rsplit('/', 1)[-1]} references {h.pdu_triggering}, which is not in this file")
        return out

    # ------------------------------------------------------------------ edits
    def id_conflicts(self, id_path: str, value: int, connections: list[str] | None = None) -> list[str]:
        """Other header ids with *value* in the scopes of *id_path* (on *connections*, default: its own)."""
        b = self.base
        h = self.ids.get(id_path)
        if h is None:
            raise EditError(f"{id_path} is not a SO-CON-I-PDU-IDENTIFIER of this file")
        sockets = [s for ch in self.channels for s in ch.sockets]
        scopes = b.header_id_scopes(sockets)
        conns = connections if connections is not None else self.connections_of(id_path)
        out = set()
        for s in sockets:
            if not s.connector:
                continue
            ecu = b.connector_ecu(s.connector)
            for c in s.connections:
                if c.path not in conns:
                    continue
                for key in b.header_id_keys(s, c, h.pdu_triggering, ecu):
                    for others in link_owners(scopes, key, value):
                        out |= {o for o in others
                                if o != id_path and self.ids[o].pdu_triggering != h.pdu_triggering}
        return sorted(out)

    def set_header_id(self, id_path: str, value) -> None:
        value = parse_number(value)
        if value is None or not 0 <= value <= 0xFFFFFFFF:
            raise EditError("A header id is a 32-bit number (e.g. 0x123).")
        clash = self.id_conflicts(id_path, value)
        if clash:
            raise EditError(f"Header id 0x{value:08X} is already used between the same sockets by "
                            + ", ".join(self._pdu_name(c) for c in clash) + ".")
        el = self.base.el(id_path)
        old = self.ids[id_path].header_id
        hel = el.find(q("HEADER-ID"))
        if hel is None:
            xmlorder.insert(el, T("HEADER-ID", value))
        else:
            hel.text = str(value)
        self._changed(f"{self._pdu_name(id_path)}: header id "
                      f"{'-' if old is None else f'0x{old:08X}'} -> 0x{value:08X}")

    def move_header_id(self, id_path: str, from_conn: str, to_conn: str) -> None:
        """Let *to_conn* carry the header id instead of *from_conn*."""
        b = self.base
        if from_conn == to_conn:
            return
        if to_conn not in self.connection_choices(id_path):
            raise EditError(f"{to_conn.rsplit('/', 1)[-1]} is not a socket connection of the connector that "
                            f"sends / receives this PDU.")
        h = self.ids[id_path]
        if h.header_id is not None:
            others = [c for c in self.connections_of(id_path) if c != from_conn] + [to_conn]
            clash = self.id_conflicts(id_path, h.header_id, others)
            if clash:
                raise EditError(f"Header id 0x{h.header_id:08X} is already used on "
                                f"{to_conn.rsplit('/', 1)[-1]} by " + ", ".join(self._pdu_name(c) for c in clash)
                                + ". Change the header id first.")
        src = b.el(from_conn)
        wrapper = None
        for r in (src.iter(q("SO-CON-I-PDU-IDENTIFIER-REF")) if src is not None else []):
            if (r.text or "").strip() == id_path:
                wrapper = r.getparent() if local(r.getparent()).endswith("-CONDITIONAL") else r
                break
        if wrapper is None:
            raise EditError(f"{from_conn.rsplit('/', 1)[-1]} does not carry {id_path.rsplit('/', 1)[-1]}.")
        dst = b.el(to_conn)
        parent = wrapper.getparent()
        arxml.remove_child(wrapper)
        self._prune([parent])
        xmlorder.insert(xmlorder.ensure(dst, "I-PDU-IDENTIFIERS"), E(
            "SO-CON-I-PDU-IDENTIFIER-REF-CONDITIONAL", R("SO-CON-I-PDU-IDENTIFIER-REF", "SO-CON-I-PDU-IDENTIFIER",
                                                       id_path)))
        self._changed(f"{self._pdu_name(id_path)}: socket connection {from_conn.rsplit('/', 1)[-1]} -> "
                      f"{to_conn.rsplit('/', 1)[-1]}")

    def set_port(self, socket_path: str, port) -> None:
        b = self.base
        port = parse_number(port)
        if port is None or not 0 <= port <= 65535:
            raise EditError("A port is a number 0..65535.")
        sa = b.el(socket_path)
        if sa is None or local(sa) != "SOCKET-ADDRESS":
            raise EditError(f"{socket_path} is not a SOCKET-ADDRESS of this file")
        me = b.socket(socket_path)
        for ch in self.channels:
            for s in ch.sockets:
                if s.path != socket_path and s.endpoint and s.endpoint == me.endpoint and \
                        s.protocol == me.protocol and s.port == port:
                    raise EditError(f"{s.name} already uses {me.protocol} port {port} on the same address.")
        aep = sa.find(q("APPLICATION-ENDPOINT"))
        if aep is None:
            raise EditError(f"{me.name} has no APPLICATION-ENDPOINT.")
        pn = aep.find(".//" + q("PORT-NUMBER"))
        if pn is None:
            tp = xmlorder.ensure(aep, "TP-CONFIGURATION")
            proto = tp.find(q("TCP-TP"))
            if proto is not None:
                holder = xmlorder.ensure(proto, "TCP-TP-PORT")
            else:
                holder = xmlorder.ensure(xmlorder.ensure(tp, "UDP-TP"), "UDP-TP-PORT")
            pn = xmlorder.insert(holder, T("PORT-NUMBER", port))
        old = pn.text
        if (old or "").strip() == str(port):
            return
        pn.text = str(port)
        self._changed(f"socket {me.name}: port {old or '-'} -> {port}")

    def set_endpoint(self, nep_path: str, ip: str | None = None, mask: str | None = None) -> None:
        b = self.base
        nep = b.el(nep_path)
        if nep is None or local(nep) != "NETWORK-ENDPOINT":
            raise EditError(f"{nep_path} is not a NETWORK-ENDPOINT of this file")
        cfg = nep.find(".//" + q("IPV-4-CONFIGURATION"))
        if cfg is None:
            raise EditError(f"{nep_path.rsplit('/', 1)[-1]} has no IPv4 configuration.")
        texts = []
        if ip:
            try:
                ipaddress.IPv4Address(ip)
            except ValueError:
                raise EditError(f"'{ip}' is not an IPv4 address.") from None
            ch = next((c for c in self.channels if any(e.path == nep_path for e in c.endpoints)), None)
            for e in (ch.endpoints if ch else []):
                if e.path != nep_path and (e.ip or "").strip() == ip:
                    raise EditError(f"{e.name} on the same channel already has {ip}.")
            el = cfg.find(q("IPV-4-ADDRESS"))
            if el is None:
                el = xmlorder.insert(cfg, T("IPV-4-ADDRESS", ip))
                texts.append(f"IP {ip}")
            elif (el.text or "").strip() != ip:
                texts.append(f"IP {el.text} -> {ip}")
                el.text = ip
        if mask:
            try:
                ipaddress.IPv4Network(f"0.0.0.0/{mask}")
            except ValueError:
                raise EditError(f"'{mask}' is not a netmask.") from None
            el = cfg.find(q("NETWORK-MASK"))
            if el is None:
                xmlorder.insert(cfg, T("NETWORK-MASK", mask))
                texts.append(f"netmask {mask}")
            elif (el.text or "").strip() != mask:
                texts.append(f"netmask {el.text} -> {mask}")
                el.text = mask
        if texts:
            self._changed(f"endpoint {nep_path.rsplit('/', 1)[-1]}: " + ", ".join(texts))

    def delete_routes(self, routes: list[ExistingRoute], cleanup: bool = True) -> collections.Counter:
        """Delete the routes; with *cleanup* also the Ethernet elements only they used. Returns the removed
        element types."""
        dead = set()
        for r in routes:
            targets = list(r.mapping.iter(q("TARGET-I-PDU-REF")))
            if len(targets) <= 1:
                dead.add(r.mapping)
            else:
                dead.add(r.target.getparent() if local(r.target.getparent()) == "TARGET-I-PDU" else r.target)
        if not dead:
            return collections.Counter()
        if cleanup:
            self._collect(dead)
        removed, unresolved = self._remove(dead)
        names = ", ".join(sorted({(r.eth or r.dst).name for r in routes})[:5]) + ("…" if len(routes) > 5 else "")
        summary = ", ".join(f"{k} {v}" for k, v in sorted(removed.items()))
        self._changed(f"deleted {len(routes)} route(s) ({names}); removed {summary}", structure=True)
        for u in unresolved:
            self.changes.append(f"warning: {u}")
        return removed

    # ------------------------------------------------------------------ helpers
    def _pdu_name(self, id_path: str) -> str:
        h = self.ids.get(id_path)
        return (h.pdu_triggering if h and h.pdu_triggering else id_path).rsplit("/", 1)[-1]

    def _collect(self, dead: set):
        """Add the elements that are only used by *dead* elements (types in GC_TYPES)."""
        b = self.base

        def is_dead(el):
            while el is not None:
                if el in dead:
                    return True
                el = el.getparent()
            return False

        def inside(el, anc):
            while el is not None:
                if el is anc:
                    return True
                el = el.getparent()
            return False

        def live_referrers(t):
            out = []
            for r in self.refs_by_target.get(b.path_of.get(t, ""), []):
                if is_dead(r) or inside(r, t) or local(r) in WEAK_REFS:
                    continue
                if any(local(a) in WEAK_OWNERS for a in r.iterancestors()):
                    continue
                if local(r) == "PDU-TRIGGERING-REF" and local(r.getparent()) == "SO-CON-I-PDU-IDENTIFIER":
                    continue        # the header id goes with its PDU triggering
                out.append(r)
            return out

        changed = True
        while changed:
            changed = False
            for idp, h in self.ids.items():
                el, pt = b.el(idp), b.el(h.pdu_triggering)
                if el is not None and pt is not None and not is_dead(el) and is_dead(pt):
                    dead.add(el)
                    changed = True
            cands = []
            for d in list(dead):
                for r in d.iter():
                    if isinstance(r.tag, str) and r.get("DEST") and r.text:
                        t = b.el(r.text.strip())
                        if t is not None and local(t) in GC_TYPES and not is_dead(t) and t not in cands:
                            cands.append(t)
            for t in cands:
                if not live_referrers(t):
                    dead.add(t)
                    changed = True

    def _remove(self, dead: set):
        return remove_elements(self.base, dead)

    @staticmethod
    def _prune(parents):
        prune_lists(parents)


def remove_elements(base: Base, dead) -> tuple[collections.Counter, list[str]]:
    """Remove the elements *dead* (and their subtrees) from *base*, then the references to them that are list
    entries (-CONDITIONAL wrappers, entries of -REFS lists) and the list containers that became empty.
    Returns (removed element types, references that could not be removed)."""
    dead = set(dead)
    tops = [e for e in dead if e.getparent() is not None and not any(a in dead for a in e.iterancestors())]
    deleted = set()
    removed = collections.Counter()
    parents = []
    for e in tops:
        for d in e.iter():
            p = base.path_of.get(d)
            if p:
                deleted.add(p)
        removed[local(e)] += 1
        parents.append(e.getparent())
        arxml.remove_child(e)
    unresolved = []
    for r in list(base.root.iter()):
        if not (isinstance(r.tag, str) and r.get("DEST") and r.text and r.text.strip() in deleted):
            continue
        p = r.getparent()
        if p is None or p.getparent() is None:
            continue
        lp = local(p)
        if lp.endswith("-CONDITIONAL"):
            victim = p
        elif lp.endswith("-REFS"):
            victim = r
        else:
            unresolved.append(f"{lp}/{local(r)} still references the deleted {r.text.strip()}")
            continue
        parents.append(victim.getparent())
        arxml.remove_child(victim)
    prune_lists(parents)
    return removed, unresolved


def prune_lists(parents):
    """Remove list containers (PRUNE) that have no child element any more, walking upwards."""
    for p in parents:
        while p is not None and local(p) in PRUNE and p.find(q("SHORT-NAME")) is None and \
                not [c for c in p if isinstance(c.tag, str)] and p.getparent() is not None:
            gp = p.getparent()
            arxml.remove_child(p)
            p = gp


def route_rows(model: GatewayModel, routes: list[ExistingRoute] | None = None) -> list[tuple]:
    """Table rows: direction, CAN bus, frame, CAN id, Ethernet channel, Ethernet PDU, length, header ids,
    socket connections, remark."""
    rows = []
    for r in routes if routes is not None else model.routes:
        can, eth = r.can, r.eth
        ids = eth.ids if eth else []
        conns = sorted({c.rsplit("/", 1)[-1] for h in ids for c in h.connections})
        rows.append((
            r.direction, can.channel_name if can else "", (can.frame or can.name) if can else r.src.name,
            can.can_id_text if can else "", eth.channel_name if eth else r.dst.channel_name,
            eth.name if eth else r.dst.name, (eth or r.dst).length or "",
            ", ".join(h.text for h in ids), ", ".join(conns),
            "; ".join(r.notes)))
    return rows


ROUTE_COLUMNS = ("Direction", "CAN bus", "CAN frame", "CAN ID", "Ethernet channel", "Ethernet PDU", "Length",
                 "Header ID", "Socket connection", "Remark")
