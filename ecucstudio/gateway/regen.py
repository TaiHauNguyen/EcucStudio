"""Regenerate a gateway file that is already imported in a DaVinci project, keeping what did not change.

A generated gateway file carries its own configuration and the list of elements the generator created, in
ADMIN-DATA/SDGS of the file root (special data, ignored by DaVinci). Opening the file restores the
configuration (older files without it are reconstructed from their content). Generating again with the file as
"previous":

* removes the elements of the previous generation from the base: after the import, the communication
  description of the DaVinci project contains them, and a base that is the previous output contains them too
* keeps the Ethernet PDU name and the SoAd header id of every route that is still wanted, so paths, UUIDs and
  header ids of unchanged routes stay identical and DaVinci keeps their ECUC configuration on the next update
* reports kept, new and removed routes
"""
from __future__ import annotations

import collections
import glob
import json
import os
from dataclasses import dataclass, field

from lxml import etree

from .. import arxml
from ..arxml import local, q
from . import dvproject, xmlorder
from .base import Base
from .config import GatewayConfig, SocketSide
from .existing import GatewayModel, remove_elements
from .xmlorder import E

META_GID = "EcucStudio.CanEthGateway"
META_VERSION = 1


# ---------------------------------------------------------------------------- metadata in the file
def _refs(el) -> list[str]:
    out = [(r.text or "").strip() for r in el.iter() if isinstance(r.tag, str) and r.get("DEST")]
    return sorted(out)


def owned_items(base: Base, added: list) -> dict:
    """What a generation added to *base*: top-level new elements (paths) and new list entries."""
    added_set = set(added)
    elements, entries = [], []
    for el in added:
        if any(a in added_set for a in el.iterancestors()):
            continue
        if el.find(q("SHORT-NAME")) is not None:
            p = base.path_of.get(el)
            if p:
                elements.append(p)
        else:
            entries.append({"parent": base.owner_path(el.getparent()) or "", "tag": local(el), "refs": _refs(el)})
    return {"elements": elements, "entries": entries}


def write_meta(root, config: dict, owned: dict):
    """Store the configuration and the owned items in ADMIN-DATA/SDGS of the file root (replaces old data)."""
    admin = xmlorder.ensure(root, "ADMIN-DATA")
    sdgs = xmlorder.ensure(admin, "SDGS")
    for old in [g for g in sdgs.findall(q("SDG")) if g.get("GID") == META_GID]:
        arxml.remove_child(old)
    sdg = E("SDG", E("SD", text=str(META_VERSION), GID="version"),
            E("SD", text=json.dumps(config, separators=(",", ":")), GID="config"),
            E("SD", text=json.dumps(owned, separators=(",", ":")), GID="owned"), GID=META_GID)
    arxml.insert_child(sdgs, sdg)


def read_meta(root) -> dict | None:
    admin = root.find(q("ADMIN-DATA"))
    if admin is None:
        return None
    for sdg in admin.iter(q("SDG")):
        if sdg.get("GID") == META_GID:
            vals = {sd.get("GID"): sd.text or "" for sd in sdg.findall(q("SD"))}
            try:
                return {"version": int(vals.get("version", "0") or 0),
                        "config": json.loads(vals["config"]) if vals.get("config") else None,
                        "owned": json.loads(vals["owned"]) if vals.get("owned") else None}
            except (ValueError, KeyError):
                return None
    return None


def is_delta(base: Base) -> bool:
    """An additional input file of a DaVinci project: no SYSTEM, existing elements written as skeletons."""
    if base.of_type("SYSTEM"):
        return False
    return any(base.by_path[p].get("UUID") is None for p in base.by_path)


def owned_from_delta(prev: Base) -> dict:
    """Owned items of an additional input file without metadata (older versions): identifiable elements with a
    UUID are definitions, the ones without are skeletons of existing elements; entries below skeletons are
    additions to existing elements."""
    elements, entries = [], []

    def has_identifiable(el):
        return any(isinstance(d.tag, str) and d.tag == q("SHORT-NAME") for d in el.iter())

    def walk(el, owner):
        for c in el:
            if not isinstance(c.tag, str) or c.tag == q("SHORT-NAME") or local(c) == "ADMIN-DATA":
                continue
            if c.find(q("SHORT-NAME")) is not None:
                if c.get("UUID"):
                    elements.append(prev.path_of[c])
                else:
                    walk(c, prev.path_of[c])
            elif has_identifiable(c):
                walk(c, owner)
            elif c.get("DEST") or c.find(".//*[@DEST]") is not None:
                entries.append({"parent": owner, "tag": local(c), "refs": _refs(c)})
    walk(prev.root, "")
    return {"elements": elements, "entries": entries}


