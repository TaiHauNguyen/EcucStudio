"""CAPL test node (CANoe) for the ETH -> CAN routes of a gateway plan.

The node plays the Ethernet node that feeds the gateway ECU (for example the central computer): it opens a UDP
socket on that node's address / port and sends every Ethernet PDU of the ETH -> CAN routes to the ECU's socket,
with the SoAd PDU header the generator configures (header id: 4 bytes, length: 4 bytes, big endian) and the
payload of the DBC initial values. The CAN frames the gateway sends are checked by eye in the CANoe Trace window.

One .can file per Ethernet source node (the default peer and every other peer that feeds ETH -> CAN routes). An
Ethernet PDU forwarded to several buses (1:N) is sent once.
"""
from __future__ import annotations

import datetime
import os
import re
from dataclasses import dataclass, field

from .planner import ETH_TO_CAN, Plan, Route

HEADER_LEN = 8                  # SoAd PDU header: header id (32 bit) + length (32 bit), big endian
DEFAULT_CYCLE_MS = 100          # cyclic sending of a PDU without cycle time in the DBC


@dataclass
class CaplPdu:
    name: str                   # Ethernet PDU
    header_id: int
    length: int
    cycle_ms: int
    payload: bytes
    targets: list[str]          # "Bus/Message (0x123)" the gateway sends it as
    message: str


@dataclass
class CaplNode:
    peer: str                   # Ethernet node played by the script
    peer_ip: str
    peer_port: int | None
    ecu: str
    ecu_ip: str
    ecu_port: int | None
    vlan: int | None
    protocol: str
    pdus: list[CaplPdu] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    tick_ms: int = 10           # cyclic sending tick = collection window in batch mode
    datagram_max: int = 1472    # bytes of one collected UDP datagram (PDU headers included)


# ---------------------------------------------------------------------------- payload
def pack(signals, length: int) -> bytes:
    """PDU bytes with the raw value of every signal (DBC start bit / byte order, as the DBC converter maps them)."""
    data = bytearray(length)
    for s in signals:
        if s.multiplexed or s.length <= 0:
            continue
        raw = int(round(float(s.initial or 0)))
        raw &= (1 << s.length) - 1                  # two's complement for negative raw values
        if s.little_endian:                          # Intel: start = LSB, bits go up
            positions = [s.start + i for i in range(s.length)]
        else:                                        # Motorola: start = MSB, sawtooth bit numbering
            positions, pos = [], s.start
            for _ in range(s.length):
                positions.append(pos)
                pos = pos + 15 if pos % 8 == 0 else pos - 1
            positions.reverse()                      # positions[i] holds bit i (LSB first)
        for i, pos in enumerate(positions):
            byte = pos // 8
            if 0 <= byte < length and (raw >> i) & 1:
                data[byte] |= 1 << (pos % 8)
    return bytes(data)


# ---------------------------------------------------------------------------- plan -> nodes
def _endpoint_ips(base) -> dict:
    out = {}
    if base is None:
        return out
    for ch in base.eth_channels():
        for e in ch.endpoints:
            out[e.path] = e.ip
        for s in ch.sockets:
            out[s.path] = (s.ip, s.port)
    return out


