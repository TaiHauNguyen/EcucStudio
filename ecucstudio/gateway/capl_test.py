"""CANoe test module (CAPL) that checks every route of one gateway ECU on the bench.

CANoe plays the rest of the network: the Ethernet nodes (central computer, other zonal ECUs) and the senders on the
ECU's CAN buses. One test case per route, each with its own data pattern so a frame taken from the wrong route fails:

    CAN -> ETH   a CAN frame on the source bus      -> the Ethernet PDU (header id, length, data) at every receiver
    ETH -> CAN   a UDP datagram from the source node -> the CAN frame on every destination bus (1:N)
    CAN -> CAN   a CAN frame on the source bus      -> the same bytes on every destination bus
    signal       two values of the source signal    -> the same values in the destination signal (Com, cyclic)

The test module waits for the answers with text events (on message *, OnUdpReceiveFrom -> TestSupplyTextEvent) and
CANoe writes the pass / fail report. Requires CANoe 12+ with the TCP/IP stack of the test node holding the addresses
of the simulated Ethernet nodes (and the VLAN).
"""
from __future__ import annotations

import datetime
import os
import re
from dataclasses import dataclass, field

from .planner import CAN_TO_ETH, ETH_TO_CAN, Plan

HEADER_LEN = 8
TIMEOUT_MS = 300                # answer of the gateway (CAN -> ETH, ETH -> CAN, CAN -> CAN)
GAP_MS = 20                     # pause between two test cases


@dataclass
class _Sock:
    ip: str
    port: int
    label: str                  # "<node> receive" / "<node> send"


@dataclass
class TestModule:
    ecu: str
    ecu_ip: str
    buses: list[str] = field(default_factory=list)
    socks: list[_Sock] = field(default_factory=list)
    groups: list[tuple[str, str, list[str]]] = field(default_factory=list)   # (title, description, [case code])
    problems: list[str] = field(default_factory=list)
    counts: dict = field(default_factory=dict)

    def sock(self, ip: str, port: int, label: str) -> int:
        for i, s in enumerate(self.socks):
            if (s.ip, s.port) == (ip, port):
                return i
        self.socks.append(_Sock(ip, port, label))
        return len(self.socks) - 1


def _q(text: str) -> str:
    return '"' + str(text).replace("\\", "/").replace('"', "'") + '"'


def _ident(text: str) -> str:
    return re.sub(r"\W", "_", text)


def _sig_values(n: int) -> tuple[int, int]:
    """Two different raw values of an n-bit signal (n <= 32)."""
    if n == 1:
        return 1, 0
    mask = (1 << n) - 1
    return (0x55555555 & mask) or 1, (0x2AAAAAAA & mask) or 2


