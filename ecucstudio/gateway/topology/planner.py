"""Plan every ECU of a topology together.

1. Each ECU is planned alone (its CAN messages, CAN -> CAN pairs, everything to / from the default peer).
2. Messages one ECU receives are paired with messages another ECU sends (same rules as CAN -> CAN: name, name
   without gateway prefix, CAN id + length; length / signal layout checked; N:1 needs a link).
3. Each ECU is planned again with the result: the sender sends the PDU to the other ECU (eth_peers), the receiver
   takes it from the sender (eth_peer) under the sender's Ethernet PDU name.
4. Header ids are assigned once for the whole network: unique on the sending socket and on every receiving socket
   (including the ids the bases already use), lock file / user values first. Both ends get the same value.
"""
from __future__ import annotations

import collections
import copy
import os
from dataclasses import dataclass, field

from ..config import EthPeer, GatewayConfig, SocketSide
from ..planner import (CAN_TO_ETH, ETH_TO_CAN, Planner, Route, _norm_gateway_name, _valid_ip, fmt, load_base,
                       pair_problem, sanitize)
from .config import TopologyConfig, socket_side


@dataclass
class CrossRoute:
    """A message one ECU receives on CAN and another ECU sends on CAN, routed directly over Ethernet."""
    key: str                        # "<ecu>/<bus>/<msg> -> <ecu>/<bus>/<msg>"
    src_ecu: str
    src: Route                      # CAN -> ETH route of the sending ECU
    dst_ecu: str
    dst: Route                      # ETH -> CAN route of the receiving ECU
    match: str
    enabled: bool = True
    reason: str = ""
    notes: list[str] = field(default_factory=list)
    change: str = ""
    header_id: int = -1
    eth_pdu: str = ""


@dataclass
class EthLink:
    """One Ethernet PDU of the network and its receivers (rows of the contract)."""
    key: str
    sender: str
    bus: str
    message: str
    can_id: str
    length: int
    eth_pdu: str
    header_id: int
    header_note: str
    sender_addr: str
    receivers: list = field(default_factory=list)   # (node, "ip:port", "bus/message" of an ECU or "")


@dataclass
class TopologyPlan:
    cfg: TopologyConfig
    plans: dict = field(default_factory=dict)       # ECU name -> Plan
    cross: list = field(default_factory=list)       # CrossRoute
    links: list = field(default_factory=list)       # EthLink
    header_ids: dict = field(default_factory=dict)  # link key -> header id (lock file)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    infos: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and all(p.ok for p in self.plans.values())

    @property
    def enabled_cross(self) -> list[CrossRoute]:
        return [c for c in self.cross if c.enabled]


