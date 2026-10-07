"""Ethernet nodes of the vehicle network: name, MAC address, IPv4 address and port base of every node.

The generator fills these values into the empty Ethernet fields of a gateway configuration or a topology (it never
overwrites a value that is already there). The table belongs to the user, not to a project: it is kept in the user
settings (%APPDATA%/EcucStudio/settings.json, key "gateway_nodes") and edited with the "Ethernet nodes" dialog.

Port base: the port of the node's gateway socket. One socket for both directions: the node sends and receives on
the port base. A socket per direction: it sends from the port base and receives on port base + 1.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

SETTINGS_KEY = "gateway_nodes"


@dataclass
class EthNode:
    name: str
    mac: str = ""
    ip: str = ""
    port: int | None = None         # port base


def load(settings=None) -> list[EthNode]:
    if settings is None:
        from ..settings import Settings
        settings = Settings()
    out = []
    for x in settings.get(SETTINGS_KEY) or []:
        if isinstance(x, dict) and str(x.get("name", "")).strip():
            port = x.get("port")
            out.append(EthNode(str(x["name"]).strip(), str(x.get("mac") or "").strip(), str(x.get("ip") or "").strip(),
                               int(port) if str(port or "").strip().isdigit() else None))
    return out


def save(nodes: list[EthNode], settings=None):
    if settings is None:
        from ..settings import Settings
        settings = Settings()
    settings[SETTINGS_KEY] = [asdict(n) for n in nodes]
    settings.save()


def find(nodes: list[EthNode], name: str) -> EthNode | None:
    """The node called *name* (case-insensitive), else the node *name* belongs to when the DBC names the ECU per bus
    ("<node>_Body")."""
    key = (name or "").strip().lower()
    if not key:
        return None
    for n in nodes:
        if n.name.lower() == key:
            return n
    hits = [n for n in nodes if key.startswith(n.name.lower() + "_")]
    return max(hits, key=lambda n: len(n.name)) if hits else None


def ports(node: EthNode, one_socket: bool) -> tuple[int | None, int | None]:
    """(port the node sends from, port it receives on)."""
    if node.port is None:
        return None, None
    return (node.port, node.port) if one_socket else (node.port, node.port + 1)


def _fill_remote(side_to, side_from, node: EthNode, one_socket: bool, label: str, out: list):
    tx, rx = ports(node, one_socket)
    done = []
    for side, port in ((side_to, rx), (side_from, tx)):      # CAN -> ETH goes to its receive port
        if side is None or side.remote_socket:
            continue
        if not side.remote_endpoint and not side.remote_ip and node.ip:
            side.remote_ip = node.ip
            done.append(f"IP {node.ip}")
        if side.remote_port is None and port is not None:
            side.remote_port = port
            done.append(f"port {port}")
    if done:
        out.append(f"{label}: " + ", ".join(dict.fromkeys(done)))


def fill_gateway(cfg, nodes: list[EthNode]) -> list[str]:
    """Fill the empty Ethernet fields of a GatewayConfig from the node table. Returns what was filled."""
    if not nodes:
        return []
    e, out = cfg.ethernet, []
    names = [(cfg.ecu or "").rsplit("/", 1)[-1]] + [b.node for b in cfg.buses if b.node]
    me = next((n for n in (find(nodes, x) for x in names) if n is not None), None)
    if me is not None:
        done = []
        if not e.ecu_ip and not e.local_endpoint and me.ip:
            e.ecu_ip = me.ip
            done.append(f"IP {me.ip}")
        if not e.mac and me.mac:
            e.mac = me.mac
            done.append(f"MAC {me.mac}")
        tx, rx = ports(me, e.one_socket)
        for side, port in ((e.can_to_eth, tx), (None if e.one_socket else e.eth_to_can, rx)):
            if side is not None and not side.local_socket and side.local_port is None and port is not None:
                side.local_port = port
                done.append(f"port {port}")
        if done:
            out.append(f"{me.name} (this ECU): " + ", ".join(dict.fromkeys(done)))
    if e.default_peer:
        node = find(nodes, e.default_peer)
        if node is not None and node is not me:
            _fill_remote(e.can_to_eth, None if e.one_socket else e.eth_to_can, node, e.one_socket, node.name, out)
    for p in e.peers:
        node = find(nodes, p.name)
        if node is not None and node is not me:
            _fill_remote(p.can_to_eth, None if e.one_socket else p.eth_to_can, node, e.one_socket, node.name, out)
    return out


def fill_topology(t, nodes: list[EthNode]) -> list[str]:
    """Fill the empty addresses / ports / MACs of the ECUs and peers of a TopologyConfig. Returns what was filled."""
    out = []
    for n in [*t.ecus, *t.peers]:
        node = find(nodes, n.name)
        if node is None:
            continue
        done = []
        if not (n.ip or "").strip() and node.ip:
            n.ip = node.ip
            done.append(f"IP {node.ip}")
        if not (n.mac or "").strip() and node.mac:
            n.mac = node.mac
            done.append(f"MAC {node.mac}")
        tx, rx = ports(node, t.one_socket)
        if n.tx_port is None and tx is not None:
            n.tx_port = tx
            done.append(f"port {tx}")
        if not t.one_socket and n.rx_port is None and rx is not None:
            n.rx_port = rx
            done.append(f"receive port {rx}")
        if done:
            out.append(f"{n.name}: " + ", ".join(done))
    return out
