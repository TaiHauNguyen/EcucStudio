"""Read a CAN database (DBC) into the small model the gateway generator needs."""
from __future__ import annotations

import codecs
import os
from dataclasses import dataclass, field

# placeholder node Vector tools write for "no receiver"
DUMMY_NODES = {"Vector__XXX", "VECTOR__XXX"}


@dataclass
class Signal:
    name: str
    start: int                  # DBC start bit (= AUTOSAR START-POSITION, as Vector's converter does)
    length: int                 # bits
    little_endian: bool
    signed: bool = False
    is_float: bool = False
    initial: float = 0          # raw initial value (GenSigStartValue)
    receivers: list[str] = field(default_factory=list)
    multiplexed: bool = False   # multiplexor or multiplexed signal
    send_type: str | None = None
    comment: str | None = None


@dataclass
class Message:
    name: str
    can_id: int
    extended: bool
    fd: bool
    length: int                 # bytes
    cycle_ms: int | None = None
    send_type: str | None = None
    senders: list[str] = field(default_factory=list)
    receivers: list[str] = field(default_factory=list)
    signals: list[Signal] = field(default_factory=list)
    nm: bool = False
    diag: bool = False
    comment: str | None = None
    il_support: bool = True     # GenMsgILSupport: No -> the Vector converter imports it without PDU triggering

    @property
    def multiplexed(self) -> bool:
        return any(s.multiplexed for s in self.signals)

    @property
    def id_text(self) -> str:
        return f"0x{self.can_id:08X}" if self.extended else f"0x{self.can_id:03X}"


@dataclass
class Database:
    path: str
    name: str
    baudrate: int | None = None
    fd_baudrate: int | None = None
    fd_bus: bool = False
    nodes: list[str] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)
    node_ecus: dict[str, str] = field(default_factory=dict)     # node -> its "ECU" attribute (one ECU, several buses)

    def ecu_of(self, node: str) -> str:
        """ECU of *node* as the Vector DBC converter names it: the node's "ECU" attribute, else the node."""
        return self.node_ecus.get(node) or node

    def node_messages(self, node: str) -> tuple[list[Message], list[Message]]:
        """(received, transmitted) messages of *node*."""
        rx, tx = [], []
        for m in self.messages:
            if node in m.senders:
                tx.append(m)
            elif node in m.receivers:
                rx.append(m)
        return rx, tx

    @property
    def has_fd(self) -> bool:
        return self.fd_bus or any(m.fd for m in self.messages)


def _attr(owner, defs, name, default=None):
    """Value of DBC attribute *name* of *owner* (cantools object), enum values as text."""
    attrs = getattr(getattr(owner, "dbc", None), "attributes", None) or {}
    a = attrs.get(name)
    if a is not None:
        value, definition = a.value, a.definition
    else:
        definition = defs.get(name)
        if definition is None:
            return default
        value = definition.default_value
    if value is None:
        return default
    choices = getattr(definition, "choices", None)
    if choices and isinstance(value, int) and 0 <= value < len(choices):
        return choices[value]
    return value


def _yes(v) -> bool:
    return str(v).strip().lower() in ("yes", "true", "1")


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def read_text(path: str) -> str:
    """DBC text with the encoding detected: a BOM (UTF-8 / UTF-16, written by some editors and export tools),
    else UTF-8, else the Windows code page Vector tools use (cp1252)."""
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw.startswith(codecs.BOM_UTF8):
        return raw[len(codecs.BOM_UTF8):].decode("utf-8", errors="replace")
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return raw.decode("utf-16", errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def load(path: str) -> Database:
    try:
        import cantools
    except ImportError as exc:     # pragma: no cover - depends on the installation
        raise RuntimeError("Reading DBC files needs the 'cantools' package: pip install cantools") from exc
    text = read_text(path)
    try:
        db = cantools.database.load_string(text, database_format="dbc", strict=False)
    except Exception as exc:       # cantools raises its own ParseError types
        raise RuntimeError(f"{os.path.basename(path)} is not a valid DBC file: {exc}") from None
    defs = dict(getattr(db.dbc, "attribute_definitions", {}) or {}) if db.dbc else {}
    name = _attr(db, defs, "DBName") or os.path.splitext(os.path.basename(path))[0]
    bus_type = str(_attr(db, defs, "BusType", "") or "")
    baud = None
    for key in ("Baudrate", "BaudRate", "Bitrate"):
        baud = _int(_attr(db, defs, key))
        if baud:
            break
    fd_baud = None
    for key in ("BaudrateCANFD", "BaudRateCANFD", "DataBaudrate", "FDBaudrate", "BitrateFD"):
        fd_baud = _int(_attr(db, defs, key))
        if fd_baud:
            break
    out = Database(path=os.path.abspath(path), name=str(name), baudrate=baud, fd_baudrate=fd_baud,
                   fd_bus="FD" in bus_type.upper().replace(" ", ""),
                   nodes=[n.name for n in db.nodes if n.name not in DUMMY_NODES])
    for n in db.nodes:
        ecu = str(_attr(n, defs, "ECU", "") or "").strip()
        if ecu and n.name not in DUMMY_NODES:
            out.node_ecus[n.name] = ecu
    for m in db.messages:
        sigs = []
        for s in m.signals:
            raw_init = getattr(s, "raw_initial", None)
            if raw_init is None:
                raw_init = 0
            sigs.append(Signal(
                name=s.name, start=int(s.start), length=int(s.length),
                little_endian=(s.byte_order == "little_endian"), signed=bool(s.is_signed),
                is_float=bool(getattr(s, "is_float", False)), initial=raw_init,
                receivers=[r for r in (s.receivers or []) if r not in DUMMY_NODES],
                multiplexed=bool(s.is_multiplexer or s.multiplexer_ids),
                send_type=_attr(s, defs, "GenSigSendType"), comment=_comment(s.comment)))
        receivers = sorted({r for s in sigs for r in s.receivers} |
                           {r for r in (m.receivers or []) if r not in DUMMY_NODES})
        cycle = m.cycle_time if m.cycle_time else _int(_attr(m, defs, "GenMsgCycleTime"))
        out.messages.append(Message(
            name=m.name, can_id=int(m.frame_id), extended=bool(m.is_extended_frame), fd=bool(m.is_fd),
            length=int(m.length), cycle_ms=cycle or None, send_type=m.send_type or _attr(m, defs, "GenMsgSendType"),
            senders=[x for x in (m.senders or []) if x not in DUMMY_NODES], receivers=receivers, signals=sigs,
            nm=_yes(_attr(m, defs, "NmAsrMessage", "No")) or _yes(_attr(m, defs, "NmMessage", "No")),
            diag=any(_yes(_attr(m, defs, a, "No")) for a in ("DiagRequest", "DiagResponse", "DiagState",
                                                              "DiagUudtResponse", "DiagUUDTResponse")),
            comment=_comment(m.comment),
            il_support=str(_attr(m, defs, "GenMsgILSupport", "Yes")).strip().lower() not in ("no", "0", "false")))
    out.messages.sort(key=lambda x: (x.extended, x.can_id))
    return out


def _comment(c):
    if c is None:
        return None
    if isinstance(c, dict):        # multilingual comments
        return next(iter(c.values()), None)
    return str(c)
