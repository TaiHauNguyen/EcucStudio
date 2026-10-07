"""Routing table of a gateway (Excel .xlsx or CSV), given by the customer together with the DBC files.

One row routes one message (Routing Type 0) or one signal (Routing Type 1) from the network marked S to every network
marked D. The columns are found by their header text, so their order may change:

    Signal name | Src Message/PDU name | Src Protocol | Receive CAN ... PduID (Hex) | ... | Routing Type (Signal = 1,
    Message = 0) | HW-Accelerator (Yes = 1, No = 0) | <one column per network: S / D / empty> | Dest Signal name |
    Dest Message/PDU name | Dest Protocol | Transmit CAN ... PduID (Hex) | ...

The network columns are the ones between the Routing Type / HW-Accelerator columns and the first destination column
(their header is the network name, e.g. the bus name of a DBC file). Other columns are read but not used.
"""
from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass, field

MESSAGE, SIGNAL = "message", "signal"

# field -> header test (on the header lower case, letters and digits only)
_FIELDS = (
    ("dst_signal", lambda h: h.startswith(("destsignal", "destinationsignal", "dstsignal", "targetsignal"))),
    ("dst_msg", lambda h: h.startswith(("destmessage", "destinationmessage", "dstmessage", "destpdu",
                                        "targetmessage"))),
    ("dst_protocol", lambda h: h.startswith(("destprotocol", "destinationprotocol", "dstprotocol"))),
    ("dst_id", lambda h: h.startswith(("transmit", "destid", "dstid", "destcanid")) and "ip" != h[-2:]),
    ("signal", lambda h: h.startswith(("signalname", "srcsignal", "sourcesignal"))),
    ("src_msg", lambda h: h.startswith(("srcmessage", "sourcemessage", "srcpdu", "sourcepdu", "messagename"))),
    ("src_protocol", lambda h: h.startswith(("srcprotocol", "sourceprotocol"))),
    ("src_id", lambda h: h.startswith(("receive", "srcid", "sourceid", "srccanid")) and "pduid" in h or
     h.startswith(("srcid", "sourceid", "srccanid"))),
    ("routing", lambda h: h.startswith("routingtype")),
    ("hw", lambda h: h.startswith(("hwaccelerator", "hardwareaccelerator")) or "accelerator" in h),
)
_DEST = ("dst_signal", "dst_msg", "dst_protocol", "dst_id")
_NETWORK_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_\- ]{0,40}$")


def _key(text) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def _cell(v) -> str:
    """Cell value as text: whole numbers without '.0'."""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def parse_hex(text: str) -> int | None:
    """CAN id given in hex ('0x11c', '11C'; a number typed into the hex column counts as hex digits too)."""
    t = text.strip().lower()
    if not t:
        return None
    if t.startswith("0x"):
        t = t[2:]
    try:
        return int(t, 16)
    except ValueError:
        return None


def _flag(text: str) -> bool | None:
    t = text.strip().lower()
    if t in ("", "0", "no", "n", "false"):
        return False
    if t in ("1", "yes", "y", "true", "x"):
        return True
    return None


@dataclass
class TableRow:
    row: int                    # row number in the sheet / file (header row = its own number)
    index: str = ""             # the table's own row number (first column), if there is one
    signal: str = ""            # source signal (signal routing)
    src_msg: str = ""
    src_protocol: str = ""
    src_id: int | None = None
    routing: str = ""           # MESSAGE | SIGNAL
    hw: bool = False            # routed by a hardware accelerator (LLCE / PFE)
    source: str = ""            # network marked S
    dests: list[str] = field(default_factory=list)  # networks marked D
    dst_signal: str = ""
    dst_msg: str = ""
    dst_protocol: str = ""
    dst_id: int | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"row {self.row}" + (f" #{self.index}" if self.index and self.index != str(self.row) else "")

    @property
    def what(self) -> str:
        """'Msg' or 'Msg.Signal' of the source."""
        return f"{self.src_msg}.{self.signal}" if self.routing == SIGNAL and self.signal else self.src_msg

    @property
    def target_signal(self) -> str:
        return self.dst_signal or self.signal

    @property
    def target_msg(self) -> str:
        return self.dst_msg or self.src_msg


