"""Remove CAN clusters from a system description (network ARXML) together with what only they use.

    python remove_can_clusters.py network.arxml --list
    python remove_can_clusters.py network.arxml CAN0 CAN2 -o network_without_CAN0_CAN2.arxml [--dry-run]

A cluster is given by its short name, its short name without "_Cluster", the name of its physical channel or its
path (case is ignored). Removed with it, repeated until nothing changes:

* the CAN cluster with its physical channels (frame / PDU / signal triggerings) and the CAN connectors of the ECUs
  on them (ports included)
* elements whose defining reference points to something removed: CAN connectors (controller), PDU-TO-FRAME and
  I-SIGNAL-TO-I-PDU mappings, PDU triggerings of other channels (e.g. Ethernet) carrying a removed PDU, socket
  connection PDU identifiers of those triggerings, secured PDUs of a removed payload, gateway I-PDU-MAPPINGs, SYSTEM
  data mappings, FIBEX element references, PDU group / port / connector references ...
* gateway partners: a gateway I-PDU-MAPPING that loses one end takes the PDU triggering at its other end (on a
  bus / Ethernet channel that stays) when no other mapping uses it - the routing to / from the removed bus
  (--keep-partners: keep them)
* elements nobody uses any more (they were used before): PDU / signal triggerings, CAN frames, PDUs, I-SIGNALs,
  I-SIGNAL groups, SYSTEM signals (groups), frame / I-PDU / I-SIGNAL ports, CAN controllers, frame triggerings left
  without PDU triggering and I-SIGNAL-I-PDU groups left without PDU. References inside the SYSTEM (FIBEX elements,
  mappings), inside PDU groups and gateway mappings do not keep anything alive.

Everything else (ECUs, other clusters, data types, compu methods ...) is kept; references to elements outside the
file are not touched. The report lists every removed element that is not part of a CAN cluster, to be checked.
"""
from __future__ import annotations

import argparse
import collections
import os
import re
import sys

from lxml import etree

# removed when nothing (but weak references) refers to them any more and something did before
GC_TYPES = {"PDU-TRIGGERING", "I-SIGNAL-TRIGGERING", "CAN-FRAME", "I-SIGNAL", "I-SIGNAL-GROUP", "SYSTEM-SIGNAL",
            "SYSTEM-SIGNAL-GROUP", "FRAME-PORT", "I-PDU-PORT", "I-SIGNAL-PORT", "CAN-COMMUNICATION-CONTROLLER",
            "I-SIGNAL-I-PDU", "NM-PDU", "N-PDU", "DCM-I-PDU", "SECURED-I-PDU", "CONTAINER-I-PDU", "MULTIPLEXED-I-PDU", "GENERAL-PURPOSE-PDU",
            "GENERAL-PURPOSE-I-PDU", "USER-DEFINED-PDU", "USER-DEFINED-I-PDU", "XCP-PDU"}
# references inside these do not keep their target alive (lists of what exists / is switched together, gateway
# mappings: a mapping that loses one end goes)
WEAK_OWNERS = {"SYSTEM", "I-SIGNAL-I-PDU-GROUP", "GATEWAY"}
# never removed because of a dangling reference inside them (the reference itself is removed)
KEEP_TYPES = {"ECU-INSTANCE", "SYSTEM", "AR-PACKAGE", "GATEWAY", "ETHERNET-CLUSTER", "ETHERNET-PHYSICAL-CHANNEL",
              "CAN-CLUSTER", "CAN-PHYSICAL-CHANNEL", "LIN-CLUSTER", "LIN-PHYSICAL-CHANNEL", "FLEXRAY-CLUSTER",
              "FLEXRAY-PHYSICAL-CHANNEL", "SOCKET-ADDRESS", "SOCKET-CONNECTION-IPDU-IDENTIFIER-SET",
              "STATIC-SOCKET-CONNECTION", "ETHERNET-COMMUNICATION-CONNECTOR", "ETHERNET-COMMUNICATION-CONTROLLER",
              "NETWORK-ENDPOINT", "SYSTEM-MAPPING"}


def local(el) -> str:
    return etree.QName(el).localname


def short_name(el) -> str | None:
    sn = el.find("{*}SHORT-NAME")
    return sn.text.strip() if sn is not None and sn.text else None


