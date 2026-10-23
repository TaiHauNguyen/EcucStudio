#!/usr/bin/env python3
"""AVTP AAF talker (IEEE 1722-2016) to test an audio receiver (ECU) over Ethernet.

    python avtp_talker.py              # GUI
    python avtp_talker.py --list       # list Npcap devices and their MAC
    python avtp_talker.py --selftest   # build packets offline, print header and build speed

Sends an AAF audio stream (sine tone or audio file: WAV, or MP3 / FLAC / OGG through the soundfile
package) out of a PC network card through Npcap. A file at another sample rate is resampled to the stream
rate (windowed-sinc polyphase) unless "Resample" is off. Packets are
paced by the Npcap send queue in synchronous mode (the driver busy-waits on the packet timestamps), so
the gap between packets stays at samples_per_frame / sample_rate instead of following the coarse
Windows timer. A "busy-wait per packet" mode is kept as fallback.

The "Receiver requirement" fields (sample rate, channels, bit depth, samples per frame, max payload)
describe what the receiver under test accepts; the "Computed" panel checks the stream against them.

IEEE 802.1CB (FRER) talker side: optional R-TAG (EtherType 0xF1C1, reserved 0, 16-bit sequence number)
after the VLAN tag; replication of every packet as two identical copies (same sequence number) either on
the same interface or with copy B on a second interface (two paths into a ring), copy B delayed by a set
time; random loss of copy A / copy B to check that the listener's sequence recovery fills the gaps.

Needs Windows, Npcap, numpy (tkinter for the GUI); soundfile only for non-WAV files. If Npcap was
installed with "Restrict Npcap driver's access to Administrators only", the GUI restarts itself elevated
(UAC). Settings are kept in %APPDATA%\\avtp_talker\\settings.json, saved 1 s after every change and on
close; "Save as..." / "Load..." keep named settings files (JSON) for different setups.
"""
import csv
import ctypes
import json
import math
import os
import queue
import random
import struct
import subprocess
import sys
import threading
import time

import numpy as np

# --------------------------------------------------------------------------------------------
# IEEE 1722 AAF constants
# --------------------------------------------------------------------------------------------
ETHERTYPE_AVTP = 0x22F0
ETHERTYPE_VLAN = 0x8100
ETHERTYPE_RTAG = 0xF1C1     # IEEE 802.1CB redundancy tag
RTAG_LEN = 6                # EtherType + reserved (16 bit) + sequence number (16 bit)
REPLICATION_MODES = ("off", "same", "second")  # 1 copy / 2 copies on interface A / copy B on interface B
AVTP_SUBTYPE_AAF = 0x02
AAF_HEADER_LEN = 24

# Nominal sample rate codes (nsr)
NSR_CODES = {8000: 0x1, 16000: 0x2, 32000: 0x3, 44100: 0x4, 48000: 0x5,
             88200: 0x6, 96000: 0x7, 176400: 0x8, 192000: 0x9, 24000: 0xA}

# bit depth -> (AAF format code, bytes per sample)
FORMAT_CODES = {32: (0x02, 4), 24: (0x03, 3), 16: (0x04, 2)}

ETH_MIN_FRAME = 60          # without FCS
ETH_MTU = 1500
WIRE_OVERHEAD = 4 + 8 + 12  # FCS + preamble/SFD + inter-frame gap

SETTINGS_FILE = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "avtp_talker", "settings.json")


# --------------------------------------------------------------------------------------------
# Npcap binding (ctypes, no scapy)
# --------------------------------------------------------------------------------------------
class PcapPktHdr(ctypes.Structure):
    # struct timeval on Windows uses 32-bit long
    _fields_ = [("tv_sec", ctypes.c_long), ("tv_usec", ctypes.c_long),
                ("caplen", ctypes.c_uint32), ("len", ctypes.c_uint32)]


class PcapIf(ctypes.Structure):
    pass


PcapIf._fields_ = [("next", ctypes.POINTER(PcapIf)), ("name", ctypes.c_char_p),
                   ("description", ctypes.c_char_p), ("addresses", ctypes.c_void_p),
                   ("flags", ctypes.c_uint32)]


def _load_wpcap():
    npcap_dir = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "Npcap")
    if os.path.isfile(os.path.join(npcap_dir, "wpcap.dll")):
        os.add_dll_directory(npcap_dir)  # lets wpcap.dll find Packet.dll
        return ctypes.CDLL(os.path.join(npcap_dir, "wpcap.dll"))
    return ctypes.CDLL("wpcap.dll")


class Npcap:
    def __init__(self):
        lib = _load_wpcap()
        lib.pcap_findalldevs.argtypes = [ctypes.POINTER(ctypes.POINTER(PcapIf)), ctypes.c_char_p]
        lib.pcap_findalldevs.restype = ctypes.c_int
        lib.pcap_freealldevs.argtypes = [ctypes.POINTER(PcapIf)]
        lib.pcap_freealldevs.restype = None
        lib.pcap_open_live.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_char_p]
        lib.pcap_open_live.restype = ctypes.c_void_p
        lib.pcap_close.argtypes = [ctypes.c_void_p]
        lib.pcap_close.restype = None
        lib.pcap_sendpacket.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        lib.pcap_sendpacket.restype = ctypes.c_int
        lib.pcap_geterr.argtypes = [ctypes.c_void_p]
        lib.pcap_geterr.restype = ctypes.c_char_p
        self.has_sendqueue = hasattr(lib, "pcap_sendqueue_alloc")
        if self.has_sendqueue:
            lib.pcap_sendqueue_alloc.argtypes = [ctypes.c_uint]
            lib.pcap_sendqueue_alloc.restype = ctypes.c_void_p
            lib.pcap_sendqueue_destroy.argtypes = [ctypes.c_void_p]
            lib.pcap_sendqueue_destroy.restype = None
            lib.pcap_sendqueue_queue.argtypes = [ctypes.c_void_p, ctypes.POINTER(PcapPktHdr), ctypes.c_char_p]
            lib.pcap_sendqueue_queue.restype = ctypes.c_int
            lib.pcap_sendqueue_transmit.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
            lib.pcap_sendqueue_transmit.restype = ctypes.c_uint
        self.lib = lib

    def devices(self):
        """Return [(name, description)] of all Npcap devices."""
        head = ctypes.POINTER(PcapIf)()
        err = ctypes.create_string_buffer(256)
        if self.lib.pcap_findalldevs(ctypes.byref(head), err) != 0:
            raise RuntimeError("pcap_findalldevs: " + err.value.decode(errors="replace"))
        result = []
        node = head
        while node:
            dev = node.contents
            name = dev.name.decode(errors="replace") if dev.name else ""
            desc = dev.description.decode(errors="replace") if dev.description else name
            result.append((name, desc))
            node = dev.next
        self.lib.pcap_freealldevs(head)
        return result

    def open(self, name):
        err = ctypes.create_string_buffer(256)
        handle = self.lib.pcap_open_live(name.encode(), 65536, 0, 1, err)
        if not handle:
            raise RuntimeError("pcap_open_live: " + err.value.decode(errors="replace"))
        return handle

    def close(self, handle):
        self.lib.pcap_close(handle)

    def error(self, handle):
        msg = self.lib.pcap_geterr(handle)
        return msg.decode(errors="replace") if msg else "unknown error"

    def send(self, handle, frame):
        return self.lib.pcap_sendpacket(handle, frame, len(frame))

    def make_queue(self, frames, rel_us):
        """Copy frames into an Npcap send queue; rel_us = send time of each frame relative to the first."""
        size = sum(len(f) + ctypes.sizeof(PcapPktHdr) for f in frames)
        squeue = self.lib.pcap_sendqueue_alloc(size)
        if not squeue:
            raise MemoryError("pcap_sendqueue_alloc failed")
        hdr = PcapPktHdr()
        for frame, us in zip(frames, rel_us):
            hdr.tv_sec, hdr.tv_usec = divmod(us, 1_000_000)
            hdr.caplen = hdr.len = len(frame)
            if self.lib.pcap_sendqueue_queue(squeue, ctypes.byref(hdr), frame) != 0:
                self.lib.pcap_sendqueue_destroy(squeue)
                raise RuntimeError("pcap_sendqueue_queue failed")
        return squeue, size

    def transmit_queue(self, handle, squeue, sync=True):
        return self.lib.pcap_sendqueue_transmit(handle, squeue, 1 if sync else 0)

    def destroy_queue(self, squeue):
        self.lib.pcap_sendqueue_destroy(squeue)


