"""Gateway generator settings. Saved as JSON so a generation can be repeated from the original base file."""
from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import dataclass, field


@dataclass
class Naming:
    """Short-name patterns. Fields: {bus} {msg} {sig} {ecu} {node} {canid} {frame} {pdu} {signal}
    {triggering} {connector} {eth_pdu}; Ethernet patterns: {ecu} {vlan} {vlan_id}."""
    can_cluster: str = "{bus}_Cluster"
    can_channel: str = "{bus}"
    can_controller: str = "CT_{bus}"
    can_connector: str = "CN_{bus}"
    can_frame: str = "{msg}_o{bus}"
    can_pdu: str = "{msg}_o{bus}"
    eth_pdu: str = "{msg}_o{bus}_Eth"
    can_signal: str = "{sig}_o{msg}_o{bus}"
    eth_signal: str = "{sig}_o{eth_pdu}"
    system_signal: str = "{sig}_o{msg}_o{bus}"
    frame_triggering: str = "{frame}_FT"
    pdu_triggering: str = "{pdu}_PT"
    signal_triggering: str = "{signal}_ST"
    frame_port: str = "{triggering}_{ecu}"
    pdu_port: str = "{pdu}_{connector}"
    header_id: str = "{eth_pdu}_ID"
    gateway: str = "Gateway_{ecu}"
    # new Ethernet elements (base file without Ethernet / new VLAN); {vlan} = VLAN<id> or Untagged
    eth_cluster: str = "EthernetCluster"
    eth_channel: str = "Channel_{vlan}"
    eth_controller: str = "CT_{ecu}_Eth"
    eth_connector: str = "CN_{ecu}_{vlan}"
    eth_endpoint: str = "NEP_{ecu}_{vlan}"


@dataclass
class SocketSide:
    """Sockets of one gateway direction. Empty paths mean "create a new element"."""
    local_socket: str = ""          # existing local SOCKET-ADDRESS (owned by the ECU's connector)
    local_name: str = ""            # short name of a new local socket
    local_port: int | None = None
    remote_socket: str = ""         # existing remote SOCKET-ADDRESS
    remote_name: str = ""           # short name of a new remote socket
    remote_port: int | None = None
    remote_endpoint: str = ""       # existing NETWORK-ENDPOINT of the remote node
    remote_ip: str = ""             # used to find or create the remote endpoint
    remote_netmask: str = "255.255.0.0"
    remote_endpoint_name: str = ""
    connection_name: str = ""       # STATIC-SOCKET-CONNECTION (default: reuse the one to the remote socket)


@dataclass
class EthPeer:
    """Another Ethernet node the gateway ECU exchanges PDUs with (besides the default peer of can_to_eth /
    eth_to_can). Empty local socket fields share the local sockets of the default peer."""
    name: str = ""                  # used in message overrides ("eth_peers" / "eth_peer") and in new names
    can_to_eth: SocketSide = field(default_factory=SocketSide)     # where CAN -> ETH PDUs are sent to
    eth_to_can: SocketSide = field(default_factory=SocketSide)     # where ETH -> CAN PDUs come from


@dataclass
class PduCollection:
    """CAN -> ETH: several PDUs in one UDP datagram (SoAd nPdu). Written to the gateway file as PDU-COLLECTION-*
    attributes of the local socket and of every SO-CON-I-PDU-IDENTIFIER; DaVinci derives SoAdSocketnPduUdpTxBufferMin,
    SoAdSocketUdpTriggerTimeout, SoAdTxUdpTriggerMode / Timeout and SoAdTxIfTriggerTransmit = false (QUEUED)."""
    enabled: bool = False
    timeout_ms: float = 5           # a collected PDU is sent at the latest after this time (SoAd main function period)
    buffer: int = 1400              # bytes per UDP datagram (PDU headers included); <= 1472 avoids IP fragmentation
    mode: str = "all"               # all: every PDU is collected | cycle: immediate when cycle <= immediate_cycle_ms
    immediate_cycle_ms: int = 20    # (mode cycle) and for event messages (no cycle time)