class TopologyPlanner:
    def __init__(self, cfg: TopologyConfig, bases: dict | None = None, dbc_cache: dict | None = None):
        self.cfg = cfg
        self.bases = bases if bases is not None else {}     # cache: key -> Base (the GUI keeps it between runs)
        self.dbc_cache = dbc_cache if dbc_cache is not None else {}
        self.plan = TopologyPlan(cfg)
        self.lock = cfg.load_lock()
        self.dp = None                                      # default peer node

    def err(self, msg):
        self.plan.errors.append(msg)

    def warn(self, msg):
        self.plan.warnings.append(msg)

    def info(self, msg):
        self.plan.infos.append(msg)

    # ------------------------------------------------------------------ main
    def run(self) -> TopologyPlan:
        if not self._check_nodes():
            return self.plan
        first = {}
        for node in self.cfg.ecus:
            pl = self._planner(node, {}, first=True)
            if pl is None:
                continue
            pl.run()
            first[node.name] = pl
            for e in pl.plan.errors:
                self.err(f"[{node.name}] {e}")
        if self.plan.errors:
            self.plan.plans = {n: pl.plan for n, pl in first.items()}
            return self.plan
        self._pair(first)
        overrides = self._overrides(first)
        second = {}
        for node in self.cfg.ecus:
            pl = self._planner(node, overrides.get(node.name, {}))
            pl.prepare()
            second[node.name] = pl
        self.plan.plans = {n: pl.plan for n, pl in second.items()}
        self._rebind(second)
        if not any(pl.plan.errors for pl in second.values()):
            self._check_addresses(second)
            self._assign_headers(second)
            for pl in second.values():
                pl.assign_header_ids()
                pl.finish()
            self._build_links(second)
        for name, pl in second.items():
            for kind, items in (("err", pl.plan.errors), ("warn", pl.plan.warnings), ("info", pl.plan.infos)):
                for m in items:
                    getattr(self, kind)(f"[{name}] {m}")
        n_on = len(self.plan.enabled_cross)
        if self.plan.cross:
            self.info(f"ECU -> ECU: {n_on} of {len(self.plan.cross)} paired message(s) routed directly over "
                      f"Ethernet.")
        return self.plan

    # ------------------------------------------------------------------ checks
    def _check_nodes(self) -> bool:
        cfg = self.cfg
        if not cfg.ecus:
            self.err("The topology has no ECU.")
        seen, ips = set(), {}
        for n in [*cfg.ecus, *cfg.peers]:
            kind = "ECU" if n in cfg.ecus else "Peer"
            if not n.name:
                self.err(f"{kind} without a name.")
                continue
            if sanitize(n.name) != n.name:
                self.err(f"{kind} name '{n.name}': use letters, digits and _ only (it is used in short names).")
            if n.name in seen:
                self.err(f"Node name {n.name} is used twice.")
            seen.add(n.name)
            if not _valid_ip((n.ip or "").strip()):
                self.err(f"{kind} {n.name}: enter its IPv4 address.")
            elif n.ip.strip() in ips:
                self.err(f"{kind} {n.name} has the IP address of {ips[n.ip.strip()]}.")
            else:
                ips[n.ip.strip()] = n.name
            for port in cfg.ports(n):
                if not isinstance(port, int) or not 0 < port < 65536:
                    self.err(f"{kind} {n.name}: port {port} is out of range.")
        for n in cfg.ecus:
            if n.generate and not n.gateway.output:
                self.err(f"ECU {n.name}: select the output file (or do not generate it).")
            if not n.gateway.buses:
                self.err(f"ECU {n.name}: add its CAN buses (DBC files or channels of its project).")
        if cfg.default_peer:
            self.dp = next((p for p in cfg.peers if p.name == cfg.default_peer), None)
            if self.dp is None:
                self.err(f"The default peer {cfg.default_peer} is not a peer of the topology.")
        return not self.plan.errors

    # ------------------------------------------------------------------ one ECU
    def _endpoint_name(self, node) -> str:
        if node in self.cfg.ecus:
            v = self.cfg.ethernet.vlan_id
            ecu = node.gateway.ecu.rsplit("/", 1)[-1] if node.gateway.ecu and not node.gateway.base else node.name
            return sanitize(fmt(node.gateway.naming.eth_endpoint, ecu=ecu,
                                vlan=f"VLAN{v}" if v is not None else "Untagged", vlan_id="" if v is None else v))
        return f"NEP_{node.name}"

    def _sides_to(self, node, partner) -> tuple[SocketSide, SocketSide]:
        """Socket settings of *node* towards *partner*: (CAN -> ETH, ETH -> CAN)."""
        p_tx, p_rx = self.cfg.ports(partner)
        over = node.sockets.get(partner.name) or {}
        nep = self._endpoint_name(partner)
        rx_name, tx_name = (f"SA_{partner.name}_CanGw",) * 2 if self.cfg.one_socket else \
            (f"SA_{partner.name}_CanGw_Rx", f"SA_{partner.name}_CanGw_Tx")
        to = (socket_side(over["can_to_eth"]) if over.get("can_to_eth") else
              SocketSide(remote_ip=partner.ip.strip(), remote_port=p_rx, remote_name=rx_name,
                         remote_endpoint_name=nep, remote_netmask=self.cfg.ethernet.netmask))
        frm = (socket_side(over["eth_to_can"]) if over.get("eth_to_can") else
               SocketSide(remote_ip=partner.ip.strip(), remote_port=p_tx, remote_name=tx_name,
                          remote_endpoint_name=nep, remote_netmask=self.cfg.ethernet.netmask))
        return to, frm

    def _ecu_config(self, node, overrides: dict, first: bool = False) -> GatewayConfig:
        cfg, te = self.cfg, self.cfg.ethernet
        gc = GatewayConfig.from_dict(copy.deepcopy(node.gateway.to_dict()))
        gc.topology = cfg.path
        if not gc.base and not gc.ecu:
            gc.ecu = node.name
        if not node.generate and not gc.output:
            # only referenced: nothing is written for it, the planner just needs a file name
            gc.output = os.path.join(os.path.dirname(cfg.path) if cfg.path else os.getcwd(),
                                     f"{node.name}_Gateway.arxml")
        elif node.generate and not gc.previous and gc.output and os.path.isfile(gc.output):
            gc.previous = gc.output                 # regeneration: DaVinci keeps what does not change
        gc.options.eth_routes = not first or self.dp is not None
        if cfg.routing_table and not gc.routing_table:        # one routing table for the whole network
            gc.routing_table, gc.options.table_hw = cfg.routing_table, cfg.table_hw
        e = gc.ethernet
        if te.channel and not e.channel and not e.new_channel:
            e.channel = te.channel
        if e.vlan_id is None and te.vlan_id is not None:
            e.vlan_id = te.vlan_id
        e.ecu_ip = e.ecu_ip or node.ip.strip()
        e.mac = e.mac or (node.mac or "").strip()
        e.one_socket = cfg.one_socket
        e.ecu_netmask = te.netmask or e.ecu_netmask
        e.protocol = te.protocol or e.protocol
        tx, rx = cfg.ports(node)
        if self.dp is not None:
            to, frm = self._sides_to(node, self.dp)
            e.default_peer = self.dp.name
        else:
            to, frm = SocketSide(), SocketSide()
            e.default_peer = "NoDefaultPeer"
        if not to.local_socket and to.local_port is None:
            to.local_port = tx
        if not frm.local_socket and frm.local_port is None:
            frm.local_port = rx
        # the partners know these sockets by the node name (SA_<node>_CanGw[_Rx]): same name in every file
        if not to.local_socket and not to.local_name:
            to.local_name = f"SA_{node.name}_CanGw" if cfg.one_socket else f"SA_{node.name}_CanGw_Tx"
        if not frm.local_socket and not frm.local_name:
            frm.local_name = f"SA_{node.name}_CanGw" if cfg.one_socket else f"SA_{node.name}_CanGw_Rx"
        e.can_to_eth, e.eth_to_can = to, frm
        e.peers = [EthPeer(o.name, *self._sides_to(node, o)) for o in [*cfg.ecus, *cfg.peers]
                   if o is not node and o is not self.dp]
        for i, b in enumerate(gc.buses):
            for msg, o in overrides.get(i, {}).items():
                cur = {k: v for k, v in b.messages.get(msg, {}).items() if k not in ("eth_peers", "eth_peer")}
                cur.update(o)
                b.messages[msg] = cur
        return gc

    def _planner(self, node, overrides: dict, first: bool = False) -> Planner | None:
        gc = self._ecu_config(node, overrides, first)
        key = (node.name, os.path.abspath(gc.base) if gc.base else "", gc.previous, gc.output, gc.schema, gc.ecu)
        base = self.bases.get(key)
        if base is None:
            try:
                base = self.bases[key] = load_base(gc)
            except (OSError, ValueError) as exc:
                self.err(f"[{node.name}] cannot read {gc.base or 'the base'}: {exc}")
                return None
        return Planner(gc, base, self.dbc_cache)

    # ------------------------------------------------------------------ pairing between ECUs
    @staticmethod
    def _candidate(r: Route, fed_locally: set) -> bool:
        if r.can_problem or id(r) in fed_locally:
            return False
        over = r.bus.cfg.messages.get(r.message.name, {}) if isinstance(r.bus.cfg.messages, dict) else {}
        if "enabled" in over and not over["enabled"]:
            return False
        m, bc = r.message, r.bus.cfg
        if (m.nm and not bc.include_nm) or (m.diag and not bc.include_diag):
            return False
        return not r.reason.startswith(("sent by Com", "already routed"))

    def _pair(self, first: dict):
        cfg, plan = self.cfg, self.plan
        rx, tx = [], []
        for ecu, pl in first.items():
            fed = {id(cr.dst) for cr in pl.plan.enabled_can_routes}
            for r in pl.plan.routes:
                if self._candidate(r, fed):
                    (rx if r.direction == CAN_TO_ETH else tx).append((ecu, r))
        by_name, by_norm, by_id = (collections.defaultdict(list) for _ in range(3))
        for ecu, r in rx:
            m = r.message
            by_name[m.name].append((ecu, r))
            by_norm[_norm_gateway_name(m.name)].append((ecu, r))
            by_id[(m.can_id, m.extended, r.length)].append((ecu, r))
        pairs = {}
        for d, t in tx:
            m = t.message
            tiers = [(by_name.get(m.name), "same name"),
                     (by_norm.get(_norm_gateway_name(m.name)), "same name without gateway prefix")]
            if cfg.cross.match_id:
                tiers.append((by_id.get((m.can_id, m.extended, t.length)), "renamed: same CAN id and length"))
            for cands, how in tiers:
                cands = [c for c in (cands or []) if c[0] != d]
                if cands:
                    if len(cands) > 1:
                        same = [c for c in cands if c[1].message.can_id == m.can_id]
                        cands = same if len(same) == 1 else cands
                    pairs[(d, t.key)] = (d, t, cands, how)
                    break
        find = lambda items, text: next(((e, r) for e, r in items if f"{e}/{r.key}" == text), None)
        for link in cfg.links:
            s, t = find(rx, link.get("src", "")), find(tx, link.get("dst", ""))
            if s is None or t is None:
                self.warn(f"Link {link.get('src')} -> {link.get('dst')}: the message is not received / sent by "
                          f"that ECU on that bus.")
                continue
            if s[0] == t[0]:
                self.warn(f"Link {link.get('src')} -> {link.get('dst')}: both ends are on the same ECU (that is a "
                          f"CAN -> CAN route of the ECU).")
                continue
            pairs[(t[0], t[1].key)] = (t[0], t[1], [s], "link")
        for d, t, cands, how in pairs.values():
            s, src = cands[0]
            cr = CrossRoute(f"{s}/{src.key} -> {d}/{t.key}", s, src, d, t, how)
            if how != "same name":
                cr.notes.append(how)
            if len(cands) > 1:
                cr.enabled = False
                cr.reason = (f"received by {len(cands)} ECU buses (" + ", ".join(f"{e}/{r.bus.name}" for e, r in cands)
                             + "): add a link to choose the source")
            else:
                reason, notes = pair_problem(src, t, f"{s}/{src.bus.name}", f"{d}/{t.bus.name}")
                cr.notes += notes
                if reason:
                    cr.enabled, cr.reason = False, reason
            over = cfg.routes.get(cr.key, {}) if isinstance(cfg.routes, dict) else {}
            if "enabled" in over:
                cr.enabled = bool(over["enabled"])
                cr.reason = "" if cr.enabled else (cr.reason or "deselected")
            plan.cross.append(cr)
        order = {n.name: i for i, n in enumerate(cfg.ecus)}
        plan.cross.sort(key=lambda c: (order[c.src_ecu], c.src.bus.name, c.src.message.can_id, order[c.dst_ecu]))

    def _overrides(self, first: dict) -> dict:
        """Message overrides of every ECU for the second planning: {ecu: {bus index: {message: {...}}}}."""
        cfg, dp = self.cfg, self.dp
        ov = collections.defaultdict(lambda: collections.defaultdict(dict))

        def slot(ecu, r):
            buses = first[ecu].cfg.buses
            i = next(k for k, b in enumerate(buses) if b is r.bus.cfg)
            return ov[ecu][i].setdefault(r.message.name, {})
        dests, also = collections.defaultdict(list), {}
        sources = {}
        for cr in self.plan.enabled_cross:
            k = (cr.src_ecu, id(cr.src))
            dests[k].append(cr.dst_ecu)
            over = cfg.routes.get(cr.key, {}) if isinstance(cfg.routes, dict) else {}
            if over.get("also_to_default_peer", cfg.cross.also_to_default_peer):
                also[k] = True
            if over.get("header_id") not in (None, ""):
                slot(cr.src_ecu, cr.src)["header_id"] = over["header_id"]
            spl = first[cr.src_ecu]
            name = cr.src.eth_pdu or sanitize(fmt(spl.cfg.naming.eth_pdu, bus=cr.src.bus.name, msg=cr.src.message.name,
                                                  ecu=spl.plan.ecu_name, node=cr.src.bus.cfg.node or spl.plan.ecu_name,
                                                  canid=f"{cr.src.message.can_id:X}"))
            o = slot(cr.dst_ecu, cr.dst)
            o["eth_peer"], o["eth_pdu"] = cr.src_ecu, name
            sources[(cr.dst_ecu, id(cr.dst))] = cr.src_ecu
        for (s, _rid), ds in dests.items():
            src = next(c.src for c in self.plan.enabled_cross if c.src_ecu == s and id(c.src) == _rid)
            slot(s, src)["eth_peers"] = ds + ([dp.name] if dp is not None and also.get((s, _rid)) else [])
        # messages without a partner ECU: default peer, or not routed
        n_to = {(c.dst_ecu, id(c.dst)): c for c in self.plan.cross}
        for ecu, pl in first.items():
            fed = {id(cr.dst) for cr in pl.plan.enabled_can_routes}
            for r in pl.plan.routes:
                if not self._candidate(r, fed):
                    continue
                if r.direction == CAN_TO_ETH and (ecu, id(r)) not in dests and dp is None:
                    slot(ecu, r).update(enabled=False, reason="no other ECU needs it (no default peer)")
                elif r.direction == ETH_TO_CAN and (ecu, id(r)) not in sources:
                    cr = n_to.get((ecu, id(r)))
                    if cr is not None and cr.reason.startswith("received by"):
                        slot(ecu, r).update(enabled=False, reason=cr.reason)
                    elif dp is None or not cfg.cross.from_default_peer:
                        slot(ecu, r).update(enabled=False, reason="no source in another ECU")
        return ov

    def _rebind(self, second: dict):
        """Cross routes point to the routes of the second planning."""
        for cr in self.plan.cross:
            for attr, ecu in (("src", cr.src_ecu), ("dst", cr.dst_ecu)):
                old = getattr(cr, attr)
                new = next((r for r in second[ecu].plan.routes if r.key == old.key and r.direction == old.direction),
                           None)
                if new is not None:
                    setattr(cr, attr, new)
            if cr.enabled:
                cr.change = cr.dst.change or cr.src.change
                cr.eth_pdu = cr.src.eth_pdu
                if cr.dst.eth_pdu and cr.dst.eth_pdu != cr.src.eth_pdu:
                    self.warn(f"{cr.key}: the Ethernet PDU is {cr.src.eth_pdu} at {cr.src_ecu} but {cr.dst.eth_pdu} "
                              f"at {cr.dst_ecu} (the name is taken in its base).")

    # ------------------------------------------------------------------ addresses
    def _check_addresses(self, second: dict):
        cfg = self.cfg
        for node in cfg.ecus:
            pl = second[node.name]
            eth = getattr(pl, "_eth", None)
            if eth is None or not pl.plan.local_endpoint:
                continue
            ip = next((e.ip for e in eth.endpoints if e.path == pl.plan.local_endpoint), None) or pl.plan.ecu_ip
            if ip and ip.strip() != node.ip.strip():
                self.err(f"[{node.name}] its IP address on {eth.name} is {ip} in the base, the topology says "
                         f"{node.ip}.")
        for cr in self.plan.enabled_cross:
            a = self.plan.plans[cr.src_ecu].sides.get((CAN_TO_ETH, cr.dst_ecu))
            b = self.plan.plans[cr.dst_ecu].sides.get((ETH_TO_CAN, cr.src_ecu))
            if a is None or b is None:
                continue
            if a.remote_port != b.local_port or a.local_port != b.remote_port:
                self.err(f"{cr.key}: the ports do not match ({cr.src_ecu} sends {a.local_port} -> {a.remote_port}, "
                         f"{cr.dst_ecu} expects {b.remote_port} -> {b.local_port}).")

    # ------------------------------------------------------------------ header ids for the whole network
    def _assign_headers(self, second: dict):
        cfg, plan = self.cfg, self.plan
        used = collections.defaultdict(dict)        # ("tx" | "rx", node) -> {header id: owner}
        for ecu, pl in second.items():
            if not pl.plan.sides:
                continue
            names = {}
            for (d, peer), sp in pl.plan.sides.items():
                if d == CAN_TO_ETH:
                    names[("tx", sp.local)] = ("tx", ecu)
                    names[("rx", sp.remote)] = ("rx", peer)
                else:
                    names[("rx", sp.local)] = ("rx", ecu)
            for k, ids in pl._scopes().items():
                if k in names:
                    for hid, owner in ids.items():
                        used[names[k]].setdefault(hid, f"{owner} (base of {ecu})")
        items = []
        order = {"user": 0, "lock": 1, "previous": 2, "": 3}
        for ecu, pl in second.items():
            hcfg = pl.cfg.header
            for r in pl.plan.enabled_routes:
                if not any((r.direction, p) in pl.plan.sides for p in r.peers) or r.fanout_of is not None:
                    continue                        # 1:N: the other buses get the id of the PDU's route
                if r.direction == CAN_TO_ETH:
                    key, sender, keys = f"{ecu}/{r.key}", ecu, [("tx", ecu)] + [("rx", p) for p in r.peers]
                else:
                    src = r.peers[0]
                    if cfg.ecu(src) is not None:
                        continue                    # receiving end of an ECU -> ECU link: gets the sender's id
                    key, sender, keys = f"{src}->{ecu}/{r.key}", src, [("tx", src), ("rx", ecu)]
                base_id = r.message.can_id | (0x80000000 if hcfg.extended_flag and r.message.extended else 0)
                if r.header_note == "set by user" and r.header_id >= 0:
                    fixed, kind = r.header_id, "user"
                elif key in self.lock:
                    fixed, kind = self.lock[key], "lock"
                elif r.locked and r.header_id >= 0:
                    fixed, kind = r.header_id, "previous"
                else:
                    fixed, kind = -1, ""
                items.append((order[kind], key, sender, keys, base_id, fixed, kind, r, hcfg))
        items.sort(key=lambda x: x[0])               # stable: ECU / route order within each group
        values = {}
        for _o, key, sender, keys, base_id, fixed, kind, r, hcfg in items:
            note = {"user": "set by user", "lock": "kept (lock file)", "previous": "kept from the previous file"}.get(
                kind, "")
            if fixed >= 0:
                clash = [used[k][fixed] for k in keys if fixed in used[k]]
                if clash and kind == "user":
                    self.err(f"{key}: header id 0x{fixed:08X} is already used by {clash[0]} on the same socket.")
                elif clash:
                    self.warn(f"{key}: header id 0x{fixed:08X} ({note}) is now used by {clash[0]}; a new header id "
                              f"is assigned.")
                    fixed = -1
            if fixed < 0:
                cand, k, shift = base_id, 0, hcfg.flag_shift
                max_flag = (0xFFFFFFFF >> shift) if not hcfg.extended_flag else (0x7FFFFFFF >> shift)
                while any(cand in used[x] for x in keys):
                    k += 1
                    if k > max_flag:
                        cand = None
                        break
                    cand = base_id | (k << shift)
                if cand is None:
                    self.err(f"{key}: no free header id for CAN id {r.message.id_text}.")
                    continue
                note = ""
                if k:
                    owner = next(used[x][base_id] for x in keys if base_id in used[x])
                    note = f"flag {k} added (0x{base_id:08X} used by {owner})"
                    if r.fanout_reason:
                        note += f"; {r.fanout_reason}"
                    self.warn(f"Header id 0x{base_id:08X} of {key} is already used by {owner}; using 0x{cand:08X} "
                              f"(flag {k} in bits {shift}..31).")
                fixed = cand
            for x in keys:
                used[x].setdefault(fixed, f"{r.eth_pt_name} ({sender})")
            values[key] = fixed
            r.header_id, r.header_note, r.locked = fixed, note, False
        plan.header_ids = values
        # the receiving end of every ECU -> ECU link uses the sender's header id
        for cr in plan.enabled_cross:
            hid = values.get(f"{cr.src_ecu}/{cr.src.key}")
            if hid is None or not cr.dst.enabled:
                continue
            cr.header_id = hid
            cr.dst.header_id, cr.dst.header_note, cr.dst.locked = hid, f"same as {cr.src_ecu}", False

    # ------------------------------------------------------------------ contract
    def _build_links(self, second: dict):
        cfg, plan = self.cfg, self.plan
        cross_dst = {(c.src_ecu, c.src.key, c.dst_ecu): c for c in plan.enabled_cross}
        for node in cfg.ecus:
            pl = second[node.name]
            for r in pl.plan.enabled_routes:
                sides = pl.plan.sides
                if r.direction == CAN_TO_ETH:
                    sps = [(p, sides.get((CAN_TO_ETH, p))) for p in r.peers]
                    sps = [(p, sp) for p, sp in sps if sp is not None]
                    if not sps:
                        continue
                    link = EthLink(f"{node.name}/{r.key}", node.name, r.bus.name, r.message.name, r.message.id_text,
                                   r.length, r.eth_pdu, r.header_id, r.header_note,
                                   f"{node.ip}:{sps[0][1].local_port}")
                    for p, sp in sps:
                        other = cfg.node(p)
                        c = cross_dst.get((node.name, r.key, p))
                        link.receivers.append((p, f"{sp.remote_ip or (other.ip if other else '?')}:{sp.remote_port}",
                                               f"{c.dst.bus.name}/{c.dst.message.name}" if c else ""))
                    plan.links.append(link)
                else:
                    src = r.peers[0] if r.peers else ""
                    sp = sides.get((ETH_TO_CAN, src))
                    if sp is None or cfg.ecu(src) is not None:
                        continue
                    other = cfg.node(src)
                    link = EthLink(f"{src}->{node.name}/{r.key}", src, "", r.message.name, r.message.id_text,
                                   r.length, r.eth_pdu, r.header_id, r.header_note,
                                   f"{sp.remote_ip or (other.ip if other else '?')}:{sp.remote_port}")
                    link.receivers.append((node.name, f"{node.ip}:{sp.local_port}", f"{r.bus.name}/{r.message.name}"))
                    plan.links.append(link)


def make_topology_plan(cfg: TopologyConfig, bases: dict | None = None, dbc_cache: dict | None = None) -> TopologyPlan:
    return TopologyPlanner(cfg, bases, dbc_cache).run()


__all__ = ["CrossRoute", "EthLink", "TopologyPlan", "TopologyPlanner", "make_topology_plan"]
