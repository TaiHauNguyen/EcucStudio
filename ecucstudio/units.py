"""Physical unit display conversion (DaVinci: values are stored in DV:BaseUnit, shown in DV:Unit)."""
from __future__ import annotations

FACTORS = {
    # time, base SEC
    "NSEC": ("time", 1e-9), "USEC": ("time", 1e-6), "MSEC": ("time", 1e-3), "SEC": ("time", 1.0),
    # memory, base BYTE
    "BIT": ("mem", 1 / 8), "BYTE": ("mem", 1.0), "KBYTE": ("mem", 1024.0), "MBYTE": ("mem", 1024.0 ** 2),
    # frequency, base HZ
    "HZ": ("freq", 1.0), "KHZ": ("freq", 1e3), "MHZ": ("freq", 1e6), "GHZ": ("freq", 1e9),
}
LABEL = {"NSEC": "ns", "USEC": "µs", "MSEC": "ms", "SEC": "s", "BIT": "bit", "BYTE": "Byte",
         "KBYTE": "KB", "MBYTE": "MB", "HZ": "Hz", "KHZ": "kHz", "MHZ": "MHz", "GHZ": "GHz"}


def label(unit: str | None) -> str:
    return LABEL.get((unit or "").upper(), unit or "")


def convertible(base: str | None, unit: str | None) -> bool:
    b, u = FACTORS.get((base or "").upper()), FACTORS.get((unit or "").upper())
    return bool(b and u and b[0] == u[0] and base.upper() != unit.upper())


def _fmt(x: float) -> str:
    s = f"{x:.12g}"
    return s


def to_display(value: str, base: str | None, unit: str | None) -> str:
    """Stored value (base unit) -> display value (unit). Non numeric values pass through."""
    if not convertible(base, unit):
        return value
    try:
        v = float(value)
    except (TypeError, ValueError):
        return value
    return _fmt(v * FACTORS[base.upper()][1] / FACTORS[unit.upper()][1])


def to_stored(value: str, base: str | None, unit: str | None) -> str:
    if not convertible(base, unit):
        return value
    try:
        v = float(value)
    except (TypeError, ValueError):
        return value
    return _fmt(v * FACTORS[unit.upper()][1] / FACTORS[base.upper()][1])
