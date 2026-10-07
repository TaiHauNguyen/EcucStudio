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

CAN PDUs fed from Ethernet (ETH -> CAN): the ECU is their sender in the DBC, so Com sends them too and the CAN PDU has
two sources (PduR N:1, DaVinci validation errors PDUR13008 / COM02202). There is no Ethernet source in the extension
format; a routing from the PDU to itself (source = target cluster and PDU) with ECU-INSTANCE-REF = the node of that bus
drops the node's signals of the PDU, so Com does not send it, and creates no mapping. Checked with DaVinci 5.24: no Com
Tx I-PDU, the Ethernet -> CanIf routing path stays, the N:1 errors are gone. The converter logs "ECU <node> does not
receive source pdu <cluster>.<PDU>" (an error it ignores: the update finishes) when the node is named like the ECU.

Signal routes of a routing table (Com signal gateway) are COM-SIGNAL-ROUTING elements:

    <COM-SIGNAL-ROUTING>
      <ECU-INSTANCE-REF>ECU</ECU-INSTANCE-REF>
      <SOURCE-CAN-CLUSTER-REF>BusA</SOURCE-CAN-CLUSTER-REF><TARGET-CAN-CLUSTER-REF>BusB</TARGET-CAN-CLUSTER-REF>
      <SIGNAL-MAPPINGS>
        <SIGNAL-MAPPING>
          <SOURCE-I-PDU-REF>MsgA</SOURCE-I-PDU-REF><SOURCE-SIGNAL-REF>SigA</SOURCE-SIGNAL-REF>
          <TARGET-I-PDU-REF>MsgB</TARGET-I-PDU-REF><TARGET-SIGNAL-REF>SigB</TARGET-SIGNAL-REF>
        </SIGNAL-MAPPING>

Checked with DaVinci 5.24: the converter writes an I-SIGNAL-MAPPING (ST_SigA_oMsgA -> ST_SigB_oMsgB) into the GATEWAY
of the ECU (ECU-INSTANCE-REF = the ECU, also when its nodes are named per bus), the I-SIGNAL-PORTs stay, DaVinci makes
a ComGwMapping. The ECU must receive the source signal and send the target signal in the DBC files, else the converter
logs "ECU X does not receive source signal ..." and skips the mapping. A PDU routed as a whole (PDUR-MESSAGE-ROUTING)
whose signals are also signal-routed lists them in SOURCE-SIGNALS of every routing of it, so they stay in Com.
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


def signal_problem(sr) -> str:
    """Why signal route *sr* cannot be given to the converter ('' = it can)."""
    src, dst = dbc_cluster(sr.src.bus), dbc_cluster(sr.dst.bus)
    if not src or not dst:
        return "a bus is not imported from a DBC file"
    ecu, other = converter_ecu(sr.src.bus), converter_ecu(sr.dst.bus)
    if ecu != other:
        return f"the gateway node is ECU {ecu} in one DBC file and {other} in the other"
    for n in (ecu, src, dst, sr.src.message.name, sr.dst.message.name, sr.src_signal.name, sr.dst_signal.name):
        if not _REF.match(n or ""):
            return f"'{n}' is not a valid name for the DBC converter"
    return ""


def tx_problem(r) -> str:
    """Why Com cannot be kept from sending the CAN PDU of ETH -> CAN route *r* ('' = it can)."""
    cluster = dbc_cluster(r.bus)
    if not cluster:
        return "the bus is not imported from a DBC file"
    for n in (r.bus.cfg.node, cluster, r.message.name):
        if not _REF.match(n or ""):
            return f"'{n}' is not a valid name for the DBC converter"
    return ""


def build(routes: list, tx: list = (), signals: list = ()) -> bytes:
    """Extension file routing *routes* (CanRoute) through PduR of their gateway ECU; Com does not send the CAN PDUs
    of the ETH -> CAN routes *tx*; Com routes the *signals* (SignalRoute)."""
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

    own = collections.OrderedDict()
    for r in tx:
        own.setdefault((r.bus.cfg.node, dbc_cluster(r.bus)), []).append(r)
    sig = collections.OrderedDict()
    for sr in signals:
        sig.setdefault((converter_ecu(sr.src.bus), dbc_cluster(sr.src.bus), dbc_cluster(sr.dst.bus)), []).append(sr)
    if groups or own or sig:
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
                    if getattr(cr, "keep_signals", None):     # Com keeps them: they are signal-routed
                        keep = sub(m, "SOURCE-SIGNALS")
                        for s in cr.keep_signals:
                            sub(keep, "SYSTEM-SIGNAL-REF", s)
                    sub(m, "TARGET-I-PDU-REF", cr.dst.message.name)
        for (node, cluster), rs in own.items():         # PDU -> itself: the node does not send its signals
            r = sub(gw, "PDUR-MESSAGE-ROUTING")
            sub(r, "ECU-INSTANCE-REF", node)
            sub(r, "SOURCE-CAN-CLUSTER-REF", cluster)
            sub(r, "TARGET-CAN-CLUSTER-REF", cluster)
            maps = sub(r, "I-PDU-MAPPINGS")
            for x in rs:
                m = sub(maps, "I-PDU-MAPPING")
                sub(m, "SOURCE-I-PDU-REF", x.message.name)
                sub(m, "TARGET-I-PDU-REF", x.message.name)
        for (ecu, src, dst), srs in sig.items():          # Com signal gateway
            r = sub(gw, "COM-SIGNAL-ROUTING")
            sub(r, "ECU-INSTANCE-REF", ecu)
            sub(r, "SOURCE-CAN-CLUSTER-REF", src)
            sub(r, "TARGET-CAN-CLUSTER-REF", dst)
            maps = sub(r, "SIGNAL-MAPPINGS")
            for sr in srs:
                m = sub(maps, "SIGNAL-MAPPING")
                sub(m, "SOURCE-I-PDU-REF", sr.src.message.name)
                sub(m, "SOURCE-SIGNAL-REF", sr.src_signal.name)
                sub(m, "TARGET-I-PDU-REF", sr.dst.message.name)
                sub(m, "TARGET-SIGNAL-REF", sr.dst_signal.name)
    etree.indent(root, space="  ")
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + etree.tostring(root, encoding="UTF-8") + b"\n"


def read(path: str, tx: bool = False) -> list[tuple[str, str, str, str, str]]:
    """[(ECU, source cluster, source PDU, target cluster, target PDU)] of the PduR message routings of *path* (a route
    once, with the ECU-INSTANCE-REF of its first routing): the CAN -> CAN routes, or with *tx* the PDUs routed to
    themselves (ETH -> CAN PDUs that Com does not send)."""
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
            if (key[:2] == key[2:]) == tx:
                out.setdefault(key, (ecu,) + key)
    return list(out.values())


def triggerings(path: str) -> set[tuple[str, str]]:
    """{(source PDU triggering, target PDU triggering)} the converter creates from *path*."""
    return {(f"/Cluster/{s}/CHNL/PT_{sm}", f"/Cluster/{d}/CHNL/PT_{dm}") for _e, s, sm, d, dm in read(path)}
