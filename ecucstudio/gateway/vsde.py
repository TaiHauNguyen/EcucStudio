"""CAN -> CAN routes of DBC buses as a Vector System Description Extension file (.vsde).

When the DBC files are imported in DaVinci Configurator, the Vector DBC converter gives the ECU an I-SIGNAL-PORT for
every signal it receives / sends, so Com gets the PDU on both buses (CanIf -> PduR -> Com and Com -> PduR -> CanIf),
also when the PDU is only routed from one bus to the other. An additional input file cannot remove those ports.

The converter reads an extension file given next to the DBC files. A PDUR-MESSAGE-ROUTING in it routes a PDU of one
CAN cluster to a PDU of another one through PduR, and the ECU then receives / sends none of its signals:

    <EXTRACT-EXTENSION xmlns="http://www.vector-informatik.de/ExtractExtension">
      <GATEWAY-ROUTING>
        <PDUR-MESSAGE-ROUTING>
          <ECU-INSTANCE-REF>ECU</ECU-INSTANCE-REF>
          <SOURCE-CAN-CLUSTER-REF>BusA</SOURCE-CAN-CLUSTER-REF>      (DBName of the DBC)
          <TARGET-CAN-CLUSTER-REF>BusB</TARGET-CAN-CLUSTER-REF>
          <I-PDU-MAPPINGS>
            <I-PDU-MAPPING><SOURCE-I-PDU-REF>Msg</SOURCE-I-PDU-REF><TARGET-I-PDU-REF>Msg</TARGET-I-PDU-REF></I-PDU-MAPPING>

Checked with the converter of DaVinci 5.24: a routing whose ECU-INSTANCE-REF is the ECU (the "ECU" attribute of the
node, e.g. node Gw_BusA with ECU = "Gw") creates the GATEWAY / I-PDU-MAPPING and drops the signals of the buses on
which the node is named like the ECU; a routing whose ECU-INSTANCE-REF is a node name drops the signals of that node's
bus only. So a group gets one routing for the ECU plus one for each node named otherwise.

The gateway file therefore leaves those CAN -> CAN mappings out; the .vsde file is written next to it.
"""
from __future__ import annotations

import collections
import os
import re

from lxml import etree

NS = "http://www.vector-informatik.de/ExtractExtension"
_REF = re.compile(r"[a-zA-Z][a-zA-Z0-9_]{0,127}$")


def path_for(output: str) -> str:
    """The extension file that belongs to gateway file *output*."""
    return os.path.splitext(output)[0] + ".vsde"


def dbc_cluster(bus) -> str:
    """Cluster name of a bus whose channel the DBC converter made (/Cluster/<DBName>/CHNL), else ''."""
    name = bus.db.name if bus.db is not None else ""
    return name if name and bus.channel == f"/Cluster/{name}/CHNL" else ""


def converter_ecu(bus) -> str:
    """The ECU of the bus's gateway node as the DBC converter names it ("ECU" attribute of the node, else the node)."""
    node = bus.cfg.node or ""
    return bus.db.ecu_of(node) if bus.db is not None and node else node


def problem(cr) -> str:
    """Why CAN -> CAN route *cr* cannot be given to the converter ('' = it can)."""
    src, dst = dbc_cluster(cr.src.bus), dbc_cluster(cr.dst.bus)
    if not src or not dst:
        return "a bus is not imported from a DBC file"
    ecu, other = converter_ecu(cr.src.bus), converter_ecu(cr.dst.bus)
    if ecu != other:
        return f"the gateway node is ECU {ecu} in one DBC file and {other} in the other"
    for n in (ecu, cr.src.bus.cfg.node, cr.dst.bus.cfg.node, src, dst, cr.src.message.name, cr.dst.message.name):
        if not _REF.match(n or ""):
            return f"'{n}' is not a valid name for the DBC converter"
    return ""


def build(routes: list) -> bytes:
    """Extension file routing *routes* (CanRoute) through PduR of their gateway ECU."""
    groups = collections.OrderedDict()
    for cr in routes:
        groups.setdefault((converter_ecu(cr.src.bus), dbc_cluster(cr.src.bus), dbc_cluster(cr.dst.bus)),
                          []).append(cr)
    root = etree.Element(f"{{{NS}}}EXTRACT-EXTENSION", nsmap={None: NS})

    def sub(parent, tag, text=None):
        el = etree.SubElement(parent, f"{{{NS}}}{tag}")
        if text is not None:
            el.text = text
        return el

    if groups:
        gw = sub(root, "GATEWAY-ROUTING")
        for (ecu, src, dst), crs in groups.items():
            nodes = [n for n in dict.fromkeys((crs[0].src.bus.cfg.node, crs[0].dst.bus.cfg.node)) if n != ecu]
            for ref in [ecu] + nodes:
                r = sub(gw, "PDUR-MESSAGE-ROUTING")
                sub(r, "ECU-INSTANCE-REF", ref)
                sub(r, "SOURCE-CAN-CLUSTER-REF", src)
                sub(r, "TARGET-CAN-CLUSTER-REF", dst)
                maps = sub(r, "I-PDU-MAPPINGS")
                for cr in crs:
                    m = sub(maps, "I-PDU-MAPPING")
                    sub(m, "SOURCE-I-PDU-REF", cr.src.message.name)
                    sub(m, "TARGET-I-PDU-REF", cr.dst.message.name)
    etree.indent(root, space="  ")
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + etree.tostring(root, encoding="UTF-8") + b"\n"


def read(path: str) -> list[tuple[str, str, str, str, str]]:
    """[(ECU, source cluster, source PDU, target cluster, target PDU)] of the PduR message routings of *path* (a route
    once, with the ECU-INSTANCE-REF of its first routing)."""
    if not path or not os.path.isfile(path):
        return []
    try:
        root = etree.parse(path).getroot()
    except etree.XMLSyntaxError:
        return []
    out = {}
    for r in root.iter(f"{{{NS}}}PDUR-MESSAGE-ROUTING"):
        ecu = r.findtext(f"{{{NS}}}ECU-INSTANCE-REF") or ""
        src = r.findtext(f"{{{NS}}}SOURCE-CAN-CLUSTER-REF") or ""
        dst = r.findtext(f"{{{NS}}}TARGET-CAN-CLUSTER-REF") or ""
        for m in r.iter(f"{{{NS}}}I-PDU-MAPPING"):
            key = (src, m.findtext(f"{{{NS}}}SOURCE-I-PDU-REF") or "", dst,
                   m.findtext(f"{{{NS}}}TARGET-I-PDU-REF") or "")
            out.setdefault(key, (ecu,) + key)
    return list(out.values())


def triggerings(path: str) -> set[tuple[str, str]]:
    """{(source PDU triggering, target PDU triggering)} the converter creates from *path*."""
    return {(f"/Cluster/{s}/CHNL/PT_{sm}", f"/Cluster/{d}/CHNL/PT_{dm}") for _e, s, sm, d, dm in read(path)}