def eth_to_can_nodes(plan: Plan, base=None) -> list[CaplNode]:
    """One CaplNode per Ethernet node that feeds ETH -> CAN routes of *plan* (*base*: to find existing addresses)."""
    known = _endpoint_ips(base)
    ecu_ip = plan.ecu_ip or (known.get(plan.local_endpoint) if plan.local_endpoint else "") or ""
    members: dict = {}
    for r in plan.enabled_routes:
        if r.direction == ETH_TO_CAN and r.fanout_of is not None:
            members.setdefault(id(r.fanout_of), []).append(r)
    nodes: dict = {}
    for r in plan.enabled_routes:
        if r.direction != ETH_TO_CAN or r.fanout_of is not None:
            continue
        peer = r.peers[0] if r.peers else plan.default_peer
        node = nodes.get(peer)
        if node is None:
            sp = plan.sides.get((ETH_TO_CAN, peer))
            peer_ip = (sp.remote_ip if sp else "") or (known.get(sp.remote_endpoint) if sp else "") or ""
            peer_port = sp.remote_port if sp else None
            ecu_port = sp.local_port if sp else None
            if sp and peer_port is None and isinstance(known.get(sp.remote), tuple):
                peer_ip, peer_port = peer_ip or known[sp.remote][0] or "", known[sp.remote][1]
            if sp and ecu_port is None and isinstance(known.get(sp.local), tuple):
                ecu_port = known[sp.local][1]
            col = plan.cfg.ethernet.collection
            node = nodes[peer] = CaplNode(peer, peer_ip, peer_port, plan.ecu_name, ecu_ip, ecu_port, plan.eth_vlan,
                                          plan.cfg.ethernet.protocol)
            if col.enabled:             # the Ethernet node collects like the gateway ECU does
                node.tick_ms = max(1, int(round(col.timeout_ms)))
                node.datagram_max = max(HEADER_LEN + 64, min(int(col.buffer), 1472))
            for what, value in (("IP address of " + peer, peer_ip), ("IP address of " + plan.ecu_name, ecu_ip),
                                ("UDP port of " + peer, peer_port), ("UDP port of " + plan.ecu_name, ecu_port)):
                if value in (None, ""):
                    node.problems.append(f"{what} unknown: set it in the variables block")
            if node.protocol.upper() != "UDP":
                node.problems.append(f"the sockets use {node.protocol}: this script sends UDP only")
        targets = [r] + members.get(id(r), [])
        node.pdus.append(CaplPdu(
            r.eth_pdu, r.header_id, r.length, r.message.cycle_ms or DEFAULT_CYCLE_MS,
            pack(r.message.signals, r.length),
            [f"{t.bus.name}/{t.message.name} ({t.message.id_text})" for t in targets], r.message.name))
    return list(nodes.values())