class Network:
    def __init__(self, path: str):
        self.path = path
        parser = etree.XMLParser(remove_blank_text=False, huge_tree=True)
        self.tree = etree.parse(path, parser)
        self.root = self.tree.getroot()
        self.reindex()
        self.original = dict(self.by_path)              # path -> element of the file as read

    def reindex(self):
        self.by_path, self.path_of = {}, {}

        def walk(el, p):
            n = short_name(el)
            if n:
                p = f"{p}/{n}"
                self.by_path[p] = el
                self.path_of[el] = p
            for c in el:
                if isinstance(c.tag, str):
                    walk(c, p)
        walk(self.root, "")

    def refs(self):
        """(reference element, target path) of every reference in the file."""
        for el in self.root.iter():
            if isinstance(el.tag, str) and el.get("DEST") is not None and el.text:
                yield el, el.text.strip()

    def weak(self, el) -> bool:
        """Reference inside the SYSTEM, a PDU group or a GATEWAY: it does not keep its target alive."""
        p = el
        while p is not None:
            if isinstance(p.tag, str) and local(p) in WEAK_OWNERS:
                return True
            p = p.getparent()
        return False

    # ------------------------------------------------------------------ clusters
    def can_clusters(self) -> list[tuple[str, list[str]]]:
        out = []
        for p, el in self.by_path.items():
            if local(el) == "CAN-CLUSTER":
                chans = [self.path_of[c] for c in el.iter("{*}CAN-PHYSICAL-CHANNEL") if c in self.path_of]
                out.append((p, chans))
        return sorted(out)

    def find_cluster(self, name: str) -> str | None:
        n = name.strip().lower()
        for p, chans in self.can_clusters():
            sn = p.rsplit("/", 1)[-1].lower()
            if n in (p.lower(), sn, sn.removesuffix("_cluster")) or \
                    any(n in (c.lower(), c.rsplit("/", 1)[-1].lower()) for c in chans):
                return p
        return None

    def connectors_of(self, cluster: str) -> list[str]:
        el = self.by_path[cluster]
        return sorted({r.text.strip() for r in el.iter("{*}COMMUNICATION-CONNECTOR-REF") if r.text})

    # ------------------------------------------------------------------ removal
    def remove(self, el):
        parent = el.getparent()
        if parent is None:
            return
        prev = el.getprevious()
        if prev is not None:                            # keep the indentation of what follows
            prev.tail = el.tail
        else:
            parent.text = el.tail
        parent.remove(el)
        # a list left empty goes too (not an identifiable element, not an AR-PACKAGE's ELEMENTS / AR-PACKAGES)
        if len([c for c in parent if isinstance(c.tag, str)]) == 0 and parent not in self.path_of and \
                local(parent) not in ("AR-PACKAGES", "AUTOSAR"):
            self.remove(parent)

    def item_of(self, ref):
        """What goes when reference *ref* dangles: the identifiable element it defines, or its entry in a list."""
        node = ref
        while True:
            parent = node.getparent()
            if parent is None:
                return None
            if parent in self.path_of:                  # a direct reference of an identifiable element
                return node if local(parent) in KEEP_TYPES else parent
            if local(parent).endswith("S") and not local(parent).endswith("-CONDITIONAL"):
                return node                             # entry of a list (…-REFS, FIBEX-ELEMENTS, I-PDU-MAPPINGS …)
            node = parent

    @staticmethod
    def in_mapping(el) -> bool:
        p = el
        while p is not None:
            if isinstance(p.tag, str) and local(p) == "I-PDU-MAPPING":
                return True
            p = p.getparent()
        return False

    def strong_refs(self) -> collections.Counter:
        return collections.Counter(t for r, t in self.refs() if not self.weak(r))

    def run(self, clusters: list[str], partners: bool = True) -> list[tuple[str, str]]:
        """Remove *clusters* and what goes with them; returns (type, path) of every removed element."""
        before = self.strong_refs()
        had = {p for p, el in self.by_path.items() if (local(el) == "I-SIGNAL-I-PDU-GROUP" and el.find(
            ".//{*}I-SIGNAL-I-PDU-REF") is not None) or (local(el).endswith("FRAME-TRIGGERING") and el.find(
            ".//{*}PDU-TRIGGERING-REF") is not None)}
        seed = list(clusters)
        for c in clusters:                              # the CAN connectors of the ECUs on its channels
            for r in self.by_path[c].iter("{*}COMMUNICATION-CONNECTOR-REF"):
                el = self.by_path.get((r.text or "").strip())
                if el is not None and local(el) == "CAN-COMMUNICATION-CONNECTOR":
                    seed.append(self.path_of[el])
        for p in dict.fromkeys(seed):
            if self.by_path[p].getparent() is not None:
                self.remove(self.by_path[p])
        ends = set()                                    # PDU triggerings of gateway mappings that lost the other end
        while True:
            self.reindex()
            changed = False
            gone = {p for p in self.original if p not in self.by_path}
            for ref, target in list(self.refs()):
                if target in gone and ref.getparent() is not None:
                    item = self.item_of(ref)
                    if item is not None and item.getparent() is not None:
                        if local(item) == "I-PDU-MAPPING":
                            ends |= {(r.text or "").strip() for r in item.iter() if isinstance(r.tag, str)
                                     and r.get("DEST") == "PDU-TRIGGERING"}
                        self.remove(item)
                        changed = True
            if changed:
                continue
            if partners and ends:
                mapped = {t for r, t in self.refs() if self.in_mapping(r)}
                for pt in ends:
                    el = self.by_path.get(pt)
                    if el is not None and pt not in mapped and el.getparent() is not None:
                        self.remove(el)
                        changed = True
                ends.clear()
                if changed:
                    continue
            now = self.strong_refs()
            for p, el in list(self.by_path.items()):
                t = local(el)
                orphan = t in GC_TYPES and before[p] and not now[p]
                empty = p in had and (
                    t == "I-SIGNAL-I-PDU-GROUP" and el.find(".//{*}I-SIGNAL-I-PDU-REF") is None
                    and el.find(".//{*}CONTAINED-I-SIGNAL-I-PDU-GROUP-REF") is None
                    or t.endswith("FRAME-TRIGGERING") and el.find(".//{*}PDU-TRIGGERING-REF") is None)
                if (orphan or empty) and el.getparent() is not None:
                    self.remove(el)
                    changed = True
            if not changed:
                break
        self.reindex()
        return sorted((local(el), p) for p, el in self.original.items() if p not in self.by_path)

    def save(self, out: str):
        """Write like the input: its XML declaration, line ends (CRLF / LF) and final line end."""
        with open(self.path, "rb") as fh:
            head = fh.read(4096)
            fh.seek(0, os.SEEK_END)
            fh.seek(max(fh.tell() - 2, 0))
            tail = fh.read()
        m = re.match(rb"\s*(<\?xml[^>]*\?>)", head)
        decl = m.group(1) if m else b'<?xml version="1.0" encoding="UTF-8"?>'
        body = etree.tostring(self.tree, encoding="UTF-8", xml_declaration=False)
        data = decl + b"\n" + body + (b"\n" if tail.endswith(b"\n") else b"")
        if b"\r\n" in head:
            data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        with open(out, "wb") as fh:
            fh.write(data)


