"""scripts/avtp_talker.py: IEEE 1722 AAF packets, WAV reading, stream summary, paced sending (fake Npcap)."""
import importlib.util
import os
import shutil
import struct
import tempfile
import time
import unittest
import wave

try:
    import numpy as np
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "scripts", "avtp_talker.py")
if HAVE_NUMPY:
    _spec = importlib.util.spec_from_file_location("avtp_talker", SCRIPT)
    at = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(at)


def settings(**kw):
    s = dict(at.DEFAULTS)
    s.update({k: str(v) if not isinstance(v, bool) else v for k, v in kw.items()})
    return s


class FakePcap:
    """Records send times; the send queue is emulated by waiting on the relative timestamps."""
    has_sendqueue = True

    def __init__(self):
        self.times = []

    def open(self, name):
        return 1

    def close(self, handle):
        pass

    def error(self, handle):
        return "fake"

    def send(self, handle, frame):
        self.times.append(time.perf_counter_ns())
        return 0

    def make_queue(self, frames, rel_us):
        return list(rel_us), sum(len(f) + 16 for f in frames)

    def transmit_queue(self, handle, squeue, sync=True):
        t0 = time.perf_counter_ns()
        for us in squeue:
            while time.perf_counter_ns() - t0 < us * 1000:
                pass
            self.times.append(time.perf_counter_ns())
        return 1 << 30

    def destroy_queue(self, squeue):
        pass


