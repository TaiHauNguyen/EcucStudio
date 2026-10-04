"""What DaVinci Configurator creates when the DBC files of an ECU are imported, predicted from the DBC files.

A gateway file for an ECU whose DBC files are (or will be) imported in its DaVinci project must reference those
CAN elements instead of creating them again (DaVinci would report duplicates). The Vector DBC converter names them
(checked with a DaVinci 5.24 project):

    /Cluster/<Bus>/CHNL                                   CAN cluster and channel (Bus = DBName of the DBC)
    /Cluster/<Bus>/CHNL/FT_<msg>, /Cluster/<Bus>/CHNL/PT_<msg>   frame / PDU triggering
    /CanFrame/<msg>_o<Bus>, /PDU/<msg>_o<Bus>             frame, I-SIGNAL-I-PDU
    /Topology/HardwareComponents/<ECU>                    ECU instance (the ECU of the project)
    /Topology/HardwareComponents/<ECU>/CN_<Bus>           its CAN connector, ports FP_<msg>_Rx|Tx, PP_<msg>_Rx|Tx

imported_view() builds such a document in memory (only the messages the ECU sends / receives, without signals). The
planner uses it as base and writes only the new elements (Ethernet + gateway), like for a DaVinci project.
"""
from __future__ import annotations

import os

from ..arxml import q
from . import dbcread, xmlorder
from .base import DEFAULT_SCHEMA, Base, new_document
from .xmlorder import E, R, T

HW = "/Topology/HardwareComponents"


def channel_path(bus: str) -> str:
    return f"/Cluster/{bus}/CHNL"


def _package(base: Base, path: str):
    el = base.el(path)
    if el is not None:
        return el
    parent_path, name = path.rsplit("/", 1)
    parent = base.root if not parent_path else _package(base, parent_path)
    pkg = E("AR-PACKAGE", T("SHORT-NAME", name))
    xmlorder.insert(xmlorder.ensure(parent, "AR-PACKAGES"), pkg)
    base.register_tree(pkg, parent_path)
    return pkg


def _element(base: Base, pkg_path: str, el):
    pkg = _package(base, pkg_path)
    xmlorder.insert(xmlorder.ensure(pkg, "ELEMENTS"), el)
    base.register_tree(el, pkg_path)
    return el


def _attach(base: Base, parent_path: str, list_tags: tuple, el):
    parent = base.el(parent_path)
    for t in list_tags:
        parent = xmlorder.ensure(parent, t)
    xmlorder.insert(parent, el)
    if el.find(q("SHORT-NAME")) is not None:
        base.register_tree(el, parent_path)
    return el


def imported_view(buses: list[tuple[str, str]], ecu: str, schema: str = DEFAULT_SCHEMA, path: str = "imported.arxml",
                  dbc_cache: dict | None = None) -> Base:
    """[(DBC file, node of the ECU in it)] -> the CAN part DaVinci creates for ECU instance *ecu*."""
    cache = dbc_cache if dbc_cache is not None else {}
    base = new_document(path, ecu, schema)
    ecu_path = f"{HW}/{ecu}"
    for dbc, node in buses:
        key = os.path.abspath(dbc)
        if key not in cache:
            cache[key] = dbcread.load(dbc)
        db = cache[key]
        bus = db.name
        ch = channel_path(bus)
        conn = f"{ecu_path}/CN_{bus}"
        if base.el(f"/Cluster/{bus}") is None:
            channel = E("CAN-PHYSICAL-CHANNEL", T("SHORT-NAME", "CHNL"))
            cond = E("CAN-CLUSTER-CONDITIONAL", T("BAUDRATE", db.baudrate or 500000), E("PHYSICAL-CHANNELS", channel))
            _element(base, "/Cluster", E("CAN-CLUSTER", T("SHORT-NAME", bus), E("CAN-CLUSTER-VARIANTS", cond)))
            _attach(base, ch, ("COMM-CONNECTORS",), E("COMMUNICATION-CONNECTOR-REF-CONDITIONAL",
                                                       R("COMMUNICATION-CONNECTOR-REF", "CAN-COMMUNICATION-CONNECTOR",
                                                         conn)))
            _attach(base, ecu_path, ("CONNECTORS",), E("CAN-COMMUNICATION-CONNECTOR", T("SHORT-NAME", f"CN_{bus}")))
        rx, tx = db.node_messages(node)
        for m, direction, suffix in [(m, "IN", "Rx") for m in rx] + [(m, "OUT", "Tx") for m in tx]:
            frame, pdu = f"/CanFrame/{m.name}_o{bus}", f"/PDU/{m.name}_o{bus}"
            if base.el(f"{ch}/FT_{m.name}") is not None:
                continue
            _element(base, "/PDU", E("I-SIGNAL-I-PDU", T("SHORT-NAME", f"{m.name}_o{bus}"), T("LENGTH", m.length)))
            _element(base, "/CanFrame", E(
                "CAN-FRAME", T("SHORT-NAME", f"{m.name}_o{bus}"), T("FRAME-LENGTH", m.length),
                E("PDU-TO-FRAME-MAPPINGS", E("PDU-TO-FRAME-MAPPING", T("SHORT-NAME", f"{m.name}_o{bus}"),
                                             T("PACKING-BYTE-ORDER", "MOST-SIGNIFICANT-BYTE-LAST"),
                                             R("PDU-REF", "I-SIGNAL-I-PDU", pdu), T("START-POSITION", 0)))))
            fp, pp = f"FP_{m.name}_{suffix}", f"PP_{m.name}_{suffix}"
            _attach(base, conn, ("ECU-COMM-PORT-INSTANCES",),
                    E("FRAME-PORT", T("SHORT-NAME", fp), T("COMMUNICATION-DIRECTION", direction)))
            _attach(base, conn, ("ECU-COMM-PORT-INSTANCES",),
                    E("I-PDU-PORT", T("SHORT-NAME", pp), T("COMMUNICATION-DIRECTION", direction)))
            _attach(base, ch, ("PDU-TRIGGERINGS",), E(
                "PDU-TRIGGERING", T("SHORT-NAME", f"PT_{m.name}"),
                E("I-PDU-PORT-REFS", R("I-PDU-PORT-REF", "I-PDU-PORT", f"{conn}/{pp}")),
                R("I-PDU-REF", "I-SIGNAL-I-PDU", pdu)))
            fd = "CAN-FD" if m.fd else "CAN-20"
            _attach(base, ch, ("FRAME-TRIGGERINGS",), E(
                "CAN-FRAME-TRIGGERING", T("SHORT-NAME", f"FT_{m.name}"),
                E("FRAME-PORT-REFS", R("FRAME-PORT-REF", "FRAME-PORT", f"{conn}/{fp}")),
                R("FRAME-REF", "CAN-FRAME", frame),
                E("PDU-TRIGGERINGS", E("PDU-TRIGGERING-REF-CONDITIONAL",
                                       R("PDU-TRIGGERING-REF", "PDU-TRIGGERING", f"{ch}/PT_{m.name}"))),
                T("CAN-ADDRESSING-MODE", "EXTENDED" if m.extended else "STANDARD"),
                T("CAN-FRAME-RX-BEHAVIOR", fd), T("CAN-FRAME-TX-BEHAVIOR", fd), T("IDENTIFIER", m.can_id)))
    base._ft_index = None
    return base