def inside(path: str, roots: list[str]) -> bool:
    return any(path == r or path.startswith(r + "/") for r in roots)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Remove CAN clusters (and what only they use) from a network ARXML.")
    ap.add_argument("network", help="system description (.arxml)")
    ap.add_argument("clusters", nargs="*", help="CAN clusters: short name, name without _Cluster, channel or path")
    ap.add_argument("-o", "--output", help="output file (default: <network>_without_<clusters>.arxml)")
    ap.add_argument("--list", action="store_true", help="list the CAN clusters of the file")
    ap.add_argument("--dry-run", action="store_true", help="only report what would be removed")
    ap.add_argument("--keep-partners", action="store_true",
                    help="keep the PDU triggerings on other buses / Ethernet that were only routed to / from the "
                         "removed clusters (gateway I-PDU-MAPPING partners)")
    a = ap.parse_args(argv)
    net = Network(a.network)
    if a.list or not a.clusters:
        for p, chans in net.can_clusters():
            conns = net.connectors_of(p)
            ecus = sorted({c.rsplit("/", 1)[0].rsplit("/", 1)[-1] for c in conns})
            print(f"{p.rsplit('/', 1)[-1]:<24} {', '.join(c.rsplit('/', 1)[-1] for c in chans):<24} "
                  f"ECUs: {', '.join(ecus) or '-'}")
        return 0 if a.list else 2
    paths, bad = [], []
    for n in a.clusters:
        p = net.find_cluster(n)
        (paths if p else bad).append(p or n)
    if bad:
        print(f"Not a CAN cluster of the file: {', '.join(bad)} (see --list)", file=sys.stderr)
        return 2
    removed = net.run(paths, partners=not a.keep_partners)
    counts = collections.Counter(t for t, _ in removed)
    print(f"Removed {len(removed)} element(s) with {', '.join(p.rsplit('/', 1)[-1] for p in paths)}:")
    for t, n in counts.most_common():
        print(f"  {n:6d}  {t}")
    outside = [(t, p) for t, p in removed if not inside(p, paths)
               and t not in ("CAN-FRAME", "FRAME-PORT", "I-PDU-PORT", "I-SIGNAL-PORT", "PDU-TO-FRAME-MAPPING",
                             "I-SIGNAL-TO-I-PDU-MAPPING", "I-SIGNAL", "SYSTEM-SIGNAL", "I-SIGNAL-I-PDU",
                             "CAN-COMMUNICATION-CONTROLLER", "CAN-COMMUNICATION-CONNECTOR")]
    if outside:
        print("Also removed (check them):")
        for t, p in outside:
            print(f"  {t:<34} {p}")
    if a.dry_run:
        return 0
    out = a.output or (os.path.splitext(a.network)[0] + "_without_" +
                       "_".join(p.rsplit("/", 1)[-1].removesuffix("_Cluster") for p in paths) + ".arxml")
    if os.path.abspath(out) == os.path.abspath(a.network):
        print("The output must not overwrite the input file.", file=sys.stderr)
        return 2
    net.save(out)
    print(f"Written {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
