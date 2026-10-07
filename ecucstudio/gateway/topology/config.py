"""Topology file (JSON): the Ethernet network, its nodes and the gateway configuration of every ECU."""
from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import dataclass, field

from ..config import GatewayConfig, SocketSide


@dataclass
class TopoEthernet:
    channel: str = ""               # existing channel in the bases (path, name or VLANnn); empty = the ECU's only one
    vlan_id: int | None = None      # VLAN of a new channel (ECUs whose base has no Ethernet channel yet)
    netmask: str = "255.255.255.0"
    protocol: str = "UDP"


@dataclass
class PeerNode:
    """Ethernet-only node (e.g. a central computer): only its address is needed, no file is generated for it."""
    name: str = ""
    ip: str = ""
    tx_port: int | None = None      # port it sends from (empty = topology tx_port); one socket: its only port
    rx_port: int | None = None      # port it receives on (empty = topology rx_port); one socket: not used
    mac: str = ""                   # MAC address (Ethernet node table; informative for a peer)


@dataclass
class EcuNode:
    name: str = ""
    generate: bool = True           # False: only referenced (its DBC / project tells what it needs and sends)
    ip: str = ""
    tx_port: int | None = None
    rx_port: int | None = None
    mac: str = ""                   # MAC address of a new Ethernet controller of the ECU
    gateway: GatewayConfig = field(default_factory=GatewayConfig)
    # existing sockets to use for a partner: {"<node>": {"can_to_eth": {SocketSide}, "eth_to_can": {SocketSide}}}
    sockets: dict = field(default_factory=dict)


@dataclass
class CrossSettings:
    also_to_default_peer: bool = False   # a message routed ECU -> ECU is also sent to the default peer
    from_default_peer: bool = True       # a message an ECU sends without a source in another ECU comes from it
    match_id: bool = True                # also pair renamed messages (same CAN id, id type and length)


@dataclass
class TopologyConfig:
    name: str = ""
    ethernet: TopoEthernet = field(default_factory=TopoEthernet)
    tx_port: int = 50000            # every node sends from this port ...
    rx_port: int = 50001            # ... and receives on this one (one socket connection per partner)
    # one socket per node for both directions (port = tx_port of the node or of the topology): one socket connection
    # per pair of nodes carries both directions. New topologies switch it on; older files keep two sockets.
    one_socket: bool = False
    peers: list[PeerNode] = field(default_factory=list)
    default_peer: str = ""          # node of the messages no other ECU needs / sends (empty = none)
    ecus: list[EcuNode] = field(default_factory=list)
    cross: CrossSettings = field(default_factory=CrossSettings)
    routes: dict = field(default_factory=dict)   # "<ecu>/<bus>/<msg> -> <ecu>/<bus>/<msg>" -> {"enabled",
                                                 #     "header_id", "also_to_default_peer"}
    links: list = field(default_factory=list)    # extra pairs: {"src": "<ecu>/<bus>/<msg>", "dst": "..."}
    path: str = field(default="", compare=False)  # file the topology was loaded from / saved to (not saved)

    # ------------------------------------------------------------------ nodes
    def node(self, name: str):
        return next((n for n in [*self.ecus, *self.peers] if n.name == name), None)

    def ecu(self, name: str) -> EcuNode | None:
        return next((n for n in self.ecus if n.name == name), None)

    def ports(self, node) -> tuple[int, int]:
        """(port it sends from, port it receives on); the same port twice with one socket per node."""
        tx = node.tx_port if node.tx_port is not None else self.tx_port
        if self.one_socket:
            return tx, tx
        return tx, node.rx_port if node.rx_port is not None else self.rx_port

    @property
    def lock_path(self) -> str:
        return os.path.splitext(self.path)[0] + ".lock.json" if self.path else ""

    @property
    def contract_path(self) -> str:
        return os.path.splitext(self.path)[0] + "_contract.csv" if self.path else ""

    # ------------------------------------------------------------------ JSON
    def to_dict(self, rel_to: str | None = None) -> dict:
        d = {f.name: dataclasses.asdict(getattr(self, f.name)) if dataclasses.is_dataclass(getattr(self, f.name))
             else getattr(self, f.name) for f in dataclasses.fields(self) if f.name not in ("ecus", "peers", "path")}
        d["peers"] = [dataclasses.asdict(p) for p in self.peers]
        d["ecus"] = []
        for e in self.ecus:
            x = {k: v for k, v in dataclasses.asdict(e).items() if k != "gateway"}
            x["gateway"] = e.gateway.to_dict(rel_to)
            d["ecus"].append(x)
        return d

    def save(self, path: str | None = None):
        path = os.path.abspath(path or self.path)
        self.path = path
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(os.path.dirname(path)), fh, indent=2)
            fh.write("\n")

    @classmethod
    def from_dict(cls, d: dict, rel_to: str | None = None) -> "TopologyConfig":
        def build(kind, data):
            names = {f.name for f in dataclasses.fields(kind)}
            return kind(**{k: v for k, v in (data or {}).items() if k in names})
        ecus = []
        for x in d.get("ecus") or []:
            node = build(EcuNode, {k: v for k, v in x.items() if k != "gateway"})
            node.gateway = GatewayConfig.from_dict(x.get("gateway") or {}, rel_to)
            node.sockets = dict(x.get("sockets") or {})
            ecus.append(node)
        cfg = cls(name=d.get("name", ""), ethernet=build(TopoEthernet, d.get("ethernet")),
                  tx_port=int(d.get("tx_port") or 50000), rx_port=int(d.get("rx_port") or 50001),
                  one_socket=bool(d.get("one_socket", False)),
                  peers=[build(PeerNode, p) for p in d.get("peers") or []], default_peer=d.get("default_peer", ""),
                  ecus=ecus, cross=build(CrossSettings, d.get("cross")), routes=dict(d.get("routes") or {}),
                  links=list(d.get("links") or []))
        return cfg

    @classmethod
    def load(cls, path: str) -> "TopologyConfig":
        path = os.path.abspath(path)
        with open(path, encoding="utf-8-sig") as fh:
            cfg = cls.from_dict(json.load(fh), os.path.dirname(path))
        cfg.path = path
        return cfg

    # ------------------------------------------------------------------ lock file
    def load_lock(self) -> dict:
        """Header ids of the last generation: {link key: header id}."""
        p = self.lock_path
        if not p or not os.path.isfile(p):
            return {}
        with open(p, encoding="utf-8-sig") as fh:
            data = json.load(fh)
        out = {}
        for k, v in (data.get("header_ids") or {}).items():
            try:
                out[k] = int(str(v), 0)
            except ValueError:
                pass
        return out

    def save_lock(self, header_ids: dict):
        p = self.lock_path
        if not p:
            return
        data = {"comment": "Written by EcucStudio (CAN gateway topology). Do not edit; keep it with the topology "
                           "file so that every ECU gets the same header ids.",
                "version": 1, "header_ids": {k: f"0x{v:08X}" for k, v in sorted(header_ids.items())}}
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")


def socket_side(data) -> SocketSide:
    names = {f.name for f in dataclasses.fields(SocketSide)}
    return SocketSide(**{k: v for k, v in (data or {}).items() if k in names})


__all__ = ["TopoEthernet", "PeerNode", "EcuNode", "CrossSettings", "TopologyConfig", "socket_side"]
