"""Suggest the Ethernet settings of a gateway from what the base file / project already contains.

Every suggestion carries the reason it was made. Suggestions only fill settings that are still empty; values
the user entered are never changed.
"""
from __future__ import annotations

import collections
import ipaddress
from dataclasses import dataclass

from ..arxml import q
from . import dvproject
from .base import Base, EthChannel
from .config import GatewayConfig, SocketSide

DEFAULT_PORT = 50000
DEFAULT_NETWORK = "192.168.{n}.0/24"     # used when the channel has no address yet; {n} = VLAN id or 1


@dataclass
class Suggestion:
    field: str            # dotted config field, e.g. "ethernet.can_to_eth.local_port"
    value: object
    reason: str

    def __str__(self):
        return f"{self.field} = {self.value}   ({self.reason})"


def _ecu(cfg: GatewayConfig, base: Base) -> str:
    ecus = base.ecus()
    if cfg.ecu:
        hits = [e for e in ecus if cfg.ecu in (e, e.rsplit("/", 1)[-1])]
        if hits:
            return hits[0]
    if dvproject.is_project(cfg.base):
        path = dvproject.read(cfg.base).ecu_path
        hits = [e for e in ecus if path in (e, e.rsplit("/", 1)[-1])]
        if hits:
            return hits[0]
    return ecus[0] if len(ecus) == 1 else ""


def _own_endpoints(base: Base, ecu: str) -> set[str]:
    return {p for c in base.ecu_connectors(ecu)
            for p in base.refs(base.el(c).find(q("NETWORK-ENDPOINT-REFS")), "NETWORK-ENDPOINT-REF")}


def _network(ch: EthChannel | None, vlan) -> ipaddress.IPv4Network:
    """Most used IPv4 subnet of the channel, or the default network."""
    nets = collections.Counter()
    for e in (ch.endpoints if ch else []):
        try:
            nets[ipaddress.ip_network(f"{e.ip}/{e.mask or '255.255.255.0'}", strict=False)] += 1
        except ValueError:
            continue
    if nets:
        return nets.most_common(1)[0][0]
    n = vlan if vlan is not None and 0 < int(vlan) < 255 else 1
    return ipaddress.ip_network(DEFAULT_NETWORK.format(n=n))


def _free_ip(net: ipaddress.IPv4Network, used: set[str], after: str | None = None) -> str | None:
    """Next free host address of *net*, continuing after the highest used one (or *after*)."""
    used_ips = set()
    for u in used:
        try:
            used_ips.add(ipaddress.ip_address(u))
        except ValueError:
            continue
    in_net = sorted(a for a in used_ips if a in net)
    start = ipaddress.ip_address(after) if after else (in_net[-1] if in_net else None)
    hosts = list(net.hosts()) if net.num_addresses <= 65536 else None
    if hosts is None:
        return None
    if start is not None:
        for h in hosts:
            if h > start and h not in used_ips:
                return str(h)
    for h in hosts:
        if h not in used_ips:
            return str(h)
    return None