def build(plan: Plan) -> TestModule:
    """Test cases of the enabled routes of *plan*."""
    tm = TestModule(plan.ecu_name, plan.ecu_ip or "")
    one = plan.cfg is None or plan.cfg.ethernet.one_socket
    rx_label = lambda p: p if one else f"{p} (receive)"
    tx_label = lambda p: p if one else f"{p} (send)"
    if not tm.ecu_ip:
        tm.problems.append(f"IP address of {plan.ecu_name} unknown: set kEcuIp")
    bus_ix = {}

    def bus(bp) -> int:
        if bp.name not in bus_ix:
            bus_ix[bp.name] = len(tm.buses)
            tm.buses.append(bp.name)
        return bus_ix[bp.name]

    def side(direction, peer):
        sp = plan.sides.get((direction, peer))
        if sp is None or not sp.remote_ip or sp.remote_port is None:
            tm.problems.append(f"{direction} {peer}: address unknown, its routes are not tested")
            return None
        return sp

    def frame(r) -> str:
        m = r.message
        return f"{bus(r.bus)}, 0x{m.can_id:X}, {int(m.extended)}, {int(m.fd or r.length > 8)}, {r.length}"
    n = 0
    # ---------------------------------------------------------------- CAN -> ETH
    cases = []
    for r in plan.enabled_routes:
        if r.direction != CAN_TO_ETH or r.header_id < 0:
            continue
        mask, names = 0, []
        for p in r.peers:
            sp = side(CAN_TO_ETH, p)
            if sp is not None:
                mask |= 1 << tm.sock(sp.remote_ip, sp.remote_port, rx_label(p))
                names.append(p)
        if not mask:
            continue
        n += 1
        what = f"{r.bus.name}/{r.message.name} {r.message.id_text} -> {', '.join(names)} (0x{r.header_id:08X})"
        cases.append(f"""testcase TC{n:03d}_CanToEth_{_ident(r.bus.name)}_{_ident(r.message.name)}()
{{
  TestCaseTitle("{n:03d}", {_q(what)});
  CheckCanToEth({n}, {frame(r)}, 0x{r.header_id:08X}, 0x{mask:X}, {_q(what)});
}}""")
    if cases:
        tm.groups.append(("CAN -> Ethernet", "a frame on the CAN bus reaches every Ethernet receiver", cases))
    tm.counts["CAN -> ETH"] = len(cases)
    # ---------------------------------------------------------------- ETH -> CAN (1:N: every bus of the PDU)
    members = {}
    for r in plan.enabled_routes:
        if r.direction == ETH_TO_CAN and r.fanout_of is not None:
            members.setdefault(id(r.fanout_of), []).append(r)
    cases = []
    for r in plan.enabled_routes:
        if r.direction != ETH_TO_CAN or r.fanout_of is not None or r.header_id < 0 or not r.peers:
            continue
        sp = side(ETH_TO_CAN, r.peers[0])
        if sp is None or sp.local_port is None:
            continue
        k = tm.sock(sp.remote_ip, sp.remote_port, tx_label(r.peers[0]))
        targets = [r] + members.get(id(r), [])
        n += 1
        what = (f"{r.peers[0]} 0x{r.header_id:08X} -> " +
                ", ".join(f"{t.bus.name}/{t.message.name} {t.message.id_text}" for t in targets))
        exp = "".join(f"\n  ExpCan({bus(t.bus)}, 0x{t.message.can_id:X}, {int(t.message.extended)}, {t.length});"
                      for t in targets)
        cases.append(f"""testcase TC{n:03d}_EthToCan_{_ident(r.bus.name)}_{_ident(r.message.name)}()
{{
  TestCaseTitle("{n:03d}", {_q(what)});
  ExpClear();{exp}
  CheckEthToCan({n}, {k}, {sp.local_port}, 0x{r.header_id:08X}, {r.length}, {_q(what)});
}}""")
    if cases:
        tm.groups.append(("Ethernet -> CAN", "a PDU from the Ethernet node comes out on every CAN bus of it", cases))
    tm.counts["ETH -> CAN"] = len(cases)
    # ---------------------------------------------------------------- CAN -> CAN (message)
    by_src = {}
    for cr in plan.enabled_can_routes:
        by_src.setdefault(id(cr.src), []).append(cr)
    cases = []
    for crs in by_src.values():
        src = crs[0].src
        n += 1
        what = (f"{src.bus.name}/{src.message.name} {src.message.id_text} -> " +
                ", ".join(f"{c.dst.bus.name}/{c.dst.message.name} {c.dst.message.id_text}" for c in crs))
        exp = "".join(f"\n  ExpCan({bus(c.dst.bus)}, 0x{c.dst.message.can_id:X}, {int(c.dst.message.extended)}, "
                      f"{c.dst.length});" for c in crs)
        cases.append(f"""testcase TC{n:03d}_CanToCan_{_ident(src.bus.name)}_{_ident(src.message.name)}()
{{
  TestCaseTitle("{n:03d}", {_q(what)});
  ExpClear();{exp}
  CheckCanToCan({n}, {frame(src)}, {_q(what)});
}}""")
    if cases:
        tm.groups.append(("CAN -> CAN (message)", "the bytes of the frame are sent unchanged on the other bus",
                          cases))
    tm.counts["CAN -> CAN"] = len(cases)
    # ---------------------------------------------------------------- signal routes (Com signal gateway)
    cases = []
    for sr in plan.enabled_signal_routes:
        s, d = sr.src_signal, sr.dst_signal
        if s.length > 32 or d.length > 32:
            tm.problems.append(f"{sr.key}: signal longer than 32 bit, not tested")
            continue
        n += 1
        v1, v2 = _sig_values(min(s.length, d.length))
        wait = max(3 * (sr.dst.message.cycle_ms or 100), TIMEOUT_MS) + 200
        what = (f"{sr.src.bus.name}/{sr.src.message.name}.{s.name} -> {sr.dst.bus.name}/{sr.dst.message.name}."
                f"{d.name}")
        cases.append(f"""testcase TC{n:03d}_Signal_{_ident(sr.src.message.name)}_{_ident(s.name)}()
{{
  TestCaseTitle("{n:03d}", {_q(what)});
  CheckSignal({frame(sr.src)}, {s.start}, {s.length}, {int(s.little_endian)},
              {bus(sr.dst.bus)}, 0x{sr.dst.message.can_id:X}, {int(sr.dst.message.extended)}, {d.start}, {d.length},
              {int(d.little_endian)}, 0x{v1:X}, 0x{v2:X}, {wait}, {_q(what)});
}}""")
    if cases:
        tm.groups.append(("CAN -> CAN (signal)", "the value of the source signal appears in the destination signal",
                          cases))
    tm.counts["signal"] = len(cases)
    if len(tm.socks) > 8:
        tm.problems.append("more than 8 Ethernet sockets: only the first 8 are opened")
    return tm


