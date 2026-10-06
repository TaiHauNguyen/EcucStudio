"""PDU collection (SoAd nPdu): several CAN -> ETH PDUs in one UDP datagram."""
import os
import re
import shutil
import tempfile
import unittest

from ecucstudio.gateway import capl, paths, report
from ecucstudio.gateway.base import Base
from ecucstudio.gateway.config import BusInput, GatewayConfig, PduCollection, SocketSide
from ecucstudio.gateway.planner import make_plan
from ecucstudio.gateway.writer import generate

try:
    import cantools  # noqa: F401
    HAVE_CANTOOLS = True
except ImportError:
    HAVE_CANTOOLS = False

NS = "{http://autosar.org/schema/r4.0}"
# Zone receives Fast (10 ms), Slow (100 ms), Event (no cycle) on BusA and sends Cmd on BusB (ETH -> CAN)
BUS_A = ('VERSION ""\n\nNS_ :\n\nBS_:\n\nBU_: Zone Src\n\n'
         'BO_ 256 Fast: 8 Src\n SG_ F : 0|8@1+ (1,0) [0|255] "" Zone\n\n'
         'BO_ 257 Slow: 8 Src\n SG_ S : 0|8@1+ (1,0) [0|255] "" Zone\n\n'
         'BO_ 258 Event: 4 Src\n SG_ E : 0|8@1+ (1,0) [0|255] "" Zone\n\n'
         'BA_DEF_ "DBName" STRING ;\nBA_DEF_ BO_ "GenMsgCycleTime" INT 0 10000;\n'
         'BA_DEF_DEF_ "DBName" "";\nBA_DEF_DEF_ "GenMsgCycleTime" 0;\nBA_ "DBName" "BusA";\n'
         'BA_ "GenMsgCycleTime" BO_ 256 10;\nBA_ "GenMsgCycleTime" BO_ 257 100;\n')
BUS_B = ('VERSION ""\n\nNS_ :\n\nBS_:\n\nBU_: Zone Dst\n\n'
         'BO_ 512 Cmd: 8 Zone\n SG_ C : 0|8@1+ (1,0) [0|255] "" Dst\n\n'
         'BA_DEF_ "DBName" STRING ;\nBA_DEF_ BO_ "GenMsgCycleTime" INT 0 10000;\n'
         'BA_DEF_DEF_ "DBName" "";\nBA_DEF_DEF_ "GenMsgCycleTime" 0;\nBA_ "DBName" "BusB";\n'
         'BA_ "GenMsgCycleTime" BO_ 512 10;\n')


