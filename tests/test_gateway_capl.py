"""CAPL test node for the ETH -> CAN routes (gateway/capl.py)."""
import os
import re
import shutil
import tempfile
import unittest

from ecucstudio.gateway import capl, dbcread
from ecucstudio.gateway.config import BusInput, GatewayConfig, SocketSide
from ecucstudio.gateway.planner import make_plan

try:
    import cantools
    HAVE_CANTOOLS = True
except ImportError:
    HAVE_CANTOOLS = False

DBC = ('VERSION ""\n\nNS_ :\n\nBS_:\n\nBU_: Zone Rcv\n\n'
       'BO_ 300 Cmd: 8 Zone\n'
       ' SG_ IntelSig : 3|10@1+ (1,0) [0|1023] "" Rcv\n'
       ' SG_ MotoSig : 23|12@0+ (1,0) [0|4095] "" Rcv\n'
       ' SG_ NegSig : 40|8@1- (1,0) [-128|127] "" Rcv\n\n'
       'BO_ 301 Other: 4 Zone\n SG_ O : 0|8@1+ (1,0) [0|255] "" Rcv\n\n'
       'BA_DEF_ "DBName" STRING ;\nBA_DEF_ SG_ "GenSigStartValue" INT -1000 100000;\n'
       'BA_DEF_ BO_ "GenMsgCycleTime" INT 0 10000;\n'
       'BA_DEF_DEF_ "DBName" "";\nBA_DEF_DEF_ "GenSigStartValue" 0;\nBA_DEF_DEF_ "GenMsgCycleTime" 0;\n'
       'BA_ "DBName" "{bus}";\nBA_ "GenMsgCycleTime" BO_ 300 20;\n'
       'BA_ "GenSigStartValue" SG_ 300 IntelSig 517;\nBA_ "GenSigStartValue" SG_ 300 MotoSig 2748;\n'
       'BA_ "GenSigStartValue" SG_ 300 NegSig -3;\n')


@unittest.skipUnless(HAVE_CANTOOLS, "cantools is not installed")
class CaplTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def dbc(self, bus):
        path = os.path.join(self.tmp, f"{bus}.dbc")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(DBC.replace("{bus}", bus))
        return path

    def test_pack_like_cantools(self):
        path = self.dbc("BusA")
        m = next(x for x in dbcread.load(path).messages if x.name == "Cmd")
        ref = cantools.database.load_file(path).get_message_by_name("Cmd")
        want = ref.encode({"IntelSig": 517, "MotoSig": 2748, "NegSig": -3})
        self.assertEqual(capl.pack(m.signals, 8), want)

    def test_eth_to_can_node(self):
        cfg = GatewayConfig(base="", output=os.path.join(self.tmp, "Zone_Gw.arxml"), ecu="Zone")
        cfg.options.dbc_imported = True
        cfg.options.can_routes = False
        cfg.buses = [BusInput(dbc=self.dbc("BusA"), node="Zone"), BusInput(dbc=self.dbc("BusB"), node="Zone")]
        e = cfg.ethernet
        e.vlan_id, e.ecu_ip, e.default_peer = 20, "10.0.20.1", "Central"
        e.can_to_eth = SocketSide(local_port=50000, remote_ip="10.0.20.2", remote_port=50001)
        e.eth_to_can = SocketSide(local_port=50001, remote_ip="10.0.20.2", remote_port=50000)
        plan = make_plan(cfg)
        self.assertEqual(plan.errors, [])
        files, problems = capl.write_eth_to_can(plan, os.path.join(self.tmp, "Zone_Gw"))
        self.assertEqual(problems, [])
        self.assertEqual([os.path.basename(f) for f in files], ["Zone_Gw_eth_to_can_Central.can"])
        with open(files[0], encoding="cp1252") as fh:
            text = fh.read()
        # addresses: this node = the Ethernet source of the ETH -> CAN routes, sent to the ECU's local port
        self.assertIn('char  kPeerIp[16] = "10.0.20.2"', text)
        self.assertIn("dword kPeerPort   = 50000", text)
        self.assertIn('char  kEcuIp[16]  = "10.0.20.1"', text)
        self.assertIn("dword kEcuPort    = 50001", text)
        # 1:N: Cmd and Other go to both buses from one Ethernet PDU each -> 2 PDUs, header id = CAN id
        self.assertIn("const int kCount  = 2;", text)
        self.assertIn("dword gHeaderId[2] = { 0x0000012C, 0x0000012D };", text)
        self.assertIn("BusA/Cmd (0x12C), BusB/Cmd (0x12C)", text)
        self.assertIn("dword gCycleMs[2] = { 20, 100 };", text)
        r = next(x for x in plan.enabled_routes if x.message.name == "Cmd" and x.fanout_of is None)
        row = "{" + ", ".join(f"0x{b:02X}" for b in capl.pack(r.message.signals, 8)) + "}"
        self.assertIn(row, text)
        for key in ("'a'", "'n'", "'c'", "'p'", "'l'"):
            self.assertIn(f"on key {key}", text)
        self.assertEqual(text.count("{"), text.count("}"))
        self.assertFalse(re.search(r"\?.*:", re.sub(r"//.*", "", text)), "no ?: in CAPL code")


if __name__ == "__main__":
    unittest.main()
