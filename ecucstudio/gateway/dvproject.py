"""Use a DaVinci Configurator project (.dpa) as the source instead of DBC files.

After the CAN databases were imported, DaVinci keeps the merged communication description of the project in
``Config/System/Communication.arxml`` (``<References><OEMCommunicationExtract>`` of the .dpa). The generator
reads ECU, CAN channels and messages from there and writes an *additional* input file (Ethernet + gateway
only) that references the CAN elements by the paths DaVinci uses, so the DBC files stay in the project.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from lxml import etree

from .. import arxml
from ..arxml import local, q
from . import dbcread
from .base import Base


@dataclass
class InputFile:
    path: str
    category: str
    ecu: str


@dataclass
class DvProject:
    path: str
    name: str = ""
    communication: str = ""          # merged communication description (Communication.arxml)
    ecu_path: str = ""               # ECU-INSTANCE the project is configured for
    system_path: str = ""
    inputs: list[InputFile] = field(default_factory=list)

    @property
    def dir(self) -> str:
        return os.path.dirname(self.path)

    @property
    def ecu_name(self) -> str:
        return self.ecu_path.rsplit("/", 1)[-1]

    def resolve(self, p: str) -> str:
        p = (p or "").replace("$(DpaProjectFolder)", self.dir)
        p = os.path.expandvars(p)
        return os.path.normpath(p if os.path.isabs(p) else os.path.join(self.dir, p))


def is_project(path: str) -> bool:
    return bool(path) and path.lower().endswith(".dpa")


def read(path: str) -> DvProject:
    path = os.path.abspath(path)
    root = etree.parse(path).getroot()
    p = DvProject(path=path, name=(root.findtext("General/Name") or os.path.splitext(os.path.basename(path))[0]))
    comm = (root.findtext("References/OEMCommunicationExtract") or "").strip()
    p.communication = p.resolve(comm or os.path.join("Config", "System", "Communication.arxml"))
    for m in root.iter("Path"):
        if m.get("Id") == "ECU-INSTANCE":
            p.ecu_path = m.get("ARPath", "")
        elif m.get("Id") == "SYSTEM":
            p.system_path = m.get("ARPath", "")
    for f in root.iter("File"):
        if f.text and f.get("FileCategory"):
            p.inputs.append(InputFile(p.resolve(f.text.strip()), f.get("FileCategory"), f.get("EcuInstance", "")))
    if not p.ecu_path:
        ecus = {i.ecu for i in p.inputs if i.ecu}
        if len(ecus) == 1:
            p.ecu_path = next(iter(ecus))       # a name only; matched by short name later
    return p


def load_communication(project: DvProject) -> Base:
    if not os.path.isfile(project.communication):
        raise RuntimeError(f"{project.communication} does not exist: import the CAN databases in DaVinci "
                           f"(Input Files, Update) and save the project first.")
    return Base(project.communication)


# ---------------------------------------------------------------------------- CAN messages of the ECU
def _num(text):
    try:
        return int(str(text).strip(), 0)
    except (TypeError, ValueError):
        try:
            return float(text)
        except (TypeError, ValueError):
            return None


def channel_database(base: Base, channel: str, ecu: str) -> dbcread.Database:
    """Messages the ECU sends / receives on a CAN channel of the project, in the DBC model of the generator."""
    ch = base.el(channel)
    if ch is None:
        raise ValueError(f"CAN channel {channel} is not in {os.path.basename(base.xf.path)}")
    cluster = base.owner_path(ch.getparent().getparent())
    bus = cluster.rsplit("/", 1)[-1] if cluster else channel.rsplit("/", 1)[-1]
    ecu_name = ecu.rsplit("/", 1)[-1]
    connector = base.ecu_connector_on(ecu, channel)
    cond = ch.getparent().getparent()
    db = dbcread.Database(path=base.xf.path, name=bus, nodes=[ecu_name],
                          baudrate=_num(arxml.text(cond, "BAUDRATE")),
                          fd_baudrate=_num(arxml.text(cond, "CAN-FD-BAUDRATE")))
    if not connector:
        return db
    suffix = re.compile(r"_o" + re.escape(bus) + r"$")
    for ft in ch.iter(q("CAN-FRAME-TRIGGERING")):
        _port, direction = base.port_of(ft, connector, "FRAME-PORT-REF")
        if direction not in ("IN", "OUT"):
            continue
        frame = base.el(base.ref(ft, "FRAME-REF"))
        name = arxml.short_name(frame) if frame is not None else arxml.short_name(ft)
        name = suffix.sub("", name)
        pts = base.refs(ft.find(q("PDU-TRIGGERINGS")), "PDU-TRIGGERING-REF")
        pdu = base.el(base.ref(base.el(pts[0]), "I-PDU-REF")) if pts and base.el(pts[0]) is not None else None
        kind = local(pdu) if pdu is not None else ""
        cycle = None
        if pdu is not None:
            per = pdu.find(".//" + q("TRANSMISSION-MODE-TRUE-TIMING") + "//" + q("TIME-PERIOD"))
            v = _num(per.findtext(q("VALUE"))) if per is not None else None
            cycle = int(round(v * 1000)) if v else None
        fd = ((arxml.text(ft, "CAN-FRAME-RX-BEHAVIOR") or arxml.text(ft, "CAN-FRAME-TX-BEHAVIOR") or "") == "CAN-FD"
              or (arxml.text(ft, "CAN-FD-FRAME-SUPPORT") or "").lower() == "true")
        sigs = []
        if kind == "I-SIGNAL-I-PDU":
            for m in pdu.iter(q("I-SIGNAL-TO-I-PDU-MAPPING")):
                sig = base.el(base.ref(m, "I-SIGNAL-REF"))
                if sig is None:
                    continue
                sigs.append(dbcread.Signal(
                    name=arxml.short_name(m), start=_num(arxml.text(m, "START-POSITION")) or 0,
                    length=_num(arxml.text(sig, "LENGTH")) or 0,
                    little_endian=(arxml.text(m, "PACKING-BYTE-ORDER") or "") != "MOST-SIGNIFICANT-BYTE-FIRST"))
        length = _num(arxml.text(frame, "FRAME-LENGTH")) if frame is not None else None
        db.messages.append(dbcread.Message(
            name=name, can_id=_num(arxml.text(ft, "IDENTIFIER")) or 0,
            extended=(arxml.text(ft, "CAN-ADDRESSING-MODE") or "").upper() == "EXTENDED", fd=fd,
            length=length or (_num(arxml.text(pdu, "LENGTH")) if pdu is not None else 0) or 0,
            cycle_ms=cycle, senders=[ecu_name] if direction == "OUT" else [],
            receivers=[ecu_name] if direction == "IN" else [], signals=sigs,
            nm=kind == "NM-PDU", diag=kind in ("DCM-I-PDU", "N-PDU")))
    db.messages.sort(key=lambda m: (m.extended, m.can_id))
    return db


def ecu_can_channels(base: Base, ecu: str) -> list[tuple[str, int, int]]:
    """(channel path, received, sent) for every CAN channel the ECU is connected to."""
    out = []
    for c in base.can_channels():
        if base.ecu_connector_on(ecu, c.path):
            db = channel_database(base, c.path, ecu)
            rx, tx = db.node_messages(ecu.rsplit("/", 1)[-1])
            out.append((c.path, len(rx), len(tx)))
    return out