def npcap_admin_only():
    """True when Npcap was installed with 'restrict access to Administrators'."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\npcap\Parameters") as k:
            return winreg.QueryValueEx(k, "AdminOnly")[0] == 1
    except (ImportError, OSError):
        return False


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def relaunch_as_admin():
    """Restart this script elevated (UAC prompt). Returns True if the elevated copy was started."""
    script = os.path.abspath(sys.argv[0])
    params = " ".join(f'"{a}"' for a in [script] + sys.argv[1:])
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
    return rc > 32


def adapter_macs():
    """Map adapter GUID (upper case, with braces) -> MAC 'AA:BB:..' using 'getmac'."""
    result = {}
    try:
        out = subprocess.run(["getmac", "/v", "/fo", "csv", "/nh"], capture_output=True, text=True,
                             encoding="mbcs", errors="replace",
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=10).stdout
    except (OSError, LookupError, subprocess.SubprocessError):
        return result
    for row in csv.reader(out.splitlines()):
        if len(row) < 4 or "{" not in row[3]:
            continue
        guid = row[3][row[3].index("{"):].upper()
        mac = row[2].replace("-", ":").upper()
        if len(mac) == 17:
            result[guid] = mac
    return result


def mac_for_device(dev_name, macs):
    if "{" in dev_name:
        return macs.get(dev_name[dev_name.index("{"):].upper(), "")
    return ""


# --------------------------------------------------------------------------------------------
# Audio sources -> float64 array (n_samples, channels) in [-1, 1]
# --------------------------------------------------------------------------------------------
def read_wav(path):
    """Read PCM 8/16/24/32-bit or float 32/64 WAV (incl. WAVE_FORMAT_EXTENSIBLE). Returns (rate, data)."""
    with open(path, "rb") as f:
        raw = f.read()
    if raw[0:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise ValueError("not a RIFF/WAVE file")
    pos, fmt, data = 12, None, None
    while pos + 8 <= len(raw):
        cid = raw[pos:pos + 4]
        size = struct.unpack_from("<I", raw, pos + 4)[0]
        body = raw[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            fmt = body
        elif cid == b"data":
            data = body
        pos += 8 + size + (size & 1)
    if fmt is None or data is None:
        raise ValueError("missing fmt or data chunk")
    tag, nch, rate, _, block, bits = struct.unpack_from("<HHIIHH", fmt, 0)
    if tag == 0xFFFE and len(fmt) >= 26:
        tag = struct.unpack_from("<H", fmt, 24)[0]  # first 2 bytes of SubFormat GUID
    data = data[:len(data) - len(data) % block]
    if tag == 1 and bits == 8:
        x = (np.frombuffer(data, np.uint8).astype(np.float64) - 128.0) / 128.0
    elif tag == 1 and bits == 16:
        x = np.frombuffer(data, "<i2").astype(np.float64) / 32768.0
    elif tag == 1 and bits == 24:
        b = np.frombuffer(data, np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        x = np.where(v & 0x800000, v - 0x1000000, v).astype(np.float64) / 8388608.0
    elif tag == 1 and bits == 32:
        x = np.frombuffer(data, "<i4").astype(np.float64) / 2147483648.0
    elif tag == 3 and bits == 32:
        x = np.frombuffer(data, "<f4").astype(np.float64)
    elif tag == 3 and bits == 64:
        x = np.frombuffer(data, "<f8").astype(np.float64)
    else:
        raise ValueError(f"unsupported WAV format tag {tag}, {bits} bit")
    return rate, x.reshape(-1, nch)


def _wav_header(path):
    """(rate, channels, frames) from the RIFF header only."""
    with open(path, "rb") as f:
        head = f.read(12)
        if head[0:4] != b"RIFF" or head[8:12] != b"WAVE":
            raise ValueError("not a RIFF/WAVE file")
        rate = nch = block = None
        while True:
            ck = f.read(8)
            if len(ck) < 8:
                raise ValueError("missing fmt or data chunk")
            cid, size = ck[:4], struct.unpack("<I", ck[4:])[0]
            if cid == b"fmt ":
                _, nch, rate, _, block = struct.unpack("<HHIIH", f.read(size)[:14])
                if size & 1:
                    f.read(1)
            elif cid == b"data":
                if rate is None:
                    raise ValueError("data chunk before fmt chunk")
                return rate, nch, size // block
            else:
                f.seek(size + (size & 1), os.SEEK_CUR)


def _soundfile():
    try:
        import soundfile
    except ImportError:
        raise ValueError("reading MP3 / FLAC / OGG needs the soundfile package: pip install soundfile") from None
    return soundfile


def audio_info(path):
    """(rate, channels, frames) of an audio file without decoding it."""
    if path.lower().endswith(".wav"):
        return _wav_header(path)
    info = _soundfile().info(path)
    return info.samplerate, info.channels, info.frames


def read_audio(path):
    """Decode an audio file to (rate, float32 array (n, channels)) in [-1, 1]."""
    if path.lower().endswith(".wav"):
        rate, data = read_wav(path)
        return rate, data.astype(np.float32)
    data, rate = _soundfile().read(path, dtype="float32", always_2d=True)
    return rate, data


def resample(x, src_rate, dst_rate, half_taps=16):
    """Polyphase windowed-sinc (Kaiser) resampler; x is (n, channels), returns float32 (m, channels).

    Output n sits at input time n * down / up. The outputs k, k + up, k + 2 up ... share one filter
    phase and read the input with a stride of 'down', so each phase is one strided dot product."""
    if src_rate == dst_rate:
        return x.astype(np.float32, copy=False)
    g = math.gcd(src_rate, dst_rate)
    up, down = dst_rate // g, src_rate // g          # output n is at input time n * down / up
    ratio = dst_rate / src_rate
    cutoff = 0.95 * min(1.0, ratio)                  # relative to the input Nyquist frequency
    half = int(math.ceil(half_taps / min(1.0, ratio)))
    offs = np.arange(-half + 1, half + 1)            # taps around floor(input time)
    tau = offs[None, :] - (np.arange(up) / up)[:, None]
    window = np.i0(8.6 * np.sqrt(np.clip(1.0 - (tau / half) ** 2, 0.0, None))) / np.i0(8.6)
    h = cutoff * np.sinc(cutoff * tau) * window
    h = (h / h.sum(axis=1, keepdims=True)).astype(np.float32)  # unity gain for every phase
    pad = half + 1
    zeros = np.zeros((pad, x.shape[1]), np.float32)
    xp = np.ascontiguousarray(np.concatenate([zeros, x.astype(np.float32, copy=False), zeros]))
    n_out = (x.shape[0] * up) // down
    out = np.empty((n_out, x.shape[1]), np.float32)
    s0, s1 = xp.strides
    for k in range(min(up, n_out)):
        m = (n_out - k + up - 1) // up
        start = (k * down) // up + pad + offs[0]
        view = np.lib.stride_tricks.as_strided(xp[start:], shape=(m, len(offs), x.shape[1]),
                                               strides=(down * s0, s0, s1), writeable=False)
        out[k::up] = np.einsum("mtc,t->mc", view, h[(k * down) % up])
    return out


def map_channels(data, out_ch, missing):
    """Fit source channels to out_ch: extra channels dropped, missing ones repeated or silent."""
    nsrc = data.shape[1]
    if nsrc >= out_ch:
        return np.ascontiguousarray(data[:, :out_ch])
    out = np.zeros((data.shape[0], out_ch))
    out[:, :nsrc] = data
    if missing == "repeat":
        for c in range(nsrc, out_ch):
            out[:, c] = data[:, c % nsrc]
    return out


class SineSource:
    def __init__(self, freq, volume, channels, rate):
        self.inc = freq / rate
        self.volume = volume
        self.channels = channels
        self.phase = 0.0

    def read(self, n):
        ph = self.phase + np.arange(n, dtype=np.float64) * self.inc
        self.phase = (self.phase + n * self.inc) % 1.0
        x = self.volume * np.sin(2.0 * np.pi * ph)
        return np.repeat(x[:, None], self.channels, axis=1), False


class FileSource:
    """Decoded audio file; channels are mapped per chunk so a long file stays at its own channel count."""
    def __init__(self, data, channels, missing, gain, loop):
        if data.shape[0] == 0:
            raise ValueError("audio file has no samples")
        self.data = data
        self.channels = channels
        self.missing = missing
        self.gain = gain
        self.loop = loop
        self.pos = 0

    def read(self, n):
        total = self.data.shape[0]
        if self.loop:
            idx = (self.pos + np.arange(n)) % total
            self.pos = (self.pos + n) % total
            chunk, ended = self.data[idx], False
        else:
            chunk = self.data[self.pos:self.pos + n]
            self.pos += chunk.shape[0]
            ended = self.pos >= total
        return map_channels(chunk, self.channels, self.missing) * self.gain, ended


WavSource = FileSource


# --------------------------------------------------------------------------------------------
# Stream configuration and packet builder
# --------------------------------------------------------------------------------------------
def parse_mac(text):
    parts = text.replace("-", ":").split(":")
    if len(parts) != 6:
        raise ValueError(f"bad MAC address: {text}")
    return bytes(int(p, 16) for p in parts)


def parse_stream_id(text):
    t = text.strip().replace(":", "").replace("-", "")
    return int(t, 16).to_bytes(8, "big")


class StreamConfig:
    def __init__(self, s):
        self.dst = parse_mac(s["dst_mac"])
        self.src = parse_mac(s["src_mac"])
        self.stream_id = parse_stream_id(s["stream_id"])
        self.vlan = bool(s["vlan"])
        self.vid = int(s["vid"])
        self.pcp = int(s["pcp"])
        self.rate = int(s["sample_rate"])
        self.channels = int(s["channels"])
        self.bit_depth = int(s["bit_depth"])
        self.spf = int(s["samples_per_frame"])
        self.link_mbps = float(s["link_speed"])
        self.frames_to_send = int(s["frames_to_send"])
        self.pres_offset_ns = int(float(s["pres_offset_ms"]) * 1_000_000)
        # 802.1CB
        self.rtag = bool(s.get("rtag", False))
        self.rtag_seq_start = int(str(s.get("rtag_seq_start", "0")).strip() or "0", 0)
        self.replication = str(s.get("replication", "off")).strip().lower()
        self.copy_b_delay_ns = int(float(s.get("copy_b_delay_us", "0") or 0) * 1000)
        self.drop_pct = {"A": float(s.get("drop_a_pct", "0") or 0), "B": float(s.get("drop_b_pct", "0") or 0)}
        if not 0 <= self.rtag_seq_start <= 0xFFFF:
            raise ValueError("R-TAG start sequence must be 0..65535 (0x0000..0xFFFF)")
        if self.replication not in REPLICATION_MODES:
            raise ValueError("replication must be off, same or second")
        if self.copy_b_delay_ns < 0 or not all(0.0 <= p <= 100.0 for p in self.drop_pct.values()):
            raise ValueError("copy B delay >= 0, drop 0..100 %")
        if self.rate not in NSR_CODES:
            raise ValueError(f"sample rate {self.rate} has no AAF nsr code")
        if self.bit_depth not in FORMAT_CODES:
            raise ValueError("bit depth must be 16, 24 or 32")
        if not 1 <= self.channels <= 1023 or self.spf < 1:
            raise ValueError("channels and samples/frame must be positive")
        if not 0 <= self.vid <= 4095 or not 0 <= self.pcp <= 7:
            raise ValueError("VID 0..4095, PCP 0..7")
        self.fmt_code, self.sample_bytes = FORMAT_CODES[self.bit_depth]
        self.payload_len = self.spf * self.channels * self.sample_bytes
        if AAF_HEADER_LEN + self.payload_len > ETH_MTU:
            raise ValueError(f"AVTP PDU {AAF_HEADER_LEN + self.payload_len} B exceeds MTU {ETH_MTU}")

    @property
    def pps(self):
        return self.rate / self.spf

    @property
    def frame_len(self):
        return max(ETH_MIN_FRAME, 14 + (4 if self.vlan else 0) + (RTAG_LEN if self.rtag else 0)
                   + AAF_HEADER_LEN + self.payload_len)

    @property
    def wire_len(self):
        return self.frame_len + WIRE_OVERHEAD

    @property
    def bandwidth_mbps(self):
        """One copy of the stream."""
        return self.wire_len * 8 * self.pps / 1e6

    @property
    def link_copies(self):
        """Copies of each packet on the link of interface A."""
        return 2 if self.replication == "same" else 1

    def send_offset_ns(self, n):
        """Exact send time of packet n relative to packet 0 (integer math, no drift)."""
        return (n * self.spf * 1_000_000_000) // self.rate


class Requirement:
    """What the receiver under test accepts."""
    def __init__(self, s):
        self.rate = int(s["req_sample_rate"])
        self.channels = int(s["req_channels"])
        self.bit_depth = int(s["req_bit_depth"])
        self.spf = int(s["req_samples_per_frame"])
        self.max_payload = int(s["req_max_payload"])

    def text(self):
        rate = f"{self.rate / 1000:g} kHz"
        return f"{rate} / {self.channels} ch / {self.bit_depth} bit / {self.spf} samples / {self.max_payload} byte"

    def mismatches(self, cfg):
        names = (("sample rate", self.rate, cfg.rate), ("channels", self.channels, cfg.channels),
                 ("bit depth", self.bit_depth, cfg.bit_depth), ("samples/frame", self.spf, cfg.spf),
                 ("payload", self.max_payload, cfg.payload_len))
        return [f"{n} {have} (needs {want})" for n, want, have in names if want != have]


def computed_text(cfg, req):
    """Summary of the stream as shown in the 'Computed' panel."""
    load = cfg.bandwidth_mbps * cfg.link_copies
    util = load / cfg.link_mbps * 100.0
    pay_note = "<- matches requirement" if cfg.payload_len == req.max_payload else \
        f"<- requirement is {req.max_payload} byte"
    tags = (", VLAN tag" if cfg.vlan else "") + (", R-TAG" if cfg.rtag else "")
    lines = [
        f"AAF payload      {cfg.payload_len:8d} byte   {pay_note}",
        f"AVTP length      {AAF_HEADER_LEN + cfg.payload_len:8d} byte  "
        f"(header {AAF_HEADER_LEN} + payload {cfg.payload_len})",
        f"On-wire frame    {cfg.wire_len:8d} byte  (+preamble/SFD +IFG{tags})",
        f"Period           {1e6 / cfg.pps:12.3f} us",
        f"Frame rate       {cfg.pps:10.1f} /s",
        f"Transmit time    {cfg.wire_len * 8 / cfg.link_mbps:12.3f} us @ {cfg.link_mbps:g} Mbps",
    ]
    if cfg.replication == "same":
        lines.append("Copies                  2 per packet on interface A (link load x2)")
    elif cfg.replication == "second":
        lines.append("Copies                  2 per packet: A on interface A, B on interface B")
    lines += [f"Bandwidth        {load:12.3f} Mbps  ({util:.2f} % of link)", ""]
    bad = req.mismatches(cfg)
    if bad:
        lines.append("DOES NOT MATCH REQUIREMENT: " + req.text())
        lines.append("  " + ", ".join(bad))
    else:
        lines.append("MATCHES REQUIREMENT: " + req.text())
    return "\n".join(lines)


class PacketBuilder:
    def __init__(self, cfg, source):
        self.cfg = cfg
        self.source = source
        self.n = 0
        self.ended = False
        self.ts_base_ns = time.time_ns()  # PC wall clock, not gPTP
        tpl = bytearray(cfg.dst + cfg.src)
        if cfg.vlan:
            tpl += struct.pack(">HH", ETHERTYPE_VLAN, (cfg.pcp << 13) | cfg.vid)
        self.rtag_seq_off = None
        if cfg.rtag:
            self.rtag_seq_off = len(tpl) + 4
            tpl += struct.pack(">HHH", ETHERTYPE_RTAG, 0, 0)  # sequence number patched per packet
        tpl += struct.pack(">H", ETHERTYPE_AVTP)
        self.avtp_off = len(tpl)
        tpl += bytes([AVTP_SUBTYPE_AAF,
                      0x81,           # sv = 1, version 0, mr 0, tv = 1
                      0x00,           # sequence_num (patched per packet)
                      0x00])          # tu
        tpl += cfg.stream_id
        tpl += b"\x00\x00\x00\x00"    # avtp_timestamp (patched per packet)
        tpl += bytes([cfg.fmt_code,
                      (NSR_CODES[cfg.rate] << 4) | ((cfg.channels >> 8) & 0x03),
                      cfg.channels & 0xFF,
                      cfg.bit_depth])
        tpl += struct.pack(">H", cfg.payload_len)
        tpl += b"\x00\x00"            # sp = 0, evt = 0, reserved
        self.template = bytes(tpl)
        self.pad = max(0, ETH_MIN_FRAME - len(tpl) - cfg.payload_len)

    def _to_bytes(self, x):
        cfg = self.cfg
        x = np.clip(x, -1.0, 1.0)
        if cfg.bit_depth == 32:
            v = np.clip(np.round(x * 2147483648.0), -2147483648, 2147483647).astype(">i4")
            return v.view(np.uint8)
        if cfg.bit_depth == 24:
            v = np.clip(np.round(x * 8388608.0), -8388608, 8388607).astype(">i4")
            return v.view(np.uint8).reshape(-1, 4)[:, 1:]
        v = np.clip(np.round(x * 32768.0), -32768, 32767).astype(">i2")
        return v.view(np.uint8)

    def build(self, count):
        """Return (frames, first_packet_index); frames is empty when the stream has ended."""
        cfg = self.cfg
        if self.ended:
            return [], self.n
        if cfg.frames_to_send > 0:
            count = min(count, cfg.frames_to_send - self.n)
            if count <= 0:
                self.ended = True
                return [], self.n
        samples, src_end = self.source.read(count * cfg.spf)
        npk = math.ceil(samples.shape[0] / cfg.spf)
        if npk == 0:
            self.ended = True
            return [], self.n
        if samples.shape[0] < npk * cfg.spf:  # last packet of a non-looping file
            samples = np.vstack([samples, np.zeros((npk * cfg.spf - samples.shape[0], cfg.channels))])
        payload = self._to_bytes(samples).reshape(npk, cfg.payload_len)
        first = self.n
        frames = []
        h = bytearray(self.template)
        seq_off, ts_off = self.avtp_off + 2, self.avtp_off + 12
        tail = b"\x00" * self.pad
        for i in range(npk):
            n = first + i
            h[seq_off] = n & 0xFF
            if self.rtag_seq_off is not None:
                struct.pack_into(">H", h, self.rtag_seq_off, (cfg.rtag_seq_start + n) & 0xFFFF)
            ts = (self.ts_base_ns + cfg.send_offset_ns(n) + cfg.pres_offset_ns) & 0xFFFFFFFF
            struct.pack_into(">I", h, ts_off, ts)
            frames.append(bytes(h) + payload[i].tobytes() + tail)
        self.n += npk
        if src_end:
            self.ended = True
        return frames, first


# --------------------------------------------------------------------------------------------
# Sender (builder thread + paced transmit thread)
# --------------------------------------------------------------------------------------------
class Talker:
    """Paced sender. Each interface is a 'lane'; copy A goes to lane 0, copy B to lane 0 ('same') or 1 ('second').

    Events are (time_ns, frame, path) with time_ns relative to the stream start. A batch only holds events
    earlier than the first packet of the next batch; later ones (copy B delayed past the batch end) are
    carried over, so batches never overlap in time on a lane."""

    # Behind schedule by up to this much: send the packets that are due back-to-back (keeps the average
    # rate exact). More than this: shift the schedule instead, to avoid a long burst at line rate.
    CATCH_UP_NS = 5_000_000

    def __init__(self, pcap):
        self.pcap = pcap
        self.stop_evt = threading.Event()
        self.lock = threading.Lock()
        self.threads = []
        self.handles = []
        self.reset_stats()

    def reset_stats(self):
        self.sent = {"A": 0, "B": 0}
        self.dropped = {"A": 0, "B": 0}  # copies left out on purpose (loss injection)
        self.late = 0       # schedule shifted (stream lost that much time)
        self.catch_up = 0   # batches sent partly back-to-back to get back on schedule
        self.errors = []
        self.t_start = None
        self.running = False

    @property
    def sent_total(self):
        return self.sent["A"] + self.sent["B"]

    def start(self, devices, cfg, source, mode, batch_ms, seed=None):
        """devices: interface name of copy A, or {'A': name, 'B': name} (B used when replication = 'second')."""
        if isinstance(devices, str):
            devices = {"A": devices}
        self.reset_stats()
        self.stop_evt.clear()
        self.cfg = cfg
        self.handles = [self.pcap.open(devices["A"])]
        if cfg.replication == "second":
            if not devices.get("B"):
                self.pcap.close(self.handles[0])
                raise ValueError("replication 'second' needs interface B")
            try:
                self.handles.append(self.pcap.open(devices["B"]))
            except Exception:
                self.pcap.close(self.handles[0])
                raise
        self.path_lane = {"A": 0, "B": len(self.handles) - 1}
        self.carry = [[] for _ in self.handles]
        self.rng = random.Random(seed)
        self.builder = PacketBuilder(cfg, source)
        self.batch = max(1, int(round(batch_ms / 1000.0 * cfg.pps)))
        self.t_base = time.perf_counter_ns() + 50_000_000
        self.t_start = self.t_base
        self.running = True
        if mode == "sendqueue" and self.pcap.has_sendqueue:
            self.queues = [queue.Queue(maxsize=3) for _ in self.handles]
            self.lanes_left = len(self.handles)
            self.threads = [threading.Thread(target=self._build_loop, daemon=True)] + \
                           [threading.Thread(target=self._queue_send_loop, args=(lane,), daemon=True)
                            for lane in range(len(self.handles))]
        else:
            sys.setswitchinterval(0.0002)
            self.lanes_left = 1
            self.threads = [threading.Thread(target=self._busy_send_loop, daemon=True)]
        for t in self.threads:
            t.start()

    def stop(self):
        self.stop_evt.set()

    def _fail(self, msg):
        self.errors.append(msg)
        self.stop_evt.set()

    def _next_batch(self, count):
        """Send events of the next batch per lane, sorted by time; None when the stream is over."""
        cfg = self.cfg
        frames, first = self.builder.build(count)
        new = [[] for _ in self.handles]
        if frames:
            paths = ("A",) if cfg.replication == "off" else ("A", "B")
            for i, frame in enumerate(frames):
                t = cfg.send_offset_ns(first + i)
                for p in paths:
                    if cfg.drop_pct[p] > 0.0 and self.rng.random() * 100.0 < cfg.drop_pct[p]:
                        self.dropped[p] += 1
                        continue
                    new[self.path_lane[p]].append((t + (cfg.copy_b_delay_ns if p == "B" else 0), frame, p))
            cutoff = cfg.send_offset_ns(first + len(frames))
        elif any(self.carry):
            cutoff = None                      # end of stream: flush what was carried over
        else:
            return None
        out = []
        for lane in range(len(self.handles)):
            ev = sorted(self.carry[lane] + new[lane], key=lambda e: e[0])  # stable: A before B at equal time
            if cutoff is None:
                now_ev, self.carry[lane] = ev, []
            else:
                now_ev = [e for e in ev if e[0] < cutoff]
                self.carry[lane] = [e for e in ev if e[0] >= cutoff]
            out.append(now_ev)
        return out

    def _build_loop(self):
        try:
            while not self.stop_evt.is_set():
                lanes = self._next_batch(self.batch)
                if lanes is None:
                    break
                for lane, ev in enumerate(lanes):
                    if not ev:
                        continue
                    start_us = ev[0][0] // 1000
                    squeue, size = self.pcap.make_queue([f for _, f, _ in ev], [t // 1000 - start_us for t, _, _ in ev])
                    while True:
                        try:
                            self.queues[lane].put((squeue, size, ev), timeout=0.1)
                            break
                        except queue.Full:
                            if self.stop_evt.is_set():
                                self.pcap.destroy_queue(squeue)
                                return
        except Exception as e:  # noqa: BLE001 - report any build error in the GUI
            self._fail(f"build: {e}")
        finally:
            for q in self.queues:
                q.put(None)

    def _wait_until(self, target_ns):
        while True:
            left = target_ns - time.perf_counter_ns()
            if left <= 0:
                return
            if left > 2_000_000:
                time.sleep((left - 1_500_000) / 1e9)

    def _queue_send_loop(self, lane):
        t_base = self.t_base
        period = self.cfg.send_offset_ns(1)
        handle = self.handles[lane]
        try:
            while True:
                item = self.queues[lane].get()
                if item is None:
                    break
                squeue, size, ev = item
                if self.stop_evt.is_set():
                    self.pcap.destroy_queue(squeue)
                    continue
                target = t_base + ev[0][0]
                now = time.perf_counter_ns()
                lateness = now - target
                if lateness > self.CATCH_UP_NS:
                    self.late += 1
                    t_base += lateness
                elif lateness > period:
                    # requeue with the packets already due at relative time 0
                    self.catch_up += 1
                    self.pcap.destroy_queue(squeue)
                    squeue, size = self.pcap.make_queue([f for _, f, _ in ev],
                                                        [max(0, t_base + t - now) // 1000 for t, _, _ in ev])
                else:
                    self._wait_until(target)
                sent = self.pcap.transmit_queue(handle, squeue, sync=True)
                self.pcap.destroy_queue(squeue)
                if sent < size:
                    self._fail("pcap_sendqueue_transmit: " + self.pcap.error(handle))
                    continue
                for _, _, p in ev:
                    self.sent[p] += 1
        finally:
            self._lane_done()

    def _busy_send_loop(self):
        t_base = self.t_base
        small = max(1, min(self.batch, 4))
        try:
            while not self.stop_evt.is_set():
                lanes = self._next_batch(small)
                if lanes is None:
                    break
                events = sorted(((t, lane, f, p) for lane, ev in enumerate(lanes) for t, f, p in ev),
                                key=lambda e: e[0])
                for t, lane, frame, p in events:
                    target = t_base + t
                    lateness = time.perf_counter_ns() - target
                    if lateness > self.CATCH_UP_NS:
                        self.late += 1
                        t_base += lateness
                    elif lateness < 0:
                        self._wait_until(target)
                    # 0 <= lateness <= CATCH_UP_NS: due already, send now to catch up
                    if self.pcap.send(self.handles[lane], frame) != 0:
                        self._fail("pcap_sendpacket: " + self.pcap.error(self.handles[lane]))
                        break
                    self.sent[p] += 1
                    if self.stop_evt.is_set():
                        break
        except Exception as e:  # noqa: BLE001
            self._fail(f"send: {e}")
        finally:
            self._lane_done()

    def _lane_done(self):
        with self.lock:
            self.lanes_left -= 1
            last = self.lanes_left == 0
        if not last:
            return
        for h in self.handles:
            try:
                self.pcap.close(h)
            except Exception:  # noqa: BLE001
                pass
        sys.setswitchinterval(0.005)
        self.running = False


# --------------------------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------------------------
DEFAULTS = {
    "device": "", "src_mac": "02:00:00:00:00:01", "dst_mac": "91:E0:F0:00:FE:00",
    "stream_id": "0x0000000000000001", "vlan": False, "vid": "0", "pcp": "3",
    "pres_offset_ms": "2.0",
    "sample_rate": "48000", "channels": "8", "bit_depth": "32", "samples_per_frame": "12",
    "link_speed": "1000.0", "frames_to_send": "0",
    "source": "sine", "wav_path": "", "loop": True, "resample": True, "missing": "repeat",
    "sine_freq": "1000.0", "sine_volume": "0.2", "file_gain": "1.0",
    "mode": "sendqueue", "batch_ms": "20",
    "req_sample_rate": "48000", "req_channels": "8", "req_bit_depth": "32",
    "req_samples_per_frame": "12", "req_max_payload": "384",
    "rtag": False, "rtag_seq_start": "0", "replication": "off", "device_b": "",
    "copy_b_delay_us": "0", "drop_a_pct": "0", "drop_b_pct": "0",
    "profile_dir": "",
}


def load_settings():
    s = dict(DEFAULTS)
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            s.update(json.load(f))
    except (OSError, ValueError):
        pass
    return s


def save_settings(d):
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)


def run_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    pcap = Npcap()
    devices = pcap.devices()
    macs = adapter_macs()
    talker = Talker(pcap)
    s = load_settings()

    root = tk.Tk()
    root.title("AVTP AAF Talker")
    v = {k: (tk.BooleanVar(value=val) if isinstance(val, bool) else tk.StringVar(value=str(val)))
         for k, val in s.items()}
    dev_labels = [f"{desc}  [{name}]" for name, desc in devices]
    dev_var = tk.StringVar()
    dev_b_var = tk.StringVar()
    for name, label in zip((d[0] for d in devices), dev_labels):
        if name == s["device"]:
            dev_var.set(label)
        if name == s.get("device_b"):
            dev_b_var.set(label)

    # START / STOP bar stays at the bottom, settings bar at the top; everything else scrolls
    bar = ttk.Frame(root, padding=8)
    bar.pack(side="bottom", fill="x")
    top = ttk.Frame(root, padding=(8, 6, 8, 0))
    top.pack(side="top", fill="x")
    ttk.Label(top, text="Settings:").pack(side="left")
    save_as_btn = ttk.Button(top, text="Save as...")
    load_btn = ttk.Button(top, text="Load...")
    save_as_btn.pack(side="left", padx=(6, 0))
    load_btn.pack(side="left", padx=6)
    note = tk.StringVar(value="saved automatically to " + SETTINGS_FILE)
    ttk.Label(top, textvariable=note, foreground="gray").pack(side="left", padx=6)
    outer = ttk.Frame(root)
    outer.pack(side="top", fill="both", expand=True)
    canvas = tk.Canvas(outer, highlightthickness=0)
    vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=vsb.set)
    vsb.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    body = ttk.Frame(canvas)
    body.columnconfigure(0, weight=1)
    body_win = canvas.create_window((0, 0), window=body, anchor="nw")
    body.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda e: canvas.itemconfigure(body_win, width=e.width))

    def on_wheel(e):
        if body.winfo_height() > canvas.winfo_height():
            canvas.yview_scroll(int(-e.delta / 120), "units")
    canvas.bind_all("<MouseWheel>", on_wheel)
    root.geometry("780x720")

    def section(title, row):
        frm = ttk.LabelFrame(body, text=title, padding=8)
        frm.grid(row=row, column=0, sticky="ew", padx=8, pady=4)
        frm.columnconfigure(1, weight=1)
        return frm

    def field(frm, r, label, widget):
        ttk.Label(frm, text=label).grid(row=r, column=0, sticky="w", pady=2, padx=(0, 8))
        widget.grid(row=r, column=1, sticky="ew", pady=2)
        return widget

    def selected_device(var=None):
        label = (var or dev_var).get()
        for (name, _), lab in zip(devices, dev_labels):
            if lab == label:
                return name
        return ""

    # Network
    net = section("Network", 0)
    dev_box = field(net, 0, "Interface", ttk.Combobox(net, textvariable=dev_var, values=dev_labels,
                                                      state="readonly", width=70))

    def on_dev(_evt=None):
        name = selected_device()
        mac = mac_for_device(name, macs) if name else ""
        if mac:
            v["src_mac"].set(mac)
    dev_box.bind("<<ComboboxSelected>>", on_dev)
    field(net, 1, "Source MAC", ttk.Entry(net, textvariable=v["src_mac"]))
    field(net, 2, "Destination MAC", ttk.Entry(net, textvariable=v["dst_mac"]))
    field(net, 3, "Stream ID", ttk.Entry(net, textvariable=v["stream_id"]))
    vl = ttk.Frame(net)
    ttk.Checkbutton(vl, text="VLAN tag", variable=v["vlan"]).pack(side="left")
    ttk.Label(vl, text="  VID").pack(side="left")
    ttk.Entry(vl, textvariable=v["vid"], width=6).pack(side="left")
    ttk.Label(vl, text="  PCP").pack(side="left")
    ttk.Entry(vl, textvariable=v["pcp"], width=4).pack(side="left")
    field(net, 4, "", vl)
    field(net, 5, "Presentation offset (ms)", ttk.Entry(net, textvariable=v["pres_offset_ms"]))

    # Stream
    st = section("Stream", 1)
    field(st, 0, "Sample rate (Hz)", ttk.Combobox(st, textvariable=v["sample_rate"],
                                                  values=[str(r) for r in sorted(NSR_CODES)]))
    field(st, 1, "Channels", ttk.Combobox(st, textvariable=v["channels"], values=[str(c) for c in range(1, 17)]))
    field(st, 2, "Bit depth", ttk.Combobox(st, textvariable=v["bit_depth"], values=["16", "24", "32"],
                                           state="readonly"))
    field(st, 3, "Samples / frame", ttk.Entry(st, textvariable=v["samples_per_frame"]))
    field(st, 4, "Link speed (Mbps)", ttk.Combobox(st, textvariable=v["link_speed"],
                                                   values=["10.0", "100.0", "1000.0"]))
    field(st, 5, "Frames to send", ttk.Entry(st, textvariable=v["frames_to_send"]))
    ttk.Label(st, text="Frames to send = 0 means keep sending until STOP is pressed",
              foreground="gray").grid(row=6, column=0, columnspan=2, sticky="w")

    # IEEE 802.1CB
    cb = section("IEEE 802.1CB (FRER talker)", 2)
    rt = ttk.Frame(cb)
    ttk.Checkbutton(rt, text="Add R-TAG (EtherType 0xF1C1)", variable=v["rtag"]).pack(side="left")
    ttk.Label(rt, text="   Start sequence (0..65535 or 0x..)").pack(side="left")
    ttk.Entry(rt, textvariable=v["rtag_seq_start"], width=8).pack(side="left", padx=(4, 0))
    field(cb, 0, "", rt)
    rp = ttk.Frame(cb)
    ttk.Combobox(rp, textvariable=v["replication"], values=list(REPLICATION_MODES), state="readonly",
                 width=8).pack(side="left")
    ttk.Label(rp, text="  off = 1 copy, same = 2 copies on interface A, second = copy B on interface B",
              foreground="gray").pack(side="left")
    field(cb, 1, "Replication", rp)
    field(cb, 2, "Interface B", ttk.Combobox(cb, textvariable=dev_b_var, values=dev_labels, state="readonly",
                                             width=70))
    dl = ttk.Frame(cb)
    ttk.Label(dl, text="Copy B delay (us)").pack(side="left")
    ttk.Entry(dl, textvariable=v["copy_b_delay_us"], width=8).pack(side="left", padx=(4, 16))
    ttk.Label(dl, text="Drop copy A (%)").pack(side="left")
    ttk.Entry(dl, textvariable=v["drop_a_pct"], width=6).pack(side="left", padx=(4, 16))
    ttk.Label(dl, text="Drop copy B (%)").pack(side="left")
    ttk.Entry(dl, textvariable=v["drop_b_pct"], width=6).pack(side="left", padx=(4, 0))
    field(cb, 3, "", dl)
    ttk.Label(cb, text="Both copies are identical (same R-TAG sequence). Drops are random, to check that the "
                       "listener's sequence recovery fills the gaps.",
              foreground="gray", wraplength=700, justify="left").grid(row=4, column=0, columnspan=2, sticky="w")

    # Source
    sc = section("Audio source", 3)
    rb = ttk.Frame(sc)
    ttk.Radiobutton(rb, text="Sine", value="sine", variable=v["source"]).pack(side="left")
    ttk.Radiobutton(rb, text="Audio file (WAV / MP3 / FLAC / OGG)", value="wav",
                    variable=v["source"]).pack(side="left", padx=12)
    field(sc, 0, "Source", rb)
    wf = ttk.Frame(sc)
    wf.columnconfigure(0, weight=1)
    ttk.Entry(wf, textvariable=v["wav_path"]).grid(row=0, column=0, sticky="ew")

    def browse():
        p = filedialog.askopenfilename(filetypes=[("Audio", "*.wav *.mp3 *.flac *.ogg"), ("All", "*.*")])
        if p:
            v["wav_path"].set(p)
            v["source"].set("wav")
    ttk.Button(wf, text="Browse...", command=browse).grid(row=0, column=1, padx=(4, 0))
    field(sc, 1, "Audio file", wf)
    lp = ttk.Frame(sc)
    ttk.Checkbutton(lp, text="Loop", variable=v["loop"]).pack(side="left")
    ttk.Checkbutton(lp, text="Resample to stream rate", variable=v["resample"]).pack(side="left", padx=(12, 0))
    ttk.Label(lp, text="   Missing channels:").pack(side="left")
    ttk.Combobox(lp, textvariable=v["missing"], values=["repeat", "silence"], state="readonly",
                 width=9).pack(side="left")
    field(sc, 2, "", lp)
    field(sc, 3, "Sine freq (Hz)", ttk.Entry(sc, textvariable=v["sine_freq"]))
    field(sc, 4, "Sine volume (0-1)", ttk.Entry(sc, textvariable=v["sine_volume"]))
    field(sc, 5, "File gain", ttk.Entry(sc, textvariable=v["file_gain"]))
    ttk.Label(sc, text="Sine volume affects the sine tone only. File gain 1.0 keeps the file at its original level.",
              foreground="gray").grid(row=6, column=0, columnspan=2, sticky="w")

    # Receiver requirement
    rq = section("Receiver requirement", 4)
    rqf = ttk.Frame(rq)
    for label, key, width in (("Rate", "req_sample_rate", 8), ("Ch", "req_channels", 4),
                              ("Bit", "req_bit_depth", 4), ("Samples", "req_samples_per_frame", 5),
                              ("Max payload (byte)", "req_max_payload", 6)):
        ttk.Label(rqf, text=label).pack(side="left", padx=(0, 4))
        ttk.Entry(rqf, textvariable=v[key], width=width).pack(side="left", padx=(0, 12))
    field(rq, 0, "", rqf)

    # Pacing
    pc = section("Pacing", 5)
    pm = ttk.Frame(pc)
    ttk.Radiobutton(pm, text="Npcap send queue (sync, recommended)", value="sendqueue",
                    variable=v["mode"]).pack(side="left")
    ttk.Radiobutton(pm, text="Busy-wait per packet", value="busy", variable=v["mode"]).pack(side="left", padx=12)
    field(pc, 0, "Mode", pm)
    field(pc, 1, "Batch (ms)", ttk.Entry(pc, textvariable=v["batch_ms"]))

    info = tk.StringVar()
    warn = tk.StringVar()
    status = tk.StringVar(value="Idle")
    comp = section("Computed", 6)
    tk.Label(comp, textvariable=info, font=("Consolas", 9), justify="left", anchor="w").grid(
        row=0, column=0, sticky="w")
    ttk.Label(body, textvariable=warn, foreground="#c05000", padding=(10, 2), wraplength=640,
              justify="left").grid(row=7, column=0, sticky="w")
    start_btn = ttk.Button(bar, text="START")
    stop_btn = ttk.Button(bar, text="STOP", state="disabled")
    start_btn.pack(side="left")
    stop_btn.pack(side="left", padx=8)
    ttk.Label(bar, textvariable=status).pack(side="left", padx=8)

    def snapshot():
        d = {k: var.get() for k, var in v.items()}
        d["device"] = selected_device()
        d["device_b"] = selected_device(dev_b_var)
        return d

    def apply_settings(d):
        """Put a settings dict into the GUI (unknown keys ignored); returns the interfaces not found."""
        for k, var in v.items():
            if k in d and k not in ("device", "device_b", "profile_dir"):
                var.set(bool(d[k]) if isinstance(var, tk.BooleanVar) else str(d[k]))
        missing = []
        for key, var in (("device", dev_var), ("device_b", dev_b_var)):
            name = d.get(key, "")
            label = next((lab for (n, _), lab in zip(devices, dev_labels) if n == name), "")
            if label:
                var.set(label)
            elif name:
                missing.append("A" if key == "device" else "B")
        return missing

    save_job = [None]

    def save_now():
        if save_job[0] is not None:
            root.after_cancel(save_job[0])
            save_job[0] = None
        try:
            save_settings(snapshot())
        except OSError as e:
            note.set(f"settings not saved: {e}")

    def schedule_save():
        # debounce: typing in an entry writes the variable on every key
        if save_job[0] is not None:
            root.after_cancel(save_job[0])
        save_job[0] = root.after(1000, save_now)

    def profile_dir():
        return v["profile_dir"].get() or os.path.dirname(SETTINGS_FILE)

    def save_as():
        p = filedialog.asksaveasfilename(title="Save settings as", initialdir=profile_dir(),
                                         initialfile="avtp_talker_settings.json", defaultextension=".json",
                                         filetypes=[("Talker settings", "*.json"), ("All", "*.*")])
        if not p:
            return
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(snapshot(), f, indent=2)
        except OSError as e:
            messagebox.showerror("AVTP Talker", f"Cannot save settings:\n{e}")
            return
        v["profile_dir"].set(os.path.dirname(p))
        note.set(f"saved {p}")

    def load_from():
        if talker.running:
            messagebox.showinfo("AVTP Talker", "Stop the stream before loading other settings.")
            return
        p = filedialog.askopenfilename(title="Load settings", initialdir=profile_dir(),
                                       filetypes=[("Talker settings", "*.json"), ("All", "*.*")])
        if not p:
            return
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            if not isinstance(d, dict) or not set(d) & set(DEFAULTS):
                raise ValueError("not an AVTP talker settings file")
        except (OSError, ValueError) as e:
            messagebox.showerror("AVTP Talker", f"Cannot load settings:\n{e}")
            return
        missing = apply_settings(d)
        v["profile_dir"].set(os.path.dirname(p))
        text = f"loaded {p}"
        if missing:
            text += "  (interface " + " / ".join(missing) + " not on this PC: select it)"
        note.set(text)

    info_cache = {}
    data_cache = {}
    peak_cache = {}

    def file_info(path):
        if path not in info_cache:
            info_cache.clear()
            info_cache[path] = audio_info(path)
        return info_cache[path]

    def file_data(path, rate, do_resample):
        """Decoded (and resampled) file, kept until another file or rate is used."""
        key = (path, rate if do_resample else None)
        if key not in data_cache:
            data_cache.clear()
            status.set("Loading audio file...")
            root.update_idletasks()
            src_rate, data = read_audio(path)
            data_cache[key] = resample(data, src_rate, rate) if do_resample else data
            peak_cache[path] = float(np.abs(data_cache[key]).max())
            refresh_info()
        return data_cache[key]

    def refresh_info():
        d = snapshot()
        msgs = []
        try:
            cfg = StreamConfig(d)
            req = Requirement(d)
            info.set(computed_text(cfg, req))
            if cfg.bandwidth_mbps * cfg.link_copies > cfg.link_mbps:
                msgs.append("Stream does not fit the link speed.")
            if cfg.payload_len > req.max_payload:
                msgs.append(f"The receiver accepts at most {req.max_payload} B per packet.")
            if cfg.rtag:
                msgs.append("R-TAG on: the listener must remove it before the host (Egress Treatment with "
                            "RemoveRTag), otherwise EthIf sees EtherType 0xF1C1. Switch ports on the path need "
                            "max frame length / MAXSDU + 6 byte.")
            if cfg.replication != "off" and not cfg.rtag:
                msgs.append("Replication without R-TAG: the listener cannot tell the copies apart.")
            if cfg.replication == "second":
                if not d["device_b"]:
                    msgs.append("Replication 'second': select Interface B.")
                elif d["device_b"] == d["device"]:
                    msgs.append("Interface B is interface A: use replication 'same' instead.")
            if d["mode"] == "busy" and cfg.replication == "second":
                msgs.append("Busy-wait mode sends both interfaces from one thread; send queue mode is preferred.")
        except (ValueError, ZeroDivisionError) as e:
            info.set(f"Invalid setting: {e}")
        if d["source"] == "wav" and d["wav_path"]:
            try:
                rate, nch, frames = file_info(d["wav_path"])
                msgs.append(f"File: {rate} Hz, {nch} channel(s), {frames / rate:.1f} s.")
                if str(rate) != d["sample_rate"]:
                    if d["resample"]:
                        msgs.append(f"Resampled {rate} -> {d['sample_rate']} Hz when START is pressed.")
                    else:
                        msgs.append(f"WARNING: file is {rate} Hz, not {d['sample_rate']} Hz and Resample is off: "
                                    f"it plays at the wrong speed.")
                if nch < int(d["channels"] or 0):
                    msgs.append(f"Missing channels: {d['missing']}.")
                peak = peak_cache.get(d["wav_path"])
                if peak is not None and peak * float(d["file_gain"] or 0) > 1.0:
                    msgs.append(f"Peak {peak:.3f} x File gain {d['file_gain']} clips: "
                                f"use File gain <= {0.99 / peak:.2f}.")
            except (OSError, ValueError, RuntimeError) as e:
                msgs.append(f"File: {e}")
        if not pcap.has_sendqueue and d["mode"] == "sendqueue":
            msgs.append("This wpcap.dll has no send queue API, busy-wait mode will be used.")
        warn.set("\n".join(msgs))

    def tick():
        if talker.running or talker.t_start:
            el = max(0.0, (time.perf_counter_ns() - talker.t_start) / 1e9) if talker.t_start else 0.0
            rate = talker.sent["A"] / el if el > 0.5 else 0.0
            txt = f"Sent A {talker.sent['A']}"
            if talker.sent["B"] or talker.dropped["B"]:
                txt += f", B {talker.sent['B']}"
            if talker.dropped["A"] or talker.dropped["B"]:
                txt += f"  |  dropped A {talker.dropped['A']}, B {talker.dropped['B']}"
            txt += f"  |  {rate:.0f} pkt/s  |  catch-up {talker.catch_up}  |  late {talker.late}  |  {el:.1f} s"
            if talker.errors:
                txt += "  |  " + talker.errors[-1]
            status.set(("Running: " if talker.running else "Stopped: ") + txt)
        if not talker.running:
            start_btn.config(state="normal")
            stop_btn.config(state="disabled")
        root.after(300, tick)

    def on_start():
        d = snapshot()
        if not d["device"]:
            messagebox.showerror("AVTP Talker", "Select an interface first.")
            return
        try:
            cfg = StreamConfig(d)
            if d["source"] == "wav":
                data = file_data(d["wav_path"], cfg.rate, bool(d["resample"]))
                source = FileSource(data, cfg.channels, d["missing"], float(d["file_gain"]), bool(d["loop"]))
            else:
                source = SineSource(float(d["sine_freq"]), float(d["sine_volume"]), cfg.channels, cfg.rate)
            talker.start({"A": d["device"], "B": d["device_b"]}, cfg, source, d["mode"], float(d["batch_ms"]))
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("AVTP Talker", str(e))
            return
        try:
            save_settings(d)
        except OSError:
            pass
        start_btn.config(state="disabled")
        stop_btn.config(state="normal")

    def on_close():
        save_now()
        talker.stop()
        root.after(200, root.destroy)

    def on_change(*_):
        refresh_info()
        schedule_save()

    start_btn.config(command=on_start)
    stop_btn.config(command=talker.stop)
    save_as_btn.config(command=save_as)
    load_btn.config(command=load_from)
    root.protocol("WM_DELETE_WINDOW", on_close)
    for var in v.values():
        var.trace_add("write", on_change)
    dev_var.trace_add("write", on_change)
    dev_b_var.trace_add("write", on_change)
    refresh_info()
    tick()
    root.mainloop()


# --------------------------------------------------------------------------------------------
# CLI helpers
# --------------------------------------------------------------------------------------------
def list_devices():
    if npcap_admin_only() and not is_admin():
        print("Npcap is restricted to Administrators: run this from an Administrator prompt.")
        return
    pcap = Npcap()
    macs = adapter_macs()
    for i, (name, desc) in enumerate(pcap.devices()):
        print(f"[{i}] {desc}\n     {name}  MAC {mac_for_device(name, macs) or '?'}")
    print("send queue API:", "yes" if pcap.has_sendqueue else "no")


def selftest():
    s = dict(DEFAULTS)
    cfg = StreamConfig(s)
    req = Requirement(s)
    print(computed_text(cfg, req))
    s["vlan"] = True
    print("\n-- with VLAN tag --\n" + computed_text(StreamConfig(s), req) + "\n")
    b = PacketBuilder(cfg, SineSource(1000.0, 0.2, cfg.channels, cfg.rate))
    f = b.build(4)[0][0]
    print("eth  :", f[:14].hex(" "))
    print("avtp :", f[14:38].hex(" "))
    print("pay  :", f[38:70].hex(" "), "...")
    s.update(vlan=True, vid="10", rtag=True, rtag_seq_start="100")
    f = PacketBuilder(StreamConfig(s), SineSource(1000.0, 0.2, cfg.channels, cfg.rate)).build(1)[0][0]
    print("with VLAN 10 + R-TAG (seq 100):", f[:24].hex(" "), "| avtp ...")
    t0 = time.perf_counter()
    nb = 0
    while time.perf_counter() - t0 < 1.0:
        nb += len(b.build(80)[0])
    print(f"build speed: {nb / (time.perf_counter() - t0):.0f} packets/s")


if __name__ == "__main__":
    if "--list" in sys.argv:
        list_devices()
    elif "--selftest" in sys.argv:
        selftest()
    elif npcap_admin_only() and not is_admin():
        # Npcap would otherwise block on its own UAC helper
        if not relaunch_as_admin():
            print("Npcap is restricted to Administrators: run this script from an Administrator prompt.")
    else:
        run_gui()
