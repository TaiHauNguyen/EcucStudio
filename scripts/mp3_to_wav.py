#!/usr/bin/env python3
"""Convert MP3 (or FLAC / OGG / WAV) files to PCM WAV, e.g. as input for avtp_talker or an ECU audio test.

    python mp3_to_wav.py song.mp3                          # song.wav next to it, same rate / channels, 16 bit
    python mp3_to_wav.py song.mp3 -o out --rate 48000 --channels 2 --bits 24
    python mp3_to_wav.py music_dir -r -o out --rate 48000  # every audio file in the folder tree
    python mp3_to_wav.py song.mp3 --start 30 --duration 10 --gain -3

Decoding uses the soundfile package (libsndfile 1.1 or newer reads MP3): pip install soundfile numpy.
A different --rate is reached with a windowed-sinc polyphase resampler (same as avtp_talker). Channels:
more output channels repeat the source ones, one output channel is the average of all source channels,
otherwise the first N are kept. Samples beyond full scale are clipped and counted in the report.
A folder input converts its .mp3 / .flac / .ogg files (a .wav is converted only when named directly);
existing outputs are skipped unless --overwrite. Exit code 1 when a file could not be converted.
"""
from __future__ import annotations

import argparse
import math
import os
import sys

import numpy as np

SCAN_EXTENSIONS = (".mp3", ".flac", ".ogg")  # folder scans skip .wav so earlier outputs are not picked up
SUBTYPES = {"16": "PCM_16", "24": "PCM_24", "32": "PCM_32", "float": "FLOAT"}


def _soundfile():
    try:
        import soundfile
    except ImportError:
        sys.exit("needs the soundfile package: pip install soundfile")
    return soundfile


def resample(x, src_rate, dst_rate, half_taps=16):
    """Polyphase windowed-sinc (Kaiser) resampler; x is (n, channels), returns float32 (m, channels)."""
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


def map_channels(data, out_ch):
    """Fit source channels to out_ch: mono is the average, more channels repeat, fewer keep the first."""
    nsrc = data.shape[1]
    if out_ch == nsrc:
        return data
    if out_ch == 1:
        return data.mean(axis=1, keepdims=True, dtype=np.float32)
    if out_ch < nsrc:
        return np.ascontiguousarray(data[:, :out_ch])
    return data[:, [c % nsrc for c in range(out_ch)]]


def collect(inputs, recursive):
    """Audio files named on the command line or found in the given folders."""
    files = []
    for p in inputs:
        if os.path.isdir(p):
            if recursive:
                for root, _, names in os.walk(p):
                    files += [os.path.join(root, n) for n in sorted(names) if n.lower().endswith(SCAN_EXTENSIONS)]
            else:
                files += [os.path.join(p, n) for n in sorted(os.listdir(p))
                          if n.lower().endswith(SCAN_EXTENSIONS) and os.path.isfile(os.path.join(p, n))]
        elif os.path.isfile(p):
            files.append(p)
    return files


def out_path(src, base_dir, out_dir):
    """Output .wav path; keeps the sub-folder of src below base_dir when writing to out_dir."""
    stem = os.path.splitext(os.path.basename(src))[0] + ".wav"
    if not out_dir:
        return os.path.join(os.path.dirname(src), stem)
    rel = os.path.relpath(os.path.dirname(src), base_dir) if base_dir else "."
    return os.path.normpath(os.path.join(out_dir, rel, stem))


def convert(sf, src, dst, args):
    data, rate = sf.read(src, dtype="float32", always_2d=True)
    first = int(round(args.start * rate))
    data = data[first:]
    if args.duration is not None:
        data = data[:int(round(args.duration * rate))]
    if data.shape[0] == 0:
        raise ValueError("no samples (check --start / --duration)")
    src_rate, src_ch = rate, data.shape[1]
    out_rate = args.rate or rate
    data = resample(data, rate, out_rate)
    data = map_channels(data, args.channels or src_ch)
    if args.gain:
        data = data * np.float32(10.0 ** (args.gain / 20.0))
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    clipped = int(np.count_nonzero(np.abs(data) > 1.0)) if args.bits != "float" else 0
    if clipped:
        data = np.clip(data, -1.0, 1.0)
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    sf.write(dst, data, out_rate, subtype=SUBTYPES[args.bits], format="WAV")
    peak_db = 20.0 * math.log10(peak) if peak > 0 else float("-inf")
    note = f", {clipped} samples clipped (try --gain {-math.ceil(peak_db * 10) / 10:.1f})" if clipped else ""
    fmt = "float" if args.bits == "float" else f"{args.bits} bit"
    print(f"{src} -> {dst}: {src_rate} Hz {src_ch} ch -> {out_rate} Hz {data.shape[1]} ch {fmt}, "
          f"{data.shape[0] / out_rate:.2f} s, peak {peak_db:.1f} dBFS{note}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Convert MP3 / FLAC / OGG / WAV files to PCM WAV.")
    ap.add_argument("inputs", nargs="+", help="audio files or folders")
    ap.add_argument("-o", "--out-dir", help="output folder (default: next to each input file)")
    ap.add_argument("-r", "--recursive", action="store_true", help="also search sub-folders of folder inputs")
    ap.add_argument("--rate", type=int, help="output sample rate in Hz, e.g. 48000 (default: keep)")
    ap.add_argument("--channels", type=int, help="output channel count (default: keep)")
    ap.add_argument("--bits", choices=sorted(SUBTYPES), default="16", help="sample format (default: 16)")
    ap.add_argument("--start", type=float, default=0.0, help="skip the first N seconds")
    ap.add_argument("--duration", type=float, help="keep at most N seconds")
    ap.add_argument("--gain", type=float, default=0.0, help="gain in dB, e.g. -3")
    ap.add_argument("--overwrite", action="store_true", help="replace existing .wav files")
    args = ap.parse_args(argv)
    if args.rate is not None and args.rate <= 0:
        ap.error("--rate must be positive")
    if args.channels is not None and args.channels <= 0:
        ap.error("--channels must be positive")
    if args.start < 0 or (args.duration is not None and args.duration <= 0):
        ap.error("--start must be >= 0 and --duration > 0")

    sf = _soundfile()
    failed = 0
    for inp in args.inputs:
        if not os.path.exists(inp):
            print(f"not found: {inp}", file=sys.stderr)
            failed += 1
            continue
        base = inp if os.path.isdir(inp) else None
        for src in collect([inp], args.recursive):
            dst = out_path(src, base, args.out_dir)
            if os.path.abspath(dst) == os.path.abspath(src):
                print(f"skip {src}: output would replace the input (use -o)", file=sys.stderr)
                failed += 1
                continue
            if os.path.exists(dst) and not args.overwrite:
                print(f"skip {src}: {dst} exists (use --overwrite)", file=sys.stderr)
                continue
            try:
                convert(sf, src, dst, args)
            except Exception as e:  # keep going with the other files
                print(f"error {src}: {e}", file=sys.stderr)
                failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
