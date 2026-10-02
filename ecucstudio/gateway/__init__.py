"""CAN <-> Ethernet PDU gateway generator.

Reads DBC files and an existing system description (e.g. a network.arxml exported by PREEvision), and
writes the system description plus a PduR gateway between the selected node's CAN messages and an
Ethernet socket connection (SoAd PDU header id = CAN id). DaVinci Configurator derives PduR routing
paths and SoAd routes from the result when the file is imported.
"""
from .config import GatewayConfig  # noqa: F401
from .planner import make_plan  # noqa: F401
from .writer import generate  # noqa: F401