def subtract(base: Base, owned: dict) -> collections.Counter:
    """Remove the owned items of a previous generation from *base* (elements by path, entries by content)."""
    dead = []
    for p in owned.get("elements", []):
        el = base.el(p)
        if el is not None:
            dead.append(el)
    for e in owned.get("entries", []):
        parent = base.el(e.get("parent")) if e.get("parent") else base.root
        if parent is None:
            continue
        want = sorted(e.get("refs", []))
        for cand in parent.iter(q(e["tag"])):
            if cand not in dead and _refs(cand) == want:
                dead.append(cand)
                break
    if not dead:
        return collections.Counter()
    removed, _unresolved = remove_elements(base, dead)
    base.reindex()
    return removed


# ---------------------------------------------------------------------------- previous generation
@dataclass
class PrevRoute:
    direction: str          # CAN->ETH / ETH->CAN
    eth_pdu: str            # short name of the Ethernet PDU
    header_id: int | None
    can_pt: str             # CAN-side PDU triggering path (may be outside the file)
    can_frame: str = ""
    can_id: int | None = None
    dst_pt: str = ""        # CAN->CAN: PDU triggering on the destination bus

    @property
    def label(self) -> str:
        hid = "-" if self.header_id is None else f"0x{self.header_id:08X}"
        return f"{self.direction} {self.eth_pdu} (header id {hid})"


@dataclass
class Previous:
    path: str
    meta: dict | None
    delta: bool
    owned: dict | None
    routes: list[PrevRoute] = field(default_factory=list)

    def match(self, can_pt: str, direction: str, eth_pdu: str) -> PrevRoute | None:
        for r in self.routes:
            if r.direction == direction and can_pt and r.can_pt == can_pt:
                return r
        for r in self.routes:
            if r.direction == direction and r.eth_pdu == eth_pdu:
                return r
        return None


def load_previous(path: str) -> Previous:
    path = os.path.abspath(path)
    m = GatewayModel(path)
    meta = read_meta(m.base.root)
    delta = is_delta(m.base)
    owned = meta.get("owned") if meta and meta.get("owned") else (owned_from_delta(m.base) if delta else None)
    prev = Previous(path, meta, delta, owned)
    for r in m.routes:
        eth = r.eth
        if r.src.kind == "CAN" and r.dst.kind == "CAN" or (eth is None and "?" in (r.src.kind, r.dst.kind)):
            # CAN -> CAN (in an additional input file both ends may be outside the file)
            prev.routes.append(PrevRoute("CAN->CAN", "", None, r.src.pt, r.src.frame, r.src.can_id, r.dst.pt))
            continue
        if eth is None or r.src.kind == r.dst.kind:
            continue
        direction = "CAN->ETH" if eth is r.dst else "ETH->CAN"
        other = r.src if eth is r.dst else r.dst
        prev.routes.append(PrevRoute(direction, eth.name, eth.ids[0].header_id if eth.ids else None, other.pt,
                                     other.frame, other.can_id))
    # CAN -> CAN routes DaVinci makes from the extension file of the DBC files (see vsde.py)
    from . import vsde
    have = {(r.can_pt, r.dst_pt) for r in prev.routes if r.direction == "CAN->CAN"}
    for src, dst in sorted(vsde.triggerings(vsde.path_for(path)) - have):
        prev.routes.append(PrevRoute("CAN->CAN", "", None, src, "", None, dst))
    return prev