# ---------------------------------------------------------------------------- CAPL text
_SEP = "// " + "=" * 108
_HELPERS = r"""
// ---------------------------------------------------------------------------- data pattern / signals
void Fill(int tc, int len)
{
  int j;
  for (j = 0; j < 64; j++)
    gData[j] = 0;
  for (j = 0; j < len; j++)
    gData[j] = (tc * 37 + j * 11 + 0x5A) & 0xFF;
}

void BitPos(int start, int len, int intel, int pos[])
{
  int i, p;
  int tmp[64];
  if (intel)
  {
    for (i = 0; i < len; i++)
      pos[i] = start + i;
    return;
  }
  p = start;                                   // Motorola: start = MSB, sawtooth bit numbering
  for (i = 0; i < len; i++)
  {
    tmp[i] = p;
    if (p % 8 == 0)
      p = p + 15;
    else
      p = p - 1;
  }
  for (i = 0; i < len; i++)
    pos[i] = tmp[len - 1 - i];
}

void SetBits(byte d[], int start, int len, int intel, dword v)
{
  int i, p;
  int pos[64];
  BitPos(start, len, intel, pos);
  for (i = 0; i < len; i++)
  {
    p = pos[i];
    if (p < 0 || p >= 512)
      continue;
    if ((v >> i) & 1)
      d[p / 8] = d[p / 8] | (1 << (p % 8));
    else
      d[p / 8] = d[p / 8] & ~(1 << (p % 8));
  }
}

dword GetBits(byte d[], int start, int len, int intel)
{
  int i, p;
  int pos[64];
  dword v;
  v = 0;
  BitPos(start, len, intel, pos);
  for (i = 0; i < len; i++)
  {
    p = pos[i];
    if (p >= 0 && p < 512 && ((d[p / 8] >> (p % 8)) & 1))
      v = v | ((dword)1 << i);
  }
  return v;
}

byte LenToDlc(int len)
{
  if (len <= 8) return len;
  if (len <= 12) return 9;
  if (len <= 16) return 10;
  if (len <= 20) return 11;
  if (len <= 24) return 12;
  if (len <= 32) return 13;
  if (len <= 48) return 14;
  return 15;
}

// ---------------------------------------------------------------------------- expectations
void ExpClear()
{
  gExpN = 0;
  gExpSig = 0;
  gEthWait = 0;
  gEthGot = 0;
  gEthMask = 0;
  gSeen[0] = 0;
}

void ExpCan(int bus, dword id, int ext, int len)
{
  if (gExpN >= kMaxExp)
    return;
  gExpBus[gExpN] = bus;
  gExpId[gExpN] = id;
  gExpExt[gExpN] = ext;
  gExpLen[gExpN] = len;
  gExpGot[gExpN] = 0;
  gExpCh[gExpN] = 0;
  gExpN++;
}

void ExpReset()
{
  int i;
  for (i = 0; i < gExpN; i++)
  {
    gExpGot[i] = 0;
    gExpCh[i] = 0;
  }
  gEthGot = 0;
  gSeen[0] = 0;
}

// ---------------------------------------------------------------------------- stimulus
void SendCan(int bus, dword id, int ext, int fd, int len)
{
  message * m;
  int j;
  if (ext)
    m.id = mkExtId(id);
  else
    m.id = id;
  m.can = gBusCh[bus];
  if (fd)
  {
    m.FDF = 1;
    m.BRS = 1;
  }
  m.dlc = LenToDlc(len);
  for (j = 0; j < len; j++)
    m.byte(j) = gData[j];
  if (gBusCtx[bus] != 0)
    SetBusContext(gBusCtx[bus]);
  output(m);
}

void SendEth(int sock, dword ecuPort, dword hid, int len)
{
  byte buf[72];
  int j;
  buf[0] = (hid >> 24) & 0xFF;
  buf[1] = (hid >> 16) & 0xFF;
  buf[2] = (hid >> 8) & 0xFF;
  buf[3] = hid & 0xFF;
  buf[4] = 0;
  buf[5] = 0;
  buf[6] = (len >> 8) & 0xFF;
  buf[7] = len & 0xFF;
  for (j = 0; j < len; j++)
    buf[8 + j] = gData[j];
  if (gSock[sock] == 0xFFFFFFFF)
  {
    snprintf(gSeen, elcount(gSeen), "socket %s:%d is not open", gSockIp[sock], gSockPort[sock]);
    return;
  }
  UdpSendTo(gSock[sock], IpGetAddressAsNumber(kEcuIp), ecuPort, buf, 8 + len);
}

// ---------------------------------------------------------------------------- answers
// The frame is recognised by its CAN id and the data of the test case (pattern / signal value); the bus is
// checked when CANoe knows it: by the network name (sure) or by the channel number of gBusCh (not sure, the
// table may not match the CANoe configuration). gExpGot: 1 = on the expected bus, 2 = bus not confirmed.
on message *
{
  int i, j, bus, len, bad, sure, pick;
  byte d[64];
  dword v;
  if (this.dir != RX || gExpN == 0)
    return;
  bus = -1;
  sure = 0;
  for (i = 0; i < kBuses; i++)
  {
    if (gBusCtx[i] != 0 && gBusCtx[i] == GetBusContext())
    {
      bus = i;
      sure = 1;
    }
  }
  if (bus < 0)
    for (i = 0; i < kBuses; i++)
      if (gBusCh[i] == this.can)
        bus = i;
  for (i = 0; i < gExpN; i++)               // this channel already answered an expectation of this id
    if (gExpGot[i] && gExpCh[i] == this.can && gExpId[i] == valOfId(this.id))
      return;
  len = this.DataLength;
  for (j = 0; j < 64; j++)
    d[j] = 0;
  for (j = 0; j < len && j < 64; j++)
    d[j] = this.byte(j);
  // the expectation it answers: same id on this bus, else (bus not sure) the first open one with this id
  pick = -1;
  for (i = 0; i < gExpN && pick < 0; i++)
    if (!gExpGot[i] && gExpId[i] == valOfId(this.id) && gExpExt[i] == (isExtId(this.id) != 0) && gExpBus[i] == bus)
      pick = i;
  for (i = 0; i < gExpN && pick < 0 && !sure; i++)
    if (!gExpGot[i] && gExpId[i] == valOfId(this.id) && gExpExt[i] == (isExtId(this.id) != 0))
      pick = i;
  if (pick < 0)
  {
    for (i = 0; i < gExpN; i++)
      if (!gExpGot[i] && gExpId[i] == valOfId(this.id) && sure)
        snprintf(gSeen, elcount(gSeen), "0x%X on network %s (wanted on %s)", gExpId[i], gBusName[bus],
                 gBusName[gExpBus[i]]);
    return;
  }
  if (gExpSig)
  {
    v = GetBits(d, gSigStart, gSigLen, gSigIntel);
    if (v != gSigVal)
    {
      snprintf(gSeen, elcount(gSeen), "0x%X channel %d: signal value 0x%X (wanted 0x%X)", gExpId[pick], this.can, v,
               gSigVal);
      return;
    }
  }
  else
  {
    bad = -1;
    if (len < gExpLen[pick])
      bad = 999;                            // CAN FD: longer frames are padded, only the PDU bytes count
    for (j = 0; j < gExpLen[pick] && bad < 0; j++)
      if (d[j] != gData[j])
        bad = j;
    if (bad == 999)
    {
      snprintf(gSeen, elcount(gSeen), "0x%X channel %d: length %d (wanted %d)", gExpId[pick], this.can, len,
               gExpLen[pick]);
      return;
    }
    if (bad >= 0)
    {
      snprintf(gSeen, elcount(gSeen), "0x%X channel %d: byte %d is 0x%02X (wanted 0x%02X)", gExpId[pick], this.can,
               bad, d[bad], gData[bad]);
      return;
    }
  }
  gExpCh[pick] = this.can;
  if (gExpBus[pick] == bus)
    gExpGot[pick] = 1;
  else
    gExpGot[pick] = 2;
  for (i = 0; i < gExpN; i++)
    if (!gExpGot[i])
      return;
  TestSupplyTextEvent("CAN_DONE");
}

void OnUdpReceiveFrom(dword socket, long result, dword address, dword port, char buffer[], dword size)
{
  int k;
  k = SockIndex(socket);
  if (k < 0)
    return;
  if (result == 0)
    ParseDatagram(k, buffer, size);
  Arm(k);
}

void ParseDatagram(int k, char buffer[], dword size)
{
  dword off, hid, len, j, bad;
  off = 0;
  while (off + 8 <= size)
  {
    hid = ((dword)(buffer[off] & 0xFF) << 24) | ((dword)(buffer[off + 1] & 0xFF) << 16) |
          ((dword)(buffer[off + 2] & 0xFF) << 8) | (dword)(buffer[off + 3] & 0xFF);
    len = ((dword)(buffer[off + 4] & 0xFF) << 24) | ((dword)(buffer[off + 5] & 0xFF) << 16) |
          ((dword)(buffer[off + 6] & 0xFF) << 8) | (dword)(buffer[off + 7] & 0xFF);
    if (off + 8 + len > size)
      return;
    if (gEthWait && hid == gEthHid)
    {
      bad = 0xFFFF;
      if (len != gEthLen)
        bad = 0xFFFE;
      for (j = 0; j < len && bad == 0xFFFF; j++)
        if ((buffer[off + 8 + j] & 0xFF) != gData[j])
          bad = j;
      if (bad == 0xFFFF)
        gEthGot = gEthGot | ((dword)1 << k);
      else if (bad == 0xFFFE)
        snprintf(gSeen, elcount(gSeen), "%s: header 0x%08X length %d (wanted %d)", gSockLabel[k], hid, len,
                 gEthLen);
      else
        snprintf(gSeen, elcount(gSeen), "%s: header 0x%08X byte %d differs", gSockLabel[k], hid, bad);
      if ((gEthGot & gEthMask) == gEthMask)
      {
        gEthWait = 0;
        TestSupplyTextEvent("ETH_DONE");
      }
    }
    off = off + 8 + len;
  }
}

// ---------------------------------------------------------------------------- checks (one per route kind)
void Verdict(long res, char what[], char missing[])
{
  char text[600];
  if (res == 1)
  {
    TestStepPass("check", what);
    return;
  }
  if (gSeen[0] != 0)
    snprintf(text, elcount(text), "%s - missing: %s - seen: %s", what, missing, gSeen);
  else
    snprintf(text, elcount(text), "%s - missing: %s", what, missing);
  TestStepFail("check", text);
}

// frames found by CAN id and data on a channel whose bus CANoe could not confirm: pass with a warning
void BusNote(char what[])
{
  int i;
  char text[400];
  char one[80];
  text[0] = 0;
  for (i = 0; i < gExpN; i++)
  {
    if (gExpGot[i] != 2)
      continue;
    snprintf(one, elcount(one), "0x%X wanted on %s, came on channel %d; ", gExpId[i], gBusName[gExpBus[i]], gExpCh[i]);
    strncat(text, one, elcount(text));
  }
  if (text[0] != 0)
  {
    strncat(text, "set gBusName / gBusCh to the CANoe networks to check the bus", elcount(text));
    TestStepWarning(what, text);
  }
}

void MissingCan(char out[])
{
  int i;
  char one[80];
  out[0] = 0;
  for (i = 0; i < gExpN; i++)
  {
    if (gExpGot[i])
      continue;
    snprintf(one, elcount(one), "%s 0x%X ", gBusName[gExpBus[i]], gExpId[i]);
    strncat(out, one, 300);
  }
}

void MissingEth(char out[])
{
  int k;
  out[0] = 0;
  for (k = 0; k < kSocks; k++)
    if (((gEthMask >> k) & 1) && !((gEthGot >> k) & 1))
    {
      strncat(out, gSockLabel[k], 300);
      strncat(out, " ", 300);
    }
}

testfunction CheckCanToEth(int tc, int bus, dword id, int ext, int fd, int len, dword hid, dword mask, char what[])
{
  long res;
  int attempt;
  char missing[300];
  res = 0;
  for (attempt = 0; attempt < 2 && res != 1; attempt++)    // 2nd try: the first frame may wait for ARP
  {
    ExpClear();
    gEthHid = hid;
    gEthLen = len;
    gEthMask = mask;
    gEthWait = 1;
    Fill(tc, len);
    SendCan(bus, id, ext, fd, len);
    res = TestWaitForTextEvent("ETH_DONE", kTimeoutEth);
  }
  gEthWait = 0;
  MissingEth(missing);
  Verdict(res, what, missing);
  TestWaitForTimeout(kGapMs);
}

testfunction CheckEthToCan(int tc, int sock, dword ecuPort, dword hid, int len, char what[])
{
  long res;
  int attempt;
  char missing[300];
  res = 0;
  for (attempt = 0; attempt < 2 && res != 1; attempt++)
  {
    ExpReset();
    Fill(tc, len);
    SendEth(sock, ecuPort, hid, len);
    res = TestWaitForTextEvent("CAN_DONE", kTimeoutCan);
  }
  MissingCan(missing);
  Verdict(res, what, missing);
  BusNote(what);
  gExpN = 0;
  TestWaitForTimeout(kGapMs);
}

testfunction CheckCanToCan(int tc, int bus, dword id, int ext, int fd, int len, char what[])
{
  long res;
  char missing[300];
  ExpReset();
  Fill(tc, len);
  SendCan(bus, id, ext, fd, len);
  res = TestWaitForTextEvent("CAN_DONE", kTimeoutCan);
  MissingCan(missing);
  Verdict(res, what, missing);
  BusNote(what);
  gExpN = 0;
  TestWaitForTimeout(kGapMs);
}

testfunction CheckSignal(int bus, dword id, int ext, int fd, int len, int sStart, int sLen, int sIntel,
                         int dBus, dword dId, int dExt, int dStart, int dLen, int dIntel, dword v1, dword v2,
                         dword waitMs, char what[])
{
  long res;
  int step, j;
  dword v;
  char missing[300];
  char text[400];
  for (step = 0; step < 2; step++)
  {
    if (step == 0)
      v = v1;
    else
      v = v2;
    ExpClear();
    ExpCan(dBus, dId, dExt, 0);
    gExpSig = 1;
    gSigStart = dStart;
    gSigLen = dLen;
    gSigIntel = dIntel;
    gSigVal = v;
    for (j = 0; j < 64; j++)
      gData[j] = 0;
    SetBits(gData, sStart, sLen, sIntel, v);
    SendCan(bus, id, ext, fd, len);
    res = TestWaitForTextEvent("CAN_DONE", waitMs);
    snprintf(text, elcount(text), "%s = 0x%X", what, v);
    MissingCan(missing);
    Verdict(res, text, missing);
    BusNote(text);
    gExpN = 0;
  }
  TestWaitForTimeout(kGapMs);
}
"""


