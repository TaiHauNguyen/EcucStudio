"""Low level AUTOSAR XML helpers: namespaces, AR paths, formatting-preserving I/O."""
from __future__ import annotations

import os
import re
import uuid

from lxml import etree

NS = "http://autosar.org/schema/r4.0"
_Q = "{%s}" % NS


def q(tag: str) -> str:
    """Qualified tag name in the AUTOSAR namespace."""
    return _Q + tag


def local(el) -> str:
    tag = el.tag
    if not isinstance(tag, str):
        return ""
    return tag[len(_Q):] if tag.startswith(_Q) else tag


def child(el, tag: str):
    return el.find(_Q + tag)


def children(el, tag: str):
    return el.findall(_Q + tag)


def text(el, tag: str, default=None):
    c = el.find(_Q + tag)
    if c is None or c.text is None:
        return default
    return c.text.strip()


def short_name(el) -> str | None:
    return text(el, "SHORT-NAME")


def ar_path(el) -> str:
    """AUTOSAR path of an identifiable element (walks SHORT-NAMEs of ancestors)."""
    parts = []
    while el is not None:
        sn = el.find(_Q + "SHORT-NAME")
        if sn is not None and sn.text:
            parts.append(sn.text.strip())
        el = el.getparent()
    return "/" + "/".join(reversed(parts))


def iter_identifiables(root, tags: set[str], prefix: str = ""):
    """Yield (path, element) for every descendant whose local tag is in *tags*.

    Path computation is done incrementally, so this is O(n) for the whole tree.
    """
    stack = [(root, prefix)]
    while stack:
        el, path = stack.pop()
        sn = el.find(_Q + "SHORT-NAME")
        if sn is not None and sn.text:
            path = path + "/" + sn.text.strip()
            if local(el) in tags:
                yield path, el
        for c in reversed(el):
            if isinstance(c.tag, str) and c.tag != _Q + "SHORT-NAME":
                stack.append((c, path))


def new_uuid() -> str:
    return str(uuid.uuid4())


# ----------------------------------------------------------------------------
# Formatting preserving insertion
# ----------------------------------------------------------------------------

def _line_indent(ws: str | None) -> str:
    if not ws:
        return ""
    i = ws.rfind("\n")
    return ws[i + 1:] if i >= 0 else ""


def indent_of(el) -> str:
    """Whitespace that precedes *el* on its line."""
    prev = el.getprevious()
    if prev is not None:
        return _line_indent(prev.tail)
    parent = el.getparent()
    if parent is None:
        return ""
    return _line_indent(parent.text)


def indent_step(el) -> str:
    """Guess the indentation unit used in the document of *el*."""
    parent = el.getparent()
    if parent is not None:
        a, b = indent_of(parent), indent_of(el)
        if len(b) > len(a) and b.startswith(a):
            return b[len(a):]
    return "  "


def _pretty(el, level: str, step: str):
    kids = [c for c in el if isinstance(c.tag, str)]
    if not kids:
        return
    el.text = "\n" + level + step
    for i, c in enumerate(kids):
        _pretty(c, level + step, step)
        c.tail = "\n" + level + step if i < len(kids) - 1 else "\n" + level


def insert_child(parent, new, index: int | None = None, step: str | None = None):
    """Insert *new* into *parent* at *index* keeping the document's pretty print."""
    kids = [c for c in parent if isinstance(c.tag, str)]
    p_indent = indent_of(parent)
    if step is None:
        step = indent_step(kids[0]) if kids else indent_step(parent)
        if not step or step.strip():
            step = "  "
    c_indent = p_indent + step
    _pretty(new, c_indent, step)
    if index is None or index >= len(kids):
        if kids:
            last = kids[-1]
            last.tail = "\n" + c_indent
            last.addnext(new)
        else:
            parent.text = "\n" + c_indent
            parent.append(new)
        new.tail = "\n" + p_indent
    else:
        ref = kids[index]
        ref.addprevious(new)
        new.tail = "\n" + c_indent
    return new


def remove_child(el):
    """Remove *el* while keeping the surrounding whitespace consistent."""
    parent = el.getparent()
    prev = el.getprevious()
    tail = el.tail
    if el.getnext() is None and prev is not None:
        prev.tail = tail
    elif el.getnext() is None and prev is None:
        parent.text = tail
    parent.remove(el)


def make(tag: str, text_value: str | None = None, attrib: dict | None = None):
    el = etree.Element(q(tag), attrib or {}, nsmap=None)
    if text_value is not None:
        el.text = text_value
    return el


def sub(parent, tag: str, text_value: str | None = None, attrib: dict | None = None):
    el = etree.SubElement(parent, q(tag), attrib or {})
    if text_value is not None:
        el.text = text_value
    return el


# ----------------------------------------------------------------------------
# File I/O that keeps the original header and line endings
# ----------------------------------------------------------------------------

_DECL = re.compile(rb"^\s*(<\?xml[^>]*\?>)")


class XmlFile:
    """An ARXML file loaded with lxml that can be written back byte-faithfully."""

    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        with open(self.path, "rb") as fh:
            raw = fh.read()
        m = _DECL.match(raw)
        self.decl = m.group(1).decode("ascii") if m else '<?xml version="1.0" encoding="UTF-8"?>'
        self.bom = raw.startswith(b"\xef\xbb\xbf")
        self.crlf = b"\r\n" in raw[:4096]
        self.trailing_newline = raw.endswith(b"\n")
        parser = etree.XMLParser(remove_blank_text=False, huge_tree=True, resolve_entities=False)
        self.tree = etree.ElementTree(etree.fromstring(raw, parser))
        self.dirty = False
        self.mtime = os.path.getmtime(self.path)

    @property
    def root(self):
        return self.tree.getroot()

    def to_bytes(self) -> bytes:
        # Serialising the ElementTree (not the root) keeps comments/PIs before the root.
        body = etree.tostring(self.tree, encoding="UTF-8", xml_declaration=False)
        out = self.decl.encode("ascii") + b"\n" + body
        if self.trailing_newline:
            out += b"\n"
        if self.crlf:
            out = out.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        if self.bom:
            out = b"\xef\xbb\xbf" + out
        return out

    def save(self, backup: bool = True):
        data = self.to_bytes()
        if backup and os.path.exists(self.path):
            bak = self.path + ".bak"
            try:
                if os.path.exists(bak):
                    os.remove(bak)
                os.replace(self.path, bak)
            except OSError:
                pass
        tmp = self.path + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, self.path)
        self.dirty = False
        self.mtime = os.path.getmtime(self.path)