def base_without_previous(cfg: GatewayConfig, base: Base) -> tuple[Base, collections.Counter, str]:
    """*base* minus the elements of cfg.previous. Returns (base, removed types, problem text or '')."""
    if not cfg.previous or not os.path.isfile(cfg.previous):
        return base, collections.Counter(), ""
    prev = load_previous(cfg.previous)
    if prev.owned is None:
        same = cfg.base and os.path.abspath(cfg.base) == prev.path
        if same or dvproject.is_project(cfg.base):
            return base, collections.Counter(), (
                f"{os.path.basename(prev.path)} was generated by an older version and contains the base: select the "
                f"original network file (without the gateway) as base.")
        return base, collections.Counter(), ""
    return base, subtract(base, prev.owned), ""


# ---------------------------------------------------------------------------- open a generated file
def find_project(path: str) -> str:
    """A DaVinci project (.dpa) near *path* that lists it as an input file ('' when none)."""
    path = os.path.abspath(path)
    d = os.path.dirname(path)
    path = os.path.normcase(path)
    seen = set()
    for _ in range(4):
        for dpa in glob.glob(os.path.join(d, "*.dpa")) + glob.glob(os.path.join(d, "*", "*.dpa")):
            if dpa in seen:
                continue
            seen.add(dpa)
            try:
                proj = dvproject.read(dpa)
            except (OSError, etree.XMLSyntaxError):
                continue
            if any(os.path.normcase(i.path) == path for i in proj.inputs):
                return dpa
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return ""


def config_from_file(path: str) -> tuple[GatewayConfig, list[str]]:
    """Configuration to regenerate the gateway file *path*: the embedded one, else reconstructed."""
    path = os.path.abspath(path)
    m = GatewayModel(path)
    meta = read_meta(m.base.root)
    notes = []
    if meta and meta.get("config"):
        cfg = GatewayConfig.from_dict(meta["config"], os.path.dirname(path))
        cfg.output = cfg.previous = path
        notes.append(f"Configuration restored from {os.path.basename(path)}.")
        if cfg.base and not os.path.exists(cfg.base):
            notes.append(f"The base {cfg.base} does not exist on this computer: select it again.")
        for b in cfg.buses:
            if b.dbc and not os.path.exists(b.dbc):
                notes.append(f"DBC {b.dbc} does not exist on this computer: select it again.")
        return cfg, notes
    return _reconstruct(path, m), notes + [
        f"{os.path.basename(path)} has no embedded configuration (older version): the settings were reconstructed "
        f"from its content. Only its routes are selected; check the base and the CAN buses."]