@unittest.skipUnless(HAVE_NUMPY, "numpy is not installed")
class AvtpTalkerTest(unittest.TestCase):
    def build(self, n=2, **kw):
        cfg = at.StreamConfig(settings(**kw))
        b = at.PacketBuilder(cfg, at.SineSource(1000.0, 0.2, cfg.channels, cfg.rate))
        return cfg, b.build(n)[0]

    def test_aaf_header(self):
        cfg, frames = self.build(dst_mac="91:E0:F0:00:FE:01", stream_id="0x1122334455667788")
        f = frames[0]
        self.assertEqual(len(f), 14 + 24 + 384)
        self.assertEqual(f[0:6], bytes.fromhex("91e0f000fe01"))
        self.assertEqual(f[12:14], b"\x22\xf0")
        avtp = f[14:38]
        self.assertEqual(avtp[0:2], b"\x02\x81")                        # AAF, sv = 1, tv = 1
        self.assertEqual(avtp[4:12], bytes.fromhex("1122334455667788"))  # stream_id
        self.assertEqual(avtp[16:20], bytes([0x02, 0x50, 0x08, 0x20]))   # INT_32BIT, 48 kHz, 8 ch, 32 bit
        self.assertEqual(struct.unpack(">H", avtp[20:22])[0], 384)

    def test_sequence_and_timestamp(self):
        cfg, frames = self.build(n=300)
        seq = [f[16] for f in frames]
        self.assertEqual(seq[:3], [0, 1, 2])
        self.assertEqual(seq[256], 0)                                   # 8-bit wrap
        ts = [struct.unpack(">I", f[26:30])[0] for f in frames]
        self.assertEqual((ts[1] - ts[0]) & 0xFFFFFFFF, 250_000)          # 12 samples at 48 kHz

    def test_vlan_tag(self):
        cfg, frames = self.build(vlan=True, vid=5, pcp=3)
        self.assertEqual(frames[0][12:16], bytes([0x81, 0x00, 0x60, 0x05]))
        self.assertEqual(frames[0][16:18], b"\x22\xf0")

    def test_sample_encoding(self):
        cfg = at.StreamConfig(settings(channels=1, samples_per_frame=1))
        b = at.PacketBuilder(cfg, at.SineSource(0.0, 0.0, 1, cfg.rate))
        self.assertEqual(b._to_bytes(np.array([[0.5]])).tobytes(), bytes.fromhex("40000000"))
        for depth, size in ((24, 288), (16, 192)):
            cfg, frames = self.build(bit_depth=depth)
            self.assertEqual(cfg.payload_len, size)
            self.assertEqual(len(frames[0]), 14 + 24 + size)

    def test_min_frame_padding(self):
        cfg, frames = self.build(channels=1, samples_per_frame=1, bit_depth=16)
        self.assertEqual(len(frames[0]), at.ETH_MIN_FRAME)

    def test_wav_and_channels(self):
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "t.wav")
            x = (np.arange(48) * 100).astype("<i2")
            with wave.open(path, "wb") as w:
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(48000)
                w.writeframes(np.stack([x, -x], axis=1).tobytes())
            rate, data = at.read_wav(path)
            self.assertEqual((rate, data.shape), (48000, (48, 2)))
            rep = at.map_channels(data, 8, "repeat")
            self.assertTrue(np.array_equal(rep[:, 4], data[:, 0]) and np.array_equal(rep[:, 7], data[:, 1]))
            self.assertFalse(at.map_channels(data, 8, "silence")[:, 2:].any())
            src = at.WavSource(data, 8, "repeat", 1.0, loop=False)
            cfg = at.StreamConfig(settings())
            b = at.PacketBuilder(cfg, src)
            frames, _ = b.build(10)
            self.assertEqual(len(frames), 4)                            # 48 samples / 12
            self.assertTrue(b.ended)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_resample(self):
        for src, dst in ((44100, 48000), (48000, 44100), (32000, 48000)):
            with self.subTest(src=src, dst=dst):
                x = (0.5 * np.sin(2 * np.pi * 1000.0 * np.arange(src) / src)).astype(np.float32)
                y = at.resample(np.stack([x, -x], axis=1), src, dst)
                self.assertEqual(y.shape, (dst, 2))
                ref = 0.5 * np.sin(2 * np.pi * 1000.0 * np.arange(dst) / dst)
                inner = slice(200, dst - 200)                           # skip the filter edges
                self.assertLess(np.max(np.abs(y[inner, 0] - ref[inner])), 1e-4)
                self.assertLess(np.max(np.abs(y[inner, 1] + ref[inner])), 1e-4)
        same = np.ones((10, 1), np.float32)
        self.assertIs(at.resample(same, 48000, 48000), same)

    def test_audio_file_info_and_read(self):
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "t.wav")
            with wave.open(path, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(44100)
                w.writeframes((np.arange(441) * 50).astype("<i2").tobytes())
            self.assertEqual(at.audio_info(path), (44100, 1, 441))
            rate, data = at.read_audio(path)
            self.assertEqual((rate, data.shape, data.dtype), (44100, (441, 1), np.float32))
            try:
                import soundfile
            except ImportError:
                return
            flac = os.path.join(tmp, "t.flac")
            soundfile.write(flac, data, 44100)
            self.assertEqual(at.audio_info(flac), (44100, 1, 441))
            self.assertLess(np.max(np.abs(at.read_audio(flac)[1] - data)), 1e-4)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_file_source_maps_channels_per_chunk(self):
        data = np.stack([np.full(30, 0.25, np.float32), np.full(30, -0.5, np.float32)], axis=1)
        src = at.FileSource(data, 8, "repeat", 0.5, loop=True)
        chunk, ended = src.read(50)                                     # wraps around the 30 samples
        self.assertEqual(chunk.shape, (50, 8))
        self.assertFalse(ended)
        self.assertTrue(np.allclose(chunk[:, 6], 0.125) and np.allclose(chunk[:, 7], -0.25))
        self.assertEqual(src.data.shape, (30, 2))                       # stored at the file's channel count

    def test_computed_text(self):
        s = settings(vlan=True)
        text = at.computed_text(at.StreamConfig(s), at.Requirement(s))
        self.assertIn("450 byte", text)
        self.assertIn("14.400 Mbps", text)
        self.assertIn("MATCHES REQUIREMENT: 48 kHz / 8 ch / 32 bit / 12 samples / 384 byte", text)
        s = settings(samples_per_frame=6)
        text = at.computed_text(at.StreamConfig(s), at.Requirement(s))
        self.assertIn("DOES NOT MATCH REQUIREMENT", text)
        self.assertIn("samples/frame 6 (needs 12)", text)

    def test_paced_sending(self):
        for mode in ("sendqueue", "busy"):
            with self.subTest(mode=mode):
                cfg = at.StreamConfig(settings(frames_to_send=400))
                fake = FakePcap()
                talker = at.Talker(fake)
                talker.start("fake", cfg, at.SineSource(1000.0, 0.2, cfg.channels, cfg.rate), mode, 20)
                deadline = time.time() + 10
                while talker.running and time.time() < deadline:
                    time.sleep(0.02)
                self.assertFalse(talker.running)
                self.assertEqual(talker.errors, [])
                self.assertEqual(talker.sent, 400)
                span = (fake.times[-1] - fake.times[0]) / 1e9
                self.assertAlmostEqual(span, 399 / 4000, delta=0.03)    # 4000 packets/s


if __name__ == "__main__":
    unittest.main()