@dataclass
class EthernetSettings:
    channel: str = ""               # ETHERNET-PHYSICAL-CHANNEL (path, short name or VLANnn; empty = the only one)
    connector: str = ""             # ECU's ETHERNET-COMMUNICATION-CONNECTOR on that channel (empty = auto / new)
    local_endpoint: str = ""        # NETWORK-ENDPOINT for new local sockets (empty = the connector's)
    # used when the base file has no Ethernet channel, when new_channel is set, or when the ECU is not
    # connected to the channel yet
    new_channel: bool = False       # create a new channel (VLAN) instead of using an existing one
    cluster: str = ""               # ETHERNET-CLUSTER of the new channel (empty = the only one, or a new cluster)
    vlan_id: int | None = None      # VLAN of the new channel (None = untagged)
    channel_name: str = ""          # short name of the new channel (empty = naming pattern)
    ecu_ip: str = ""                # IP address of the ECU (creates its NETWORK-ENDPOINT when there is none)
    ecu_netmask: str = "255.255.255.0"
    controller: str = ""            # ECU's ETHERNET-COMMUNICATION-CONTROLLER for a new connector (empty = auto/new)
    mac: str = ""                   # MAC-UNICAST-ADDRESS of a new controller (optional)
    protocol: str = "UDP"           # UDP | TCP (TCP: the socket connection gets TCP-ROLE)
    tcp_role: str = "CONNECT"
    # one socket for both directions: ETH -> CAN uses the socket (and socket connection) of CAN -> ETH;
    # eth_to_can is then not used. New configurations (GUI, wizard, template) switch it on; files of older
    # versions (no such key) keep a socket per direction.
    one_socket: bool = False
    can_to_eth: SocketSide = field(default_factory=SocketSide)
    eth_to_can: SocketSide = field(default_factory=SocketSide)
    id_set: str = ""                # SOCKET-CONNECTION-IPDU-IDENTIFIER-SET (path or new name; empty = auto)
    default_peer: str = ""          # name of the node of can_to_eth / eth_to_can (empty = "default")
    peers: list[EthPeer] = field(default_factory=list)  # more nodes; a message picks them by name
    collection: PduCollection = field(default_factory=PduCollection)   # CAN -> ETH: PDUs per UDP datagram


@dataclass
class BusInput:
    dbc: str = ""
    node: str = ""                  # the gateway ECU as named in the DBC
    channel: str = ""               # existing CAN-PHYSICAL-CHANNEL (path or name); empty = auto/new
    bus: str = ""                   # bus name for {bus} and new clusters (empty = channel or DBC name)
    new_channel: bool = False       # always create a new CAN cluster, even if a channel with the name exists
    baudrate: int | None = None
    fd_baudrate: int | None = None
    rx: bool = True                 # route messages the node receives: CAN -> ETH
    tx: bool = True                 # route messages the node sends: ETH -> CAN
    include_nm: bool = False
    include_diag: bool = False
    # bus of another ECU (e.g. another zone ECU), named after its node in the DBC: no CAN element is made for it; it
    # tells which messages go to that ECU (it sends them on its bus) or come from it (it receives them) over Ethernet.
    # The Ethernet peer with this name gives its IP address and ports.
    remote_ecu: str = ""
    table_network: str = ""         # network column of the routing table (empty = the column named like the bus / DBC)
    messages: dict = field(default_factory=dict)   # name -> {"enabled": bool, "header_id": "0x..", "eth_pdu": "..",
                                                   #   "eth_peers": [..] (CAN -> ETH destinations),
                                                   #   "eth_peer": ".." (ETH -> CAN source)}


@dataclass
class HeaderSettings:
    extended_flag: bool = False     # always set bit 31 for extended CAN ids (AUTOSAR Can_IdType style)
    flag_shift: int = 29            # older versions added collision flags k << 29 (bits a 29-bit CAN id never uses);
                                    # such kept header ids become the CAN id again (a collision is an error now)


@dataclass
class Options:
    eth_signals: str = "copy"       # copy: ETH PDU gets the same signal layout | none: opaque PDU
    can_tx_timing: str = "event"    # timing of CAN PDUs the gateway sends: event | dbc (cycle time from DBC)
    add_fibex: bool = True          # add new elements to the SYSTEM's FIBEX-ELEMENTS
    only_previous: bool = False     # regeneration: route only the messages of the previous gateway file
    eth_routes: bool = True         # CAN <-> Ethernet routes (node RX -> Ethernet, node TX <- Ethernet)
    can_routes: bool = True         # CAN <-> CAN routes: a message the node receives on one bus and sends on another
    can_match_id: bool = True       # CAN <-> CAN: also pair renamed messages (same CAN id, length and layout)
    eth_fanout: bool = True         # ETH -> CAN: the same CAN id from the same Ethernet node on several buses (same
                                    # length) is one Ethernet PDU forwarded to every bus (1:N)
    eth_no_com: bool = True         # DBC files imported in DaVinci: Com does not send the CAN PDUs fed from Ethernet
                                    # (written to the .vsde file of the DBC converter, see vsde.py)
    dbc_imported: bool = False      # no base file: the DBC files are imported in the DaVinci project of the ECU, the
                                    # output holds only Ethernet + gateway (the CAN part is referenced, see imported)
    table_hw: bool = False          # routing table: also route the rows of a hardware accelerator (HW-Accelerator = 1,
                                    # LLCE / PFE); off = those rows are left to the accelerator


