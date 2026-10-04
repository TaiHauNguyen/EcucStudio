"""Multi-ECU mode: several gateway ECUs (zones) on one Ethernet network, configured together.

One topology file lists the ECUs (each a normal gateway configuration: DaVinci project, network file or DBC files)
and the Ethernet-only nodes (peers, e.g. a central computer used as switch). The planner pairs the messages one
ECU receives on CAN with the messages another ECU sends on CAN and routes them directly over Ethernet; both ends
get the same Ethernet PDU, header id, IP addresses and ports. Header ids are kept in a lock file next to the
topology file, so ECUs generated at different times (or by different teams) stay consistent.
"""
from .config import CrossSettings, EcuNode, PeerNode, TopoEthernet, TopologyConfig
from .planner import CrossRoute, EthLink, TopologyPlan, make_topology_plan
from .writer import generate_topology, write_contract

__all__ = ["CrossSettings", "EcuNode", "PeerNode", "TopoEthernet", "TopologyConfig", "CrossRoute", "EthLink",
           "TopologyPlan", "make_topology_plan", "generate_topology", "write_contract"]