def _reconstruct(path: str, m: GatewayModel) -> GatewayConfig:
    b = m.base
    delta = is_delta(b)
    owned = set(owned_from_delta(b)["elements"]) if delta else set()

    def is_owned(p):
        return any(p == o or p.startswith(o + "/") for o in owned)

    cfg = GatewayConfig(output=path, previous=path)
    cfg.options.only_previous = True
    gws = b.gateways()
    if gws:
        ecu = b.ref(b.el(gws[0]), "ECU-REF") or ""
        cfg.ecu = ecu.rsplit("/", 1)[-1]
    if delta:
        cfg.base = find_project(path)
    eth_routes = [r for r in m.routes if r.eth is not None and r.src.kind != r.dst.kind]
    can_routes = [r for r in m.routes if r.eth is None and r.src.kind != "ETH" and r.dst.kind != "ETH"]
    cfg.options.eth_routes = bool(eth_routes)
    cfg.options.can_routes = bool(can_routes) or not eth_routes
    # ---- Ethernet channel / connector / endpoint
    chans = collections.Counter(r.eth.channel for r in eth_routes if r.eth.channel)
    e = cfg.ethernet
    if chans:
        ch_path = chans.most_common(1)[0][0]
        if is_owned(ch_path):
            ch = b.el(ch_path)
            e.new_channel = True
            e.channel_name = ch_path.rsplit("/", 1)[-1]
            vid = ch.findtext(".//" + q("VLAN-IDENTIFIER"))
            e.vlan_id = int(vid) if vid and vid.strip().isdigit() else None
            cluster = ch_path.rsplit("/", 1)[0]
            cluster_path = b.owner_path(b.el(ch_path).getparent())
            e.cluster = "" if cluster_path and is_owned(cluster_path) else (cluster_path or cluster)
        else:
            e.channel = ch_path
    ports = [x.text.strip() for r in eth_routes for x in b.el(r.eth.pt).iter(q("I-PDU-PORT-REF"))
             if b.el(r.eth.pt) is not None and x.text]
    if ports:
        conn = collections.Counter(p.rsplit("/", 1)[0] for p in ports).most_common(1)[0][0]
        if not is_owned(conn):
            e.connector = conn
        conn_el = b.el(conn)
        for nep in b.refs(conn_el.find(q("NETWORK-ENDPOINT-REFS")) if conn_el is not None else None,
                          "NETWORK-ENDPOINT-REF"):
            if is_owned(nep) and b.el(nep) is not None:
                cfgv4 = b.el(nep).find(".//" + q("IPV-4-CONFIGURATION"))
                if cfgv4 is not None:
                    e.ecu_ip = (cfgv4.findtext(q("IPV-4-ADDRESS")) or "").strip()
                    e.ecu_netmask = (cfgv4.findtext(q("NETWORK-MASK")) or e.ecu_netmask).strip()
        ctrl = b.ref(conn_el, "COMM-CONTROLLER-REF") if conn_el is not None else ""
        if ctrl and is_owned(ctrl) and b.el(ctrl) is not None:
            e.mac = (b.el(ctrl).findtext(".//" + q("MAC-UNICAST-ADDRESS")) or "").strip()
    # ---- sockets of both directions (one socket when both use the same socket connection)
    e.one_socket = False
    conns = {}
    for direction, side in (("CAN->ETH", e.can_to_eth), ("ETH->CAN", e.eth_to_can)):
        ids = [h for r in eth_routes if (r.eth is r.dst) == (direction == "CAN->ETH") for h in r.eth.ids]
        conn_path = next((c for h in ids for c in h.connections), "")
        if not conn_path:
            continue
        conns[direction] = conn_path
        _side_from_connection(b, conn_path, side, is_owned)
        if not e.id_set:
            id_set = ids[0].path.rsplit("/", 1)[0]
            e.id_set = id_set.rsplit("/", 1)[-1] if is_owned(id_set) else id_set
        if b.el(conn_path).getparent().getparent().find(".//" + q("TCP-TP")) is not None:
            e.protocol = "TCP"
    if len(conns) == 2 and conns["CAN->ETH"] == conns["ETH->CAN"]:
        e.one_socket = True
    # ---- CAN buses: one per CAN channel of the routes
    from .config import BusInput
    channels = []
    ends = [(r.src if r.eth is r.dst else r.dst) for r in eth_routes] + [e for r in can_routes for e in (r.src, r.dst)]
    for end in ends:
        ch = end.channel or end.pt.rsplit("/", 1)[0]
        if ch and ch not in channels:
            channels.append(ch)
    for ch in channels:
        cfg.buses.append(BusInput(channel=ch))
    return cfg


def _side_from_connection(b: Base, conn_path: str, side: SocketSide, is_owned):
    conn = b.el(conn_path)
    sock = conn.getparent().getparent()            # STATIC-SOCKET-CONNECTIONS -> SOCKET-ADDRESS
    sock_path = b.path_of.get(sock, "")
    s = b.socket(sock_path)
    if is_owned(sock_path):
        side.local_name, side.local_port = s.name, s.port
    else:
        side.local_socket = sock_path
        if is_owned(conn_path):
            side.connection_name = conn_path.rsplit("/", 1)[-1]
    remote = (b.refs(conn.find(q("REMOTE-ADDRESSS")), "SOCKET-ADDRESS-REF") or [""])[0]
    rs = b.socket(remote) if remote else None
    if rs is None:
        return
    if is_owned(remote):
        side.remote_name, side.remote_port, side.remote_ip = rs.name, rs.port, rs.ip or ""
        if rs.endpoint and is_owned(rs.endpoint):
            side.remote_endpoint_name = rs.endpoint.rsplit("/", 1)[-1]
            cfgv4 = b.el(rs.endpoint).find(".//" + q("IPV-4-CONFIGURATION"))
            if cfgv4 is not None and cfgv4.findtext(q("NETWORK-MASK")):
                side.remote_netmask = cfgv4.findtext(q("NETWORK-MASK")).strip()
        elif rs.endpoint:
            side.remote_endpoint = rs.endpoint
    else:
        side.remote_socket = remote