@dataclass
class GatewayConfig:
    base: str = ""                  # existing system description (network.arxml); empty = create a new file
    output: str = ""                # file to write (base + gateway)
    can_gateway: dict = field(default_factory=dict)   # CAN <-> CAN route key -> {"enabled": bool}
    can_links: list = field(default_factory=list)     # extra CAN <-> CAN pairs: {"src_bus", "src_msg",
                                                      #                           "dst_bus", "dst_msg"}
    previous: str = ""              # gateway file of the previous generation (already imported in DaVinci):
                                    # its elements are taken out of the base, its header ids / names are kept
    routing_table: str = ""         # routing table of the customer (.xlsx / .csv): the CAN -> CAN routes (message and
                                    # signal rows) come only from it; empty = messages paired by name / CAN id
    ecu: str = ""                   # gateway ECU-INSTANCE (path or short name; empty = auto). Without a base
                                    # file: name of the new ECU-INSTANCE (empty = node of the first DBC)
    schema: str = "AUTOSAR_00052"   # schema of a new file (without base file); DaVinci 5.24 reads <= AUTOSAR_00049
    system: str = ""                # SYSTEM to extend (empty = auto)
    buses: list[BusInput] = field(default_factory=list)
    ethernet: EthernetSettings = field(default_factory=EthernetSettings)
    header: HeaderSettings = field(default_factory=HeaderSettings)
    naming: Naming = field(default_factory=Naming)
    options: Options = field(default_factory=Options)
    topology: str = ""              # topology file this ECU's gateway belongs to (multi-ECU mode)

    # ------------------------------------------------------------------ JSON
    def to_dict(self, rel_to: str | None = None) -> dict:
        d = dataclasses.asdict(self)
        if rel_to:
            for key in ("base", "output", "previous", "topology", "routing_table"):
                d[key] = _rel(d[key], rel_to)
            for b in d["buses"]:
                b["dbc"] = _rel(b["dbc"], rel_to)
        return d

    def save(self, path: str):
        path = os.path.abspath(path)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(os.path.dirname(path)), fh, indent=2)
            fh.write("\n")

    @classmethod
    def from_dict(cls, d: dict, rel_to: str | None = None) -> "GatewayConfig":
        def build(kind, data):
            if data is None:
                return kind()
            names = {f.name for f in dataclasses.fields(kind)}
            return kind(**{k: v for k, v in data.items() if k in names})
        eth = dict(d.get("ethernet") or {})
        eth["can_to_eth"] = build(SocketSide, eth.get("can_to_eth"))
        eth["eth_to_can"] = build(SocketSide, eth.get("eth_to_can"))
        eth["collection"] = build(PduCollection, eth.get("collection"))
        eth["peers"] = [EthPeer(name=x.get("name", ""), can_to_eth=build(SocketSide, x.get("can_to_eth")),
                                eth_to_can=build(SocketSide, x.get("eth_to_can")))
                        for x in eth.get("peers") or [] if isinstance(x, dict)]
        cfg = cls(base=d.get("base", ""), output=d.get("output", ""), ecu=d.get("ecu", ""),
                  system=d.get("system", ""), schema=d.get("schema", "AUTOSAR_00052"),
                  previous=d.get("previous", ""), topology=d.get("topology", ""),
                  routing_table=d.get("routing_table", ""),
                  can_gateway=dict(d.get("can_gateway") or {}),
                  can_links=list(d.get("can_links") or []),
                  buses=[build(BusInput, b) for b in d.get("buses", [])],
                  ethernet=build(EthernetSettings, eth),
                  header=build(HeaderSettings, d.get("header")),
                  naming=build(Naming, d.get("naming")),
                  options=build(Options, d.get("options")))
        if rel_to:
            cfg.base = _abs(cfg.base, rel_to)
            cfg.output = _abs(cfg.output, rel_to)
            cfg.previous = _abs(cfg.previous, rel_to)
            cfg.topology = _abs(cfg.topology, rel_to)
            cfg.routing_table = _abs(cfg.routing_table, rel_to)
            for b in cfg.buses:
                b.dbc = _abs(b.dbc, rel_to)
        return cfg

    @classmethod
    def load(cls, path: str) -> "GatewayConfig":
        path = os.path.abspath(path)
        with open(path, encoding="utf-8-sig") as fh:      # Notepad saves UTF-8 with a BOM
            return cls.from_dict(json.load(fh), os.path.dirname(path))


def _rel(p: str, base: str) -> str:
    if not p:
        return p
    try:
        # relative also with "..": a project and its gateway file are usually moved / checked out together
        return os.path.relpath(os.path.abspath(p), base)
    except ValueError:              # other drive
        return os.path.abspath(p)


def _abs(p: str, base: str) -> str:
    return os.path.normpath(os.path.join(base, p)) if p and not os.path.isabs(p) else p