@dataclass
class RoutingTable:
    path: str
    sheet: str = ""
    networks: list[str] = field(default_factory=list)   # network columns, in table order
    rows: list[TableRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def network(self, name: str) -> str:
        """The network column called *name* (case-insensitive), '' when there is none."""
        k = _key(name)
        return next((n for n in self.networks if _key(n) == k), "")


_CACHE: dict = {}       # (path, mtime, size) -> RoutingTable: a network is planned ECU by ECU, twice


def read(path: str) -> RoutingTable:
    """Read a routing table (.xlsx / .xlsm, or .csv / .tsv / .txt with ; , or tab). Raises ValueError / OSError.
    The result is shared between callers while the file does not change: do not modify it."""
    st = os.stat(path)
    key = (os.path.abspath(path), st.st_mtime_ns, st.st_size)
    if key not in _CACHE:
        if len(_CACHE) > 8:
            _CACHE.clear()
        _CACHE[key] = _read(path)
    return _CACHE[key]


def _read(path: str) -> RoutingTable:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        sheets = _xlsx_sheets(path)
    elif ext == ".xls":
        raise ValueError("old Excel format (.xls): save the routing table as .xlsx or .csv")
    else:
        sheets = [("", _csv_rows(path))]
    for name, rows in sheets:
        found = _header_row(rows)
        if found is not None:
            return _parse(path, name, rows, found)
    raise ValueError(f"{os.path.basename(path)}: no header row with 'Routing Type', the message names and the "
                     f"network columns (S / D) was found")


def _xlsx_sheets(path: str) -> list[tuple[str, list[list[str]]]]:
    try:
        import openpyxl
    except ImportError as exc:         # pragma: no cover - depends on the installation
        raise RuntimeError("Reading .xlsx routing tables needs the 'openpyxl' package: pip install openpyxl "
                           "(or save the table as .csv)") from exc
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        return [(ws.title, [[_cell(v) for v in row] for row in ws.iter_rows(values_only=True)])
                for ws in wb.worksheets]
    finally:
        wb.close()


def _csv_rows(path: str) -> list[list[str]]:
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    first = text.split("\n", 1)[0]
    delim = "\t" if "\t" in first else (";" if first.count(";") >= first.count(",") else ",")
    return [[c.strip() for c in row] for row in csv.reader(text.splitlines(), delimiter=delim)]


def _columns(header: list[str]) -> dict:
    out = {}
    for i, h in enumerate(header):
        k = _key(h)
        if not k:
            continue
        for name, test in _FIELDS:
            if name not in out and test(k):
                out[name] = i
                break
    return out


def _sd_column(rows: list[list[str]], i: int) -> bool:
    values = {r[i].strip().upper() for r in rows if i < len(r)} - {""}
    return bool(values) and values <= {"S", "D"}


def _header_row(rows: list[list[str]]) -> int | None:
    for i, row in enumerate(rows[:30]):
        cols = _columns(row)
        if "routing" in cols and "src_msg" in cols and ("dst_msg" in cols or "dst_signal" in cols):
            return i
    return None


def _parse(path: str, sheet: str, rows: list[list[str]], h: int) -> RoutingTable:
    header = rows[h]
    cols = _columns(header)
    first = max(cols["routing"], cols.get("hw", -1)) + 1
    last = min(cols[k] for k in _DEST if k in cols)
    nets = [(i, header[i].strip()) for i in range(first, last) if header[i].strip()]
    if not nets:            # other column order: the unknown columns holding only S / D
        known = set(cols.values())
        nets = [(i, x.strip()) for i, x in enumerate(header)
                if x.strip() and i not in known and _sd_column(rows[h + 1:], i)]
    t = RoutingTable(path=os.path.abspath(path), sheet=sheet, networks=[n for _, n in nets])
    bad = [n for _, n in nets if not _NETWORK_NAME.match(n)]
    if bad:
        t.warnings.append(f"Network column(s) with an unusual name: {', '.join(bad)}")
    index_col = bool(header) and 0 not in cols.values() and _key(header[0]) in ("", "no", "index", "nr", "rowno")
    get = lambda r, k: r[cols[k]] if k in cols and cols[k] < len(r) else ""
    for n, r in enumerate(rows[h + 1:], start=h + 2):
        if not any(r):
            continue
        row = TableRow(row=n, index=r[0] if index_col and r else "", signal=get(r, "signal"),
                       src_msg=get(r, "src_msg"), src_protocol=get(r, "src_protocol"),
                       dst_signal=get(r, "dst_signal"), dst_msg=get(r, "dst_msg"),
                       dst_protocol=get(r, "dst_protocol"))
        rt = get(r, "routing").strip().lower()
        if rt in ("1", "signal", "sig", "s"):
            row.routing = SIGNAL
        elif rt in ("0", "message", "msg", "pdu", "m"):
            row.routing = MESSAGE
        else:
            row.problems.append(f"Routing Type '{get(r, 'routing')}' is not 1 (signal) or 0 (message)")
        hw = _flag(get(r, "hw"))
        if hw is None:
            row.problems.append(f"HW-Accelerator '{get(r, 'hw')}' is not 1 or 0")
        row.hw = bool(hw)
        for k in ("src_id", "dst_id"):
            text = get(r, k)
            v = parse_hex(text)
            if text and v is None:
                row.problems.append(f"{'Receive' if k == 'src_id' else 'Transmit'} id '{text}' is not hex")
            setattr(row, k, v)
        for i, net in nets:
            v = r[i].strip().upper() if i < len(r) else ""
            if v == "S":
                row.source = net if not row.source else row.source
                if row.source != net:
                    row.problems.append(f"several source networks (S): {row.source}, {net}")
            elif v == "D":
                row.dests.append(net)
            elif v:
                row.problems.append(f"'{r[i].strip()}' in column {net} (only S or D)")
        if not row.source:
            row.problems.append("no source network (S)")
        if not row.dests:
            row.problems.append("no destination network (D)")
        if not row.src_msg:
            row.problems.append("no source message name")
        if row.routing == SIGNAL and not row.signal:
            row.problems.append("signal routing without a signal name")
        t.rows.append(row)
    return t
