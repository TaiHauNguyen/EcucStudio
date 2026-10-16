"""Checks of a system description (network ARXML) for what makes the DaVinci project update fail.

    python check_network.py network.arxml [--ecu /Topology/HardwareComponents/MyEcu] [--base original.arxml]

* references whose target is not in the file (with --base: only the ones the original file did not have)
* PDU triggerings (of the ECU, with --ecu) whose PDU maps signals without an I-SIGNAL-TRIGGERING in that PDU
  triggering: DaVinci's "Create ECU configuration" (BaseEcuC) logs "ISignalTriggering not available" for each, and
  may stop with an internal error (NullPointerException)
* triggerings / mappings without their defining reference, frame triggerings without PDU triggering, CAN connectors
  without controller, ports nobody references, empty lists

Only lxml is needed. Exit code 1 when something was found.
"""
from __future__ import annotations

import argparse
import collections
import sys

from lxml import etree


def ln(el) -> str:
    return etree.QName(el).localname


class File:
    def __init__(self, path: str):
        self.root = etree.parse(path, etree.XMLParser(huge_tree=True)).getroot()
        self.by_path, self.path_of = {}, {}

        def walk(el, p):
            sn = el.find("{*}SHORT-NAME")
            if sn is not None and sn.text:
                p = p + "/" + sn.text.strip()
                self.by_path[p] = el
                self.path_of[el] = p
            for c in el:
                if isinstance(c.tag, str):
                    walk(c, p)
        walk(self.root, "")

    def owner(self, el) -> str:
        p = el
        while p is not None and p not in self.path_of:
            p = p.getparent()
        return self.path_of.get(p, "?")

    def dangling(self) -> list[tuple[str, str, str]]:
        return sorted({(self.owner(el), ln(el), el.text.strip()) for el in self.root.iter()
                       if isinstance(el.tag, str) and el.get("DEST") and el.text
                       and el.text.strip() not in self.by_path})

    def text(self, el, tag) -> str:
        return (el.findtext("{*}" + tag) or "").strip()

    def missing_signal_triggerings(self, ecu: str | None) -> list[tuple[str, str, list[str]]]:
        out = []
        for p, el in self.by_path.items():
            if ln(el) != "PDU-TRIGGERING":
                continue
            ports = [(r.text or "").strip() for r in el.iter("{*}I-PDU-PORT-REF")]
            if ecu and not any(x.startswith(ecu + "/") for x in ports):
                continue
            pdu = self.by_path.get(self.text(el, "I-PDU-REF"))
            if pdu is None:
                continue
            sigs = {self.text(m, "I-SIGNAL-REF") for m in pdu.iter("{*}I-SIGNAL-TO-I-PDU-MAPPING")} - {""}
            trig = set()
            for r in el.iter("{*}I-SIGNAL-TRIGGERING-REF"):
                st = self.by_path.get((r.text or "").strip())
                if st is not None:
                    trig.add(self.text(st, "I-SIGNAL-REF") or self.text(st, "I-SIGNAL-GROUP-REF"))
            miss = sorted(sigs - trig)
            if miss:
                out.append((p, self.path_of[pdu], miss))
        return out

    def structure(self) -> list[tuple[str, str]]:
        probs, t = [], self.text
        for p, el in self.by_path.items():
            k = ln(el)
            if k == "PDU-TRIGGERING" and not t(el, "I-PDU-REF"):
                probs.append(("PDU-TRIGGERING without I-PDU-REF", p))
            elif k.endswith("FRAME-TRIGGERING"):
                if not t(el, "FRAME-REF"):
                    probs.append(("frame triggering without FRAME-REF", p))
                if el.find(".//{*}PDU-TRIGGERING-REF") is None:
                    probs.append(("frame triggering without PDU triggering", p))
            elif k == "I-SIGNAL-TRIGGERING" and not (t(el, "I-SIGNAL-REF") or t(el, "I-SIGNAL-GROUP-REF")):
                probs.append(("I-SIGNAL-TRIGGERING without signal", p))
            elif k == "I-SIGNAL-TO-I-PDU-MAPPING" and not (t(el, "I-SIGNAL-REF") or t(el, "I-SIGNAL-GROUP-REF")):
                probs.append(("I-SIGNAL-TO-I-PDU-MAPPING without signal", p))
            elif k == "PDU-TO-FRAME-MAPPING" and not t(el, "PDU-REF"):
                probs.append(("PDU-TO-FRAME-MAPPING without PDU", p))
            elif k == "CAN-COMMUNICATION-CONNECTOR" and not t(el, "COMM-CONTROLLER-REF"):
                probs.append(("CAN connector without controller", p))
            elif k == "SECURED-I-PDU" and not t(el, "PAYLOAD-REF"):
                probs.append(("SECURED-I-PDU without payload", p))
            elif k == "SO-CON-I-PDU-IDENTIFIER" and not t(el, "PDU-TRIGGERING-REF"):
                probs.append(("SO-CON-I-PDU-IDENTIFIER without PDU triggering", p))
        used = {(e.text or "").strip() for e in self.root.iter()
                if isinstance(e.tag, str) and e.get("DEST") in ("FRAME-PORT", "I-PDU-PORT", "I-SIGNAL-PORT")}
        probs += [(f"{ln(el)} referenced by nobody", p) for p, el in self.by_path.items()
                  if ln(el) in ("FRAME-PORT", "I-PDU-PORT", "I-SIGNAL-PORT") and p not in used]
        probs += [(f"empty <{ln(el)}>", self.owner(el)) for el in self.root.iter()
                  if isinstance(el.tag, str) and el not in self.path_of and ln(el).endswith("S") and len(el) == 0
                  and not (el.text or "").strip()]
        return probs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Check a network ARXML for what makes the DaVinci update fail.")
    ap.add_argument("network")
    ap.add_argument("--ecu", help="ECU-INSTANCE path: only its PDU triggerings (ports) are checked for signals")
    ap.add_argument("--base", help="original file: report only dangling references it did not have")
    ap.add_argument("--max", type=int, default=30, help="lines per finding (default 30)")
    a = ap.parse_args(argv)
    f = File(a.network)
    found = 0
    d = f.dangling()
    if a.base:
        old = {t for _o, _r, t in File(a.base).dangling()}
        d = [x for x in d if x[2] not in old]
    print(f"References without target{' (new)' if a.base else ''}: {len(d)}")
    for o, r, t in d[:a.max]:
        print(f"  {o}  <{r}> -> {t}")
    found += len(d)
    m = f.missing_signal_triggerings(a.ecu)
    print(f"PDU triggerings with signals that have no I-SIGNAL-TRIGGERING (DaVinci 'ISignalTriggering not available'): "
          f"{len(m)}")
    for pt, pdu, miss in m[:a.max]:
        print(f"  {pt}\n      PDU {pdu}: {len(miss)} signal(s), e.g. {miss[0]}")
    found += len(m)
    s = f.structure()
    c = collections.Counter(k for k, _ in s)
    print(f"Structure: {sum(c.values())}" + (f" ({dict(c)})" if c else ""))
    for k, p in s[:a.max]:
        print(f"  {k}: {p}")
    found += len(s)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