class Suggester:
    def __init__(self, cfg: GatewayConfig, base: Base):
        self.cfg, self.base = cfg, base
        self.out: list[Suggestion] = []

    def add(self, field, value, reason):
        self.out.append(Suggestion(field, value, reason))

    def run(self) -> list[Suggestion]:
        b, cfg, eth = self.base, self.cfg, self.cfg.ethernet
        ecu = _ecu(cfg, b)
        if not ecu:
            return self.out
        ecu_name = ecu.rsplit("/", 1)[-1]
        channels = b.eth_channels()
        mine = [c for c in channels if any(b.connector_ecu(x) == ecu for x in c.connectors)]
        own = _own_endpoints(b, ecu)
        # ------------------------------------------------------------ channel
        ch, vlan = None, eth.vlan_id
        if eth.channel and not eth.new_channel:
            ch = next((c for c in channels if eth.channel in (c.path, c.name) or
                       (c.vlan is not None and eth.channel.upper() in (f"VLAN{c.vlan}", str(c.vlan)))), None)
        elif not eth.new_channel:
            if len(mine) == 1:
                ch = mine[0]
                self.add("ethernet.channel", ch.path, f"the only Ethernet channel {ecu_name} is connected to")
            elif len(mine) > 1:
                ch = max(mine, key=lambda c: self._pdu_header_ids(c))
                self.add("ethernet.channel", ch.path,
                         f"{ecu_name} is on {len(mine)} channels; {ch.name} carries the most PDUs with a SoAd header")
            elif len(channels) == 1:
                ch = channels[0]
                self.add("ethernet.channel", ch.path, f"the only Ethernet channel of the file "
                                                      f"({ecu_name} gets a new connector)")
        if ch is None and not eth.channel:
            if not eth.new_channel and channels:
                self.add("ethernet.new_channel", True, f"{ecu_name} is not connected to any Ethernet channel")
            if vlan is None:
                used = sorted({c.vlan for c in channels if c.vlan is not None})
                if used:
                    vlan = (max(used) // 10 + 1) * 10
                    if vlan <= 4094:
                        self.add("ethernet.vlan_id", vlan, f"next free VLAN after {', '.join(map(str, used))}")
                    else:
                        vlan = None
        elif ch is not None:
            vlan = ch.vlan
        # ------------------------------------------------------------ connector on the channel
        conns = [c for c in (ch.connectors if ch else []) if b.connector_ecu(c) == ecu]
        if len(conns) > 1 and not eth.connector:
            best = max(conns, key=lambda c: (bool(b.refs(b.el(c).find(q("NETWORK-ENDPOINT-REFS")),
                                                         "NETWORK-ENDPOINT-REF")),
                                             sum(1 for s in ch.sockets if s.connector == c)))
            self.add("ethernet.connector", best, f"connector of {ecu_name} on {ch.name} with an IP address and "
                                                 f"the most sockets")
        # ------------------------------------------------------------ IP address of the ECU
        net = _network(ch, vlan)
        used_ips = {e.ip for e in (ch.endpoints if ch else []) if e.ip}
        ecu_ip = None
        own_here = [e for e in (ch.endpoints if ch else []) if e.path in own]
        if own_here:
            ecu_ip = own_here[0].ip
        elif eth.ecu_ip:
            ecu_ip = eth.ecu_ip
        else:
            ecu_ip = _free_ip(net, used_ips)
            if ecu_ip:
                where = f"{ch.name}" if ch is not None else "the new channel"
                why = f"next free address in {net} on {where}" if used_ips else f"default network {net}"
                self.add("ethernet.ecu_ip", ecu_ip, why)
                if str(net.netmask) != (eth.ecu_netmask or ""):
                    self.add("ethernet.ecu_netmask", str(net.netmask), f"netmask of {net}")
        # MAC for a controller that has to be created
        if not b.ecu_controllers(ecu, "ETHERNET-COMMUNICATION-CONTROLLER") and not eth.mac and ecu_ip:
            octets = ipaddress.ip_address(ecu_ip).packed
            mac = "02:00:" + ":".join(f"{x:02X}" for x in octets)
            self.add("ethernet.mac", mac, "locally administered MAC derived from the ECU IP (new controller)")
        # ------------------------------------------------------------ remote node and ports
        partner, why_partner = self._partner(ch, own, ecu)
        remote_ip = partner.ip if partner is not None else None
        if remote_ip is None and ecu_ip:
            remote_ip = _free_ip(net, used_ips | {ecu_ip}, after=ecu_ip)
            why_partner = f"next free address after the ECU in {net} (no other node on the channel)"
        used_ports = {s.port for s in (ch.sockets if ch else []) if s.port}
        tx_port, rx_port = self._port_pair(used_ports)
        port_why = (f"next free port pair after the ports used on {ch.name} (highest {max(used_ports)})"
                    if used_ports else f"no port used on the channel yet; starting at {DEFAULT_PORT}")
        for side_name, side, port in (("can_to_eth", eth.can_to_eth, tx_port), ("eth_to_can", eth.eth_to_can, rx_port)):
            self._side(side_name, side, port, port_why, remote_ip, why_partner, partner)
        return self.out

    def _side(self, name: str, side: SocketSide, port: int, port_why: str, remote_ip, why_partner, partner):
        f = f"ethernet.{name}"
        if not side.local_socket and side.local_port is None:
            self.add(f"{f}.local_port", port, port_why)
        if not side.remote_socket:
            if side.remote_port is None:
                self.add(f"{f}.remote_port", port, "same port number on the other node")
            if not side.remote_ip and not side.remote_endpoint and remote_ip:
                if partner is not None:
                    self.add(f"{f}.remote_endpoint", partner.path, why_partner)
                self.add(f"{f}.remote_ip", remote_ip, why_partner)

    def _partner(self, ch: EthChannel | None, own: set[str], ecu: str):
        """The other node the ECU talks to most on the channel (endpoint of remote sockets)."""
        if ch is None:
            return None, ""
        others = [e for e in ch.endpoints if e.path not in own and e.ip]
        if not others:
            return None, ""
        by_path = {e.path: e for e in others}
        sockets = {s.path: s for s in ch.sockets}
        cnt = collections.Counter()
        for s in ch.sockets:
            if s.connector and self.base.connector_ecu(s.connector) == ecu:
                for c in s.connections:
                    for r in c.remotes:
                        ep = sockets[r].endpoint if r in sockets else None
                        if ep in by_path:
                            cnt[ep] += 1
        if cnt:
            ep, n = cnt.most_common(1)[0]
            return by_path[ep], f"partner of {n} existing socket connection(s) of the ECU on {ch.name}"
        if len(others) == 1:
            return others[0], f"the only other node on {ch.name}"
        return None, ""

    def _pdu_header_ids(self, ch: EthChannel) -> int:
        return sum(len(c.ids) for s in ch.sockets for c in s.connections)

    @staticmethod
    def _port_pair(used: set[int]) -> tuple[int, int]:
        p = (max(used) + 1) if used else DEFAULT_PORT
        while p in used or p + 1 in used:
            p += 1
        return p, p + 1


def suggest(cfg: GatewayConfig, base: Base | None = None) -> list[Suggestion]:
    from .planner import load_base
    return Suggester(cfg, base or load_base(cfg)).run()


def apply(cfg: GatewayConfig, suggestions: list[Suggestion]) -> list[Suggestion]:
    """Write the suggestions into *cfg* where the field is still empty; returns the applied ones."""
    done = []
    for s in suggestions:
        obj = cfg
        *path, last = s.field.split(".")
        for part in path:
            obj = getattr(obj, part)
        cur = getattr(obj, last)
        if cur in (None, "", False):
            setattr(obj, last, s.value)
            done.append(s)
    return done