def capl_text(tm: TestModule, source: str = "", collect_ms: float = 0) -> str:
    when = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    nb, ns = max(len(tm.buses), 1), max(min(len(tm.socks), 8), 1)
    socks = tm.socks[:8]
    bus_rows = "\n".join(f"//   {i:>2}  CAN channel {i + 1:<3} network {_q(b)}" for i, b in enumerate(tm.buses))
    sock_rows = "\n".join(f"//   {i:>2}  {s.ip}:{s.port}  ({s.label})" for i, s in enumerate(socks))
    ips = sorted({s.ip for s in socks})
    problems = "".join(f"// !! {x}\n" for x in tm.problems)
    arm = "\n".join(f"  {'if' if i == 0 else 'else if'} (k == {i}) UdpReceiveFrom(gSock[{i}], gRx{i}, elcount(gRx{i}));"
                    for i in range(len(socks))) or "  ;"
    rx_bufs = "\n".join(f"  char  gRx{i}[1600];" for i in range(len(socks)))
    groups, calls = [], []
    for title, desc, cases in tm.groups:
        groups.append("\n\n".join(cases) + "\n")
        names = [re.match(r"testcase (\w+)\(", c).group(1) for c in cases]
        calls.append(f'  TestGroupBegin({_q(title)}, {_q(desc)});\n' + "".join(f"  {x}();\n" for x in names) +
                     "  TestGroupEnd();")
    counts = ", ".join(f"{k}: {v}" for k, v in tm.counts.items())
    return f"""/*@!Encoding:1252*/
// ============================================================================================================
// Gateway test of {tm.ecu} (CANoe test module, generated by EcucStudio {when}{', from ' + source if source else ''})
// Test cases: {counts}.
//
// CANoe setup (CANoe 12+, VN56xx on the Ethernet port of {tm.ecu}, nothing else on the network)
//   1. Test Setup: add this file as a test module. Assign it to every CAN network below and to the Ethernet
//      network of {tm.ecu}.
//   2. CAN: the channel / network name of every bus of {tm.ecu} (change gBusCh / gBusName below if they differ).
//      With a network name found in CANoe the frame is sent in that network, else on the channel number.
{bus_rows}
//   3. Ethernet: the TCP/IP stack of this test node (Node configuration -> TCP/IP Stack) gets these IPv4
//      addresses (VLAN as in the gateway file), the nodes CANoe plays:
//        {', '.join(ips) or '-'}
//      Sockets of the test (they must be free on the stack):
{sock_rows}
//      {tm.ecu} itself: {tm.ecu_ip or '?'}.
//   4. {tm.ecu} awake and communicating (KL15, network management) before the test starts.
// Each test case sends its own data pattern (bytes depend on the test number), so a frame of another route fails.
// CAN -> ETH and ETH -> CAN are tried twice (the first frame may wait for ARP).
{problems}{_SEP}

includes
{{
}}

variables
{{
  char  kEcuIp[16] = {_q(tm.ecu_ip or "0.0.0.0")};
  const dword kTimeoutCan = {TIMEOUT_MS};
  const dword kTimeoutEth = {TIMEOUT_MS + int(collect_ms)};
  const dword kGapMs = {GAP_MS};

  // ---- CAN buses of {tm.ecu}: CANoe channel and network name
  const int kBuses = {nb};
  int   gBusCh[{nb}] = {{ {', '.join(str(i + 1) for i in range(nb))} }};
  char  gBusName[{nb}][40] = {{ {', '.join(_q(b) for b in tm.buses) or '""'} }};
  dword gBusCtx[{nb}];

  // ---- Ethernet sockets of the nodes CANoe plays
  const int kSocks = {ns};
  char  gSockIp[{ns}][16] = {{ {', '.join(_q(s.ip) for s in socks) or '"0.0.0.0"'} }};
  dword gSockPort[{ns}] = {{ {', '.join(str(s.port) for s in socks) or '0'} }};
  char  gSockLabel[{ns}][40] = {{ {', '.join(_q(s.label) for s in socks) or '""'} }};
  dword gSock[{ns}];
{rx_bufs}

  // ---- expected answers
  const int kMaxExp = 16;
  int   gExpN = 0;
  int   gExpBus[16];
  dword gExpId[16];
  int   gExpExt[16];
  int   gExpLen[16];
  int   gExpGot[16];
  long  gExpCh[16];
  int   gExpSig = 0;
  int   gSigStart;
  int   gSigLen;
  int   gSigIntel;
  dword gSigVal;
  int   gEthWait = 0;
  dword gEthHid;
  dword gEthLen;
  dword gEthMask;
  dword gEthGot;
  byte  gData[64];
  char  gSeen[300];
}}

// ---------------------------------------------------------------------------- set-up
int SockIndex(dword socket)
{{
  int k;
  for (k = 0; k < kSocks; k++)
    if (gSock[k] == socket)
      return k;
  return -1;
}}

void Arm(int k)
{{
{arm}
}}

void OpenAll()
{{
  int i;
  char err[200];
  for (i = 0; i < kBuses; i++)
  {{
    gBusCtx[i] = GetBusNameContext(gBusName[i]);
    if (gBusCtx[i] != 0)
      write("Gateway test: bus %s = CANoe network %s", gBusName[i], gBusName[i]);
    else
      write("Gateway test: bus %s: no CANoe network of this name, channel %d is used (gBusCh)", gBusName[i],
            gBusCh[i]);
  }}
  for (i = 0; i < kSocks; i++)
  {{
    gSock[i] = UdpOpen(IpGetAddressAsNumber(gSockIp[i]), gSockPort[i]);
    if (gSock[i] == 0xFFFFFFFF)
    {{
      IpGetLastErrorAsString(err, elcount(err));
      write("Gateway test: cannot open UDP %s:%d (%s): add the address to the TCP/IP stack of this node.",
            gSockIp[i], gSockPort[i], err);
      continue;
    }}
    Arm(i);
  }}
}}

void CloseAll()
{{
  int i;
  for (i = 0; i < kSocks; i++)
    if (gSock[i] != 0xFFFFFFFF)
      UdpClose(gSock[i]);
}}
{_HELPERS}
// ---------------------------------------------------------------------------- test cases
{chr(10).join(groups)}

void MainTest()
{{
  TestModuleTitle({_q("Gateway " + tm.ecu)});
  TestModuleDescription({_q(f"Routes of the gateway file of {tm.ecu}: {counts}")});
  OpenAll();
  TestWaitForTimeout(500);
{chr(10).join(calls)}
  CloseAll();
}}
"""


def write_test_module(plan: Plan, path: str) -> TestModule:
    """Write the CANoe test module of the routes of *plan* to *path* (cp1252, CRLF)."""
    tm = build(plan)
    col = plan.cfg.ethernet.collection if plan.cfg is not None else None
    source = os.path.basename(plan.cfg.output) if plan.cfg is not None and plan.cfg.output else ""
    text = capl_text(tm, source, col.timeout_ms if col is not None and col.enabled else 0)
    with open(path, "w", encoding="cp1252", errors="replace", newline="\r\n") as fh:
        fh.write(text)
    return tm