# ---------------------------------------------------------------------------- CAPL text
def _c_str(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', "'") + '"'


def _ident(text: str) -> str:
    return re.sub(r"\W", "_", text)


def capl_text(node: CaplNode, source: str = "") -> str:
    n = len(node.pdus)
    max_len = max([p.length for p in node.pdus] + [1])
    names = ",\n    ".join(_c_str(p.name) for p in node.pdus)
    targets = ",\n    ".join(_c_str(", ".join(p.targets)) for p in node.pdus)
    hids = ", ".join(f"0x{p.header_id & 0xFFFFFFFF:08X}" for p in node.pdus)
    lens = ", ".join(str(p.length) for p in node.pdus)
    cycles = ", ".join(str(p.cycle_ms) for p in node.pdus)
    rows = ",\n    ".join("{" + ", ".join(f"0x{b:02X}" for b in (p.payload or b"\x00")) + "}" for p in node.pdus)
    table = "\n".join(f"//  {k:>3}  0x{p.header_id & 0xFFFFFFFF:08X}  {p.length:>3} B  {p.cycle_ms:>5} ms  "
                      f"{p.name}  ->  {', '.join(p.targets)}" for k, p in enumerate(node.pdus))
    problems = "".join(f"// !! {x}\n" for x in node.problems)
    when = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""/*@!Encoding:1252*/
// ============================================================================================================
// ETH -> CAN gateway test: this node plays the Ethernet node {node.peer} and sends the Ethernet PDUs of the
// ETH -> CAN routes of {node.ecu} (generated by EcucStudio {when}{', from ' + source if source else ''}).
//
// CANoe setup
//   - Simulation node on the Ethernet network, TCP/IP stack of the node (Node configuration -> TCP/IP Stack):
//     IP address {node.peer_ip or '?'}{f', VLAN {node.vlan}' if node.vlan is not None else ''}.
//   - The CAN channels of {node.ecu} in the Trace window: every PDU below must come out as the CAN frame(s) listed.
//
// Keys (measurement running)
//   'a'  send every PDU once           'n'  send the next PDU (index in the Write window)
//   'c'  cyclic sending on / off (DBC cycle time, {DEFAULT_CYCLE_MS} ms when none)
//   'p'  payload: DBC initial values / running counter in every byte (to see the data change on CAN)
//   'l'  list the PDUs in the Write window
//   'b'  send every PDU once, collected: as few UDP datagrams as possible ({node.datagram_max} bytes each)
//   'v'  cyclic sending collected on / off: every {node.tick_ms} ms the PDUs that are due leave in one datagram
//        (like an Ethernet node with PDU collection: the gateway then puts them on CAN at once)
//
// UDP datagram = SoAd PDU header (header id 4 bytes + length 4 bytes, big endian) + PDU payload.
//
//  idx  header id   len   cycle  Ethernet PDU  ->  CAN frame(s) sent by the gateway
{table}
{problems}// ============================================================================================================

includes
{{
}}

variables
{{
  // ---- addresses (from the gateway file; change them here if the network differs)
  char  kPeerIp[16] = {_c_str(node.peer_ip or "0.0.0.0")};    // this node ({node.peer})
  dword kPeerPort   = {node.peer_port if node.peer_port is not None else 0};
  char  kEcuIp[16]  = {_c_str(node.ecu_ip or "0.0.0.0")};    // gateway ECU {node.ecu}
  dword kEcuPort    = {node.ecu_port if node.ecu_port is not None else 0};

  // ---- Ethernet PDUs of the ETH -> CAN routes
  const int kCount  = {n};
  const int kMaxLen = {max_len};
  char  gName[{n}][64] = {{
    {names}
  }};
  char  gTarget[{n}][200] = {{
    {targets}
  }};
  dword gHeaderId[{n}] = {{ {hids} }};
  dword gLength[{n}] = {{ {lens} }};
  dword gCycleMs[{n}] = {{ {cycles} }};
  byte  gPayload[{n}][{max_len}] = {{
    {rows}
  }};

  // ---- state
  dword   gSocket = 0xFFFFFFFF;
  int     gNext = 0;
  int     gCyclic = 0;
  int     gCounterMode = 0;
  byte    gCounter = 0;
  dword   gDue[{n}];
  msTimer tCycle;
  const dword kTickMs = {node.tick_ms};
  const dword kDgramMax = {node.datagram_max};
  int     gBatch = 0;
  byte    gDgram[{node.datagram_max}];
  dword   gDgramLen = 0;
  int     gDgramPdus = 0;
}}

on start
{{
  char err[200];
  gSocket = UdpOpen(IpGetAddressAsNumber(kPeerIp), kPeerPort);
  if (gSocket == 0xFFFFFFFF)
    gSocket = UdpOpen(0, kPeerPort);              // any address of the node's TCP/IP stack
  if (gSocket == 0xFFFFFFFF)
  {{
    IpGetLastErrorAsString(err, elcount(err));
    write("ETH->CAN test: cannot open UDP %s:%d (%s). Check the TCP/IP stack of this node.", kPeerIp, kPeerPort, err);
    return;
  }}
  write("ETH->CAN test: %d PDU(s) from %s:%d to {node.ecu} %s:%d. Keys: a n c p l b v", kCount, kPeerIp, kPeerPort,
        kEcuIp, kEcuPort);
}}

on stopMeasurement
{{
  cancelTimer(tCycle);
  if (gSocket != 0xFFFFFFFF)
    UdpClose(gSocket);
}}

// SoAd header + payload of PDU i at buf[off]; returns the bytes written
dword PutPdu(int i, byte buf[], dword off)
{{
  dword len, k;
  len = gLength[i];
  buf[off + 0] = (gHeaderId[i] >> 24) & 0xFF;
  buf[off + 1] = (gHeaderId[i] >> 16) & 0xFF;
  buf[off + 2] = (gHeaderId[i] >> 8) & 0xFF;
  buf[off + 3] = gHeaderId[i] & 0xFF;
  buf[off + 4] = (len >> 24) & 0xFF;
  buf[off + 5] = (len >> 16) & 0xFF;
  buf[off + 6] = (len >> 8) & 0xFF;
  buf[off + 7] = len & 0xFF;
  for (k = 0; k < len; k++)
  {{
    if (gCounterMode) buf[off + {HEADER_LEN} + k] = gCounter;
    else buf[off + {HEADER_LEN} + k] = gPayload[i][k];
  }}
  return {HEADER_LEN} + len;
}}

void SendBuffer(byte buf[], dword len)
{{
  long rc;
  rc = UdpSendTo(gSocket, IpGetAddressAsNumber(kEcuIp), kEcuPort, buf, len);
  if (rc != 0 && IpGetLastError() != 0)
    write("ETH->CAN test: send failed (%d)", IpGetLastError());
}}

// one UDP datagram: SoAd header + payload of PDU i
void SendPdu(int i)
{{
  byte buf[{HEADER_LEN + max_len}];
  dword len;
  if (gSocket == 0xFFFFFFFF || i < 0 || i >= kCount)
    return;
  len = PutPdu(i, buf, 0);
  SendBuffer(buf, len);
}}

// collected datagram: PDUs are appended, a full datagram is sent before the next PDU
void DgramFlush()
{{
  if (gDgramLen == 0 || gSocket == 0xFFFFFFFF)
    return;
  SendBuffer(gDgram, gDgramLen);
  gDgramLen = 0;
  gDgramPdus = 0;
}}

void DgramAdd(int i)
{{
  if (gDgramLen + {HEADER_LEN} + gLength[i] > kDgramMax)
    DgramFlush();
  gDgramLen += PutPdu(i, gDgram, gDgramLen);
  gDgramPdus++;
}}

void SendAll()
{{
  int i;
  for (i = 0; i < kCount; i++)
    SendPdu(i);
  write("ETH->CAN test: sent %d PDU(s)", kCount);
}}

on key 'a'
{{
  if (gCounterMode) gCounter++;
  SendAll();
}}

on key 'n'
{{
  if (gCounterMode) gCounter++;
  SendPdu(gNext);
  write("ETH->CAN test: [%d] %s header 0x%08X -> %s", gNext, gName[gNext], gHeaderId[gNext], gTarget[gNext]);
  gNext = (gNext + 1) % kCount;
}}

on key 'c'
{{
  int i;
  gCyclic = !gCyclic;
  if (gCyclic)
  {{
    for (i = 0; i < kCount; i++)
      gDue[i] = 0;
    gBatch = 0;
    setTimer(tCycle, kTickMs);
  }}
  else
    cancelTimer(tCycle);
  if (gCyclic) write("ETH->CAN test: cyclic sending on");
  else write("ETH->CAN test: cyclic sending off");
}}

on key 'p'
{{
  gCounterMode = !gCounterMode;
  if (gCounterMode) write("ETH->CAN test: payload = running counter in every byte");
  else write("ETH->CAN test: payload = DBC initial values");
}}

on key 'b'
{{
  int i;
  if (gCounterMode) gCounter++;
  for (i = 0; i < kCount; i++)
    DgramAdd(i);
  DgramFlush();
  write("ETH->CAN test: sent %d PDU(s) collected (datagrams of max %d bytes)", kCount, kDgramMax);
}}

on key 'v'
{{
  int i;
  gBatch = !gBatch;
  gCyclic = gBatch;
  cancelTimer(tCycle);
  if (gBatch)
  {{
    for (i = 0; i < kCount; i++)
      gDue[i] = 0;
    setTimer(tCycle, kTickMs);
    write("ETH->CAN test: cyclic sending collected on (one datagram every %d ms)", kTickMs);
  }}
  else
    write("ETH->CAN test: cyclic sending collected off");
}}

on key 'l'
{{
  int i;
  for (i = 0; i < kCount; i++)
    write("[%d] %s header 0x%08X len %d cycle %d ms -> %s", i, gName[i], gHeaderId[i], gLength[i], gCycleMs[i],
          gTarget[i]);
}}

on timer tCycle
{{
  int i;
  if (gCounterMode) gCounter++;
  for (i = 0; i < kCount; i++)
  {{
    if (gDue[i] <= kTickMs)
    {{
      if (gBatch) DgramAdd(i);
      else SendPdu(i);
      gDue[i] = gCycleMs[i];
    }}
    else
      gDue[i] -= kTickMs;
  }}
  if (gBatch) DgramFlush();
  setTimer(tCycle, kTickMs);
}}
"""


def write_eth_to_can(plan: Plan, stem: str, base=None) -> tuple[list[str], list[str]]:
    """Write <stem>_eth_to_can_<peer>.can for every Ethernet source node. Returns (files, problems)."""
    files, problems = [], []
    source = os.path.basename(plan.cfg.output) if plan.cfg.output else ""
    for node in eth_to_can_nodes(plan, base):
        path = f"{stem}_eth_to_can_{_ident(node.peer)}.can"
        with open(path, "w", encoding="cp1252", errors="replace", newline="\r\n") as fh:
            fh.write(capl_text(node, source))
        files.append(path)
        problems += [f"{os.path.basename(path)}: {x}" for x in node.problems]
    return files, problems