@unittest.skipUnless(HAVE_CANTOOLS, "cantools is not installed")
class CollectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def config(self, **col):
        paths_ = []
        for name, text in (("BusA", BUS_A), ("BusB", BUS_B)):
            p = os.path.join(self.tmp, f"{name}.dbc")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(text)
            paths_.append(p)
        cfg = GatewayConfig(base="", output=os.path.join(self.tmp, "Zone_Gw.arxml"), ecu="Zone")
        cfg.options.dbc_imported = True
        cfg.options.can_routes = False
        cfg.buses = [BusInput(dbc=p, node="Zone") for p in paths_]
        e = cfg.ethernet
        e.vlan_id, e.ecu_ip, e.default_peer = 20, "10.0.20.1", "Central"
        e.can_to_eth = SocketSide(local_port=50000, remote_ip="10.0.20.2", remote_port=50001)
        e.eth_to_can = SocketSide(local_port=50001, remote_ip="10.0.20.2", remote_port=50000)
        e.collection = PduCollection(**col)
        return cfg

    def identifiers(self, path):
        out = Base(path)
        return {i.findtext(NS + "SHORT-NAME"): i for i in out.root.iter(NS + "SO-CON-I-PDU-IDENTIFIER")}

    def test_off_writes_nothing(self):
        plan = make_plan(self.config())
        self.assertFalse(any(r.eth_send for r in plan.routes))
        self.assertEqual(plan.load, [])
        res = generate(plan)
        with open(res.output, encoding="utf-8") as fh:
            self.assertNotIn("PDU-COLLECTION", fh.read())

    def test_collect_all(self):
        plan = make_plan(self.config(enabled=True))
        self.assertEqual(plan.errors, [])
        r = {x.key: x for x in plan.enabled_routes}
        self.assertEqual({k: x.eth_send for k, x in r.items()},
                         {"BusA/Fast": "collect", "BusA/Slow": "collect", "BusA/Event": "collect",
                          "BusB/Cmd": ""})                                    # ETH -> CAN: receiver, nothing
        res = generate(plan)
        out = Base(res.output)
        tx = next(s for s in out.root.iter(NS + "SOCKET-ADDRESS") if s.findtext(NS + "SHORT-NAME") == "SA_Zone_CanGw_Tx")
        self.assertEqual(tx.findtext(NS + "PDU-COLLECTION-MAX-BUFFER-SIZE"), "1400")
        self.assertEqual(tx.findtext(NS + "PDU-COLLECTION-TIMEOUT"), "0.005")
        # schema order: ... CONNECTOR-REF, PDU-COLLECTION-*, STATIC-SOCKET-CONNECTIONS
        tags = [c.tag.replace(NS, "") for c in tx]
        self.assertLess(tags.index("CONNECTOR-REF"), tags.index("PDU-COLLECTION-MAX-BUFFER-SIZE"))
        self.assertLess(tags.index("PDU-COLLECTION-TIMEOUT"), tags.index("STATIC-SOCKET-CONNECTIONS"))
        rx = next(s for s in out.root.iter(NS + "SOCKET-ADDRESS") if s.findtext(NS + "SHORT-NAME") == "SA_Zone_CanGw_Rx")
        self.assertIsNone(rx.find(NS + "PDU-COLLECTION-TIMEOUT"))
        ids = self.identifiers(res.output)
        fast = ids["Fast_oBusA_Eth_ID"]
        self.assertEqual([c.tag.replace(NS, "") for c in fast],
                         ["SHORT-NAME", "HEADER-ID", "PDU-COLLECTION-PDU-TIMEOUT", "PDU-COLLECTION-SEMANTICS",
                          "PDU-COLLECTION-TRIGGER", "PDU-TRIGGERING-REF"])
        self.assertEqual(fast.findtext(NS + "PDU-COLLECTION-SEMANTICS"), "QUEUED")
        self.assertEqual(fast.findtext(NS + "PDU-COLLECTION-TRIGGER"), "NEVER")
        self.assertIsNone(ids["Cmd_oBusB_Eth_ID"].find(NS + "PDU-COLLECTION-TRIGGER"))
        # load: 1:1 = 100 + 10 pkt/s (event not counted); collected <= 1000 / 5 ms windows, 2 PDUs a window max
        load = plan.load[0]
        self.assertEqual((load.peer, load.collected, load.immediate, load.events), ("Central", 3, 0, 1))
        self.assertAlmostEqual(load.pkts_1to1, 110.0)
        self.assertAlmostEqual(load.pkts_collected, 110.0)                 # fewer frames than windows here
        self.assertEqual(load.worst_window, 2 * (8 + 8))
        self.assertTrue(any(i.startswith("PDU collection to Central:") for i in plan.infos))
        self.assertEqual([b.bus for b in plan.burst], ["BusB"])
        # route table and reports
        row = dict(zip(report.COLUMNS, report.route_rows(plan)[0]))
        self.assertEqual(row["ETH send"], "collect <= 5 ms")
        rep = paths.report_of_plan(plan)
        p = next(x for x in rep.paths if x.names == "Fast")
        self.assertEqual(p.eth_send, "collect <= 5 ms")
        self.assertEqual(rep.load[0][:3], ("Zone", "Central", "SA_Zone_CanGw_Tx"))
        html_path, csv_path = paths.write_report(rep, os.path.join(self.tmp, "rep"))
        with open(html_path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("Ethernet load (PDU collection)", text)
        self.assertIn('<table id="paths">', text)
        with open(csv_path, encoding="utf-8-sig") as fh:
            csv_text = fh.read()
        self.assertIn("ETH send", csv_text.splitlines()[0])
        self.assertIn("Ethernet load, CAN -> ETH", csv_text)
        # regeneration: identical
        from ecucstudio.gateway.regen import config_from_file
        with open(res.output, "rb") as fh:
            v1 = fh.read()
        cfg2, _ = config_from_file(res.output)
        self.assertTrue(cfg2.ethernet.collection.enabled)
        generate(make_plan(cfg2))
        with open(res.output, "rb") as fh:
            self.assertEqual(fh.read(), v1)

    def test_mode_cycle_and_override(self):
        cfg = self.config(enabled=True, mode="cycle", immediate_cycle_ms=20, timeout_ms=10)
        cfg.buses[0].messages = {"Slow": {"eth_send": "immediate"}}
        plan = make_plan(cfg)
        r = {x.key: x for x in plan.enabled_routes}
        self.assertEqual((r["BusA/Fast"].eth_send, r["BusA/Fast"].eth_send_why), ("immediate", "cycle 10 ms <= 20 ms"))
        self.assertEqual((r["BusA/Event"].eth_send, r["BusA/Event"].eth_send_why), ("immediate", "event message"))
        self.assertEqual((r["BusA/Slow"].eth_send, r["BusA/Slow"].eth_send_why), ("immediate", "chosen"))
        self.assertTrue(any("every CAN -> ETH PDU is sent immediately" in i for i in plan.infos))
        cfg.buses[0].messages = {"Fast": {"eth_send": "collect"}}
        plan = make_plan(cfg)
        r = {x.key: x for x in plan.enabled_routes}
        self.assertEqual(r["BusA/Fast"].eth_send, "collect")
        ids = self.identifiers(generate(plan).output)
        self.assertEqual(ids["Fast_oBusA_Eth_ID"].findtext(NS + "PDU-COLLECTION-PDU-TIMEOUT"), "0.01")
        self.assertEqual(ids["Event_oBusA_Eth_ID"].findtext(NS + "PDU-COLLECTION-TRIGGER"), "ALWAYS")
        self.assertIsNone(ids["Event_oBusA_Eth_ID"].find(NS + "PDU-COLLECTION-PDU-TIMEOUT"))

    def test_buffer_checks(self):
        plan = make_plan(self.config(enabled=True, buffer=12))
        self.assertTrue(any("smaller than the largest PDU" in e for e in plan.errors), plan.errors)
        plan = make_plan(self.config(enabled=True, buffer=4000))
        self.assertTrue(any("IP fragmented" in w for w in plan.warnings), plan.warnings)
        plan = make_plan(self.config(enabled=True, timeout_ms=0))
        self.assertTrue(any("collection timeout" in e for e in plan.errors), plan.errors)
        # a window that does not fit: Fast 10 ms twice in 20 ms + Slow ... with a 24 byte buffer
        plan = make_plan(self.config(enabled=True, buffer=24, timeout_ms=20))
        self.assertTrue(any("datagrams per window" in w for w in plan.warnings), plan.warnings)

    def test_capl_collected_keys(self):
        plan = make_plan(self.config(enabled=True, timeout_ms=5, buffer=1000))
        files, _ = capl.write_eth_to_can(plan, os.path.join(self.tmp, "Zone_Gw"))
        with open(files[0], encoding="cp1252") as fh:
            text = fh.read()
        for key in ("'b'", "'v'"):
            self.assertIn(f"on key {key}", text)
        self.assertIn("const dword kTickMs = 5;", text)
        self.assertIn("const dword kDgramMax = 1000;", text)
        self.assertEqual(text.count("{"), text.count("}"))
        self.assertFalse(re.search(r"\?.*:", re.sub(r"//.*", "", text)))


if __name__ == "__main__":
    unittest.main()
