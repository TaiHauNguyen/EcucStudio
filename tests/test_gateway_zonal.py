"""Zonal network: ECU -> ECU routes from the routing table (topology/planner.py, TopologyPlanner._pair_table)."""
import csv
import os
import shutil
import tempfile
import unittest

from ecucstudio.gateway.config import BusInput, GatewayConfig
from ecucstudio.gateway.topology import TopologyConfig, generate_topology, make_topology_plan
from ecucstudio.gateway.topology.config import EcuNode, PeerNode

try:
    import cantools  # noqa: F401
    HAVE_CANTOOLS = True
except ImportError:
    HAVE_CANTOOLS = False

HEADER = ["", "Signal name", "Src Message/PDU name", "Src Protocol (CAN/Ethernet/LIN)",
          "Receive CAN Container PduID(Hex)/ ETH Pdu ID (Hex)", "Routing Type (Signal = 1, Message = 0)",
          "HW-Accelerator (LLCE/PFE) (Yes = 1, No = 0)", "BusA", "BusB", "BusC", "Lin1",
          "Dest Signal name", "Dest Message/PDU name", "Dest Protocol (CAN/Ethernet/LIN)",
          "Transmit CAN Container PduID(Hex)/ ETH Pdu ID (Hex)"]
ROWS = [  # index, signal, message, protocol, id, type, hw, BusA, BusB, BusC, Lin1, dst signal, dst message ...
    ["0", "", "EngineData", "CAN_FD", "0x100", "0", "1", "S", "D", "", "", "", "EngineData", "CAN_FD", "0x100"],
    ["1", "", "Wheel", "CAN_FD", "0x101", "0", "1", "S", "", "D", "", "", "Wheel", "CAN_FD", "0x101"],
    ["2", "EngSpeed", "EngineData", "CAN_FD", "0x100", "1", "0", "S", "", "D", "", "Speed", "BodyInfo", "CAN_FD",
     "0x300"],
    ["3", "", "Door", "CAN_FD", "0x200", "0", "0", "", "", "S", "", "", "Door", "CAN_FD", "0x200"],
    ["4", "", "Door", "CAN_FD", "0x200", "0", "0", "D", "", "S", "", "", "Door", "CAN_FD", "0x200"],
    ["5", "LinSig", "LinMsg", "LIN", "0x10", "1", "0", "", "", "D", "S", "Spare", "BodyInfo", "CAN_FD", "0x300"],
]


def _dbc(folder, name, nodes, msgs):
    text = 'VERSION ""\n\nNS_ :\n\nBS_:\n\nBU_: ' + " ".join(nodes) + "\n\n"
    for mid, mname, length, sender, sigs in msgs:
        text += f"BO_ {mid} {mname}: {length} {sender}\n"
        for sname, start, slen, rcv in sigs:
            text += f' SG_ {sname} : {start}|{slen}@1+ (1,0) [0|0] "" {rcv}\n'
        text += "\n"
    text += f'BA_DEF_ "DBName" STRING ;\nBA_DEF_DEF_ "DBName" "";\nBA_ "DBName" "{name}";\n'
    path = os.path.join(folder, name + ".dbc")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _sigs(rcv, *names):
    return [(n, 8 * i, 8, rcv) for i, n in enumerate(names)]


@unittest.skipUnless(HAVE_CANTOOLS, "cantools is not installed")
class ZonalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = t = tempfile.mkdtemp()
        a = _dbc(t, "BusA", ["ZoneA", "Engine"], [
            (256, "EngineData", 8, "Engine", _sigs("ZoneA", "EngSpeed", "EngTemp")),
            (257, "Wheel", 8, "Engine", _sigs("ZoneA", "WheelSpeed")),
            (258, "Extra", 8, "Engine", _sigs("ZoneA", "ExtraSig")),
            (512, "Door", 8, "ZoneA", _sigs("Engine", "DoorState"))])
        b = _dbc(t, "BusB", ["ZoneA", "Dash"], [(256, "EngineData", 8, "ZoneA", _sigs("Dash", "EngSpeed", "EngTemp"))])
        c = _dbc(t, "BusC", ["ZoneB", "Body"], [
            (257, "Wheel", 8, "ZoneB", _sigs("Body", "WheelSpeed")),
            (258, "Extra", 8, "ZoneB", _sigs("Body", "ExtraSig")),
            (768, "BodyInfo", 8, "ZoneB", _sigs("Body", "Speed", "Spare")),
            (512, "Door", 8, "Body", _sigs("ZoneB", "DoorState"))])
        table = os.path.join(t, "routing.csv")
        with open(table, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(HEADER)
            w.writerows(ROWS)
        self.cfg = cfg = TopologyConfig(name="net", one_socket=True, routing_table=table, table_hw=True,
                                        default_peer="Central")
        cfg.cross.also_to_default_peer = True
        cfg.peers.append(PeerNode(name="Central", ip="10.0.9.1", tx_port=41100))
        for name, ip, port, dbcs in (("ZoneA", "10.0.9.2", 41200, (a, b)), ("ZoneB", "10.0.9.3", 41300, (c,))):
            g = GatewayConfig(output=os.path.join(t, f"{name}_Gateway.arxml"), ecu=name,
                              buses=[BusInput(dbc=p, node=name) for p in dbcs])
            g.options.dbc_imported = True
            cfg.ecus.append(EcuNode(name=name, ip=ip, tx_port=port, gateway=g))
        cfg.save(os.path.join(t, "network.json"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def routes(self, tplan, ecu):
        return {(r.key, r.direction): r for r in tplan.plans[ecu].routes}

    def test_routes_between_ecus_come_from_the_table(self):
        tplan = make_topology_plan(self.cfg)
        self.assertEqual(tplan.errors, [])
        # only the table pairs ECUs: Extra has the same name on both ECUs but is not in the table
        self.assertEqual([(c.key, c.enabled) for c in tplan.cross],
                         [("ZoneA/BusA/Wheel -> ZoneB/BusC/Wheel", True), ("ZoneB/BusC/Door -> ZoneA/BusA/Door", True)])
        a, b = self.routes(tplan, "ZoneA"), self.routes(tplan, "ZoneB")
        # CAN -> ETH: always to the central node, and to the ECU the table names
        self.assertEqual(a["BusA/Wheel", "CAN->ETH"].peers, ["ZoneB", "Central"])
        self.assertEqual(a["BusA/Extra", "CAN->ETH"].peers, ["Central"])
        self.assertEqual(a["BusA/EngineData", "CAN->ETH"].peers, ["ZoneB", "Central"])   # signal row: message only
        # ETH -> CAN: from the ECU the table names, else from the central node
        self.assertEqual(b["BusC/Wheel", "ETH->CAN"].peers, ["ZoneA"])
        self.assertEqual(b["BusC/Wheel", "ETH->CAN"].header_id, a["BusA/Wheel", "CAN->ETH"].header_id)
        self.assertEqual(b["BusC/Extra", "ETH->CAN"].peers, ["Central"])
        self.assertEqual(a["BusA/Door", "ETH->CAN"].peers, ["ZoneB"])
        # inside ZoneA: CAN -> CAN (HW row routed, table_hw)
        self.assertEqual([cr.key for cr in tplan.plans["ZoneA"].enabled_can_routes],
                         ["BusA/EngineData->BusB/EngineData"])
        # the rows between ECUs are known in both ECUs' table status
        st = {e: {s.row.index: s for s in p.table_rows} for e, p in tplan.plans.items()}
        self.assertEqual((st["ZoneA"]["1"].status, st["ZoneB"]["1"].status), ("routed", "routed"))
        self.assertEqual((st["ZoneA"]["2"].status, st["ZoneB"]["2"].status), ("not supported", "not supported"))
        self.assertIn("ZoneA sends EngineData to ZoneB", st["ZoneB"]["2"].detail)
        self.assertEqual(st["ZoneB"]["3"].status, "problem")            # no destination network on an ECU: D empty
        self.assertEqual(st["ZoneB"]["5"].status, "LIN")
        self.assertTrue(any("signal gateway there" in w for w in tplan.warnings), tplan.warnings)
        # HW rows left to the accelerator: no ECU -> ECU route
        self.cfg.table_hw = False
        tplan = make_topology_plan(self.cfg)
        self.assertEqual([c.key for c in tplan.cross], ["ZoneB/BusC/Door -> ZoneA/BusA/Door"])
        st = {s.row.index: s for s in tplan.plans["ZoneB"].table_rows}
        self.assertEqual(st["1"].status, "HW accelerator")

    def test_generate_target_and_report(self):
        from ecucstudio.gateway import paths
        for e in self.cfg.ecus:
            e.generate = e.name == "ZoneB"
        tplan = make_topology_plan(self.cfg)
        res = dict(generate_topology(tplan))
        self.assertEqual(list(res), ["ZoneB"])
        self.assertTrue(os.path.isfile(res["ZoneB"].output))
        rep = paths.report_of_topology(tplan)
        rows = {(r[0], r[5]): r for r in rep.table}
        self.assertEqual(rows["row 3 #1", "ZoneA"][6], "routed")
        self.assertEqual(rows["row 3 #1", "ZoneB"][6], "routed")
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "network_message_paths.html")))
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "network.lock.json")))

    def test_canoe_test_module(self):
        """capl_test.py: one test case per route of the target ECU, CANoe plays the other nodes."""
        import re
        from ecucstudio.gateway import capl_test
        tplan = make_topology_plan(self.cfg)
        path = os.path.join(self.tmp, "ZoneA_canoe_test.can")
        tm = capl_test.write_test_module(tplan.plans["ZoneA"], path)
        self.assertEqual(tm.problems, [])
        self.assertEqual(tm.counts, {"CAN -> ETH": 3, "ETH -> CAN": 1, "CAN -> CAN": 1, "signal": 0})
        self.assertEqual(tm.buses, ["BusA", "BusB"])
        self.assertEqual([(s.ip, s.port, s.label) for s in tm.socks],
                         [("10.0.9.3", 41300, "ZoneB"), ("10.0.9.1", 41100, "Central")])
        with open(path, "rb") as fh:
            raw = fh.read()
        self.assertIn(b"\r\n", raw)
        text = raw.decode("cp1252")
        self.assertEqual(text.count("{"), text.count("}"))
        self.assertNotIn("{{", text.replace("{{ ", ""))
        names = re.findall(r"^testcase (\w+)\(\)", text, re.M)
        self.assertEqual(len(names), 5)
        self.assertEqual(sorted(names), sorted(re.findall(r"^  (TC\w+)\(\);", text, re.M)))   # all in MainTest
        # Wheel goes to ZoneB and Central: both receive sockets must see it (mask 0x3)
        self.assertIn("CheckCanToEth(2, 0, 0x101, 0, 0, 8, 0x00000101, 0x3,", text)
        # Door comes from ZoneB: sent from ZoneB's socket to ZoneA's port
        self.assertIn("CheckEthToCan(4, 0, 41200, 0x00000200, 8,", text)
        # signal routes: a target ECU with a Com signal gateway inside
        from ecucstudio.gateway.planner import make_plan
        from tests.test_gateway_routing_table import RoutingTableTest
        rt = RoutingTableTest()
        rt.setUp()
        try:
            tm = capl_test.build(make_plan(rt.config()))
            self.assertEqual(tm.counts["signal"], 2)
            text = capl_test.capl_text(tm)
            self.assertIn("CheckSignal(0, 0x100, 0, 0, 8, 0, 16, 1,", text)
        finally:
            rt.tearDown()

    def test_same_can_id_from_the_hpc_is_one_message(self):
        """The HPC sends a message the zone ECU sends on three buses (same CAN id, classic and FD frames): one Ethernet
        PDU with the CAN id as header id, forwarded to every bus (1:N), although no routing table row links them. A CAN
        id the zone receives on two buses goes to the HPC twice on one link: an error (no flag is added)."""
        from ecucstudio.gateway.base import Base
        t = self.tmp

        def bus(name, fd, twice=True):
            msgs = [(420, "SeatCmd", 8, "ZoneC", _sigs("Seat", "SeatSig"))]
            if twice:
                msgs.append((400, "Twice", 8, "Seat", _sigs("ZoneC", "TwiceSig")))
            path = _dbc(t, name, ["ZoneC", "Seat"], msgs)
            if fd:                                                      # SeatCmd is a CAN FD frame there
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
                text = text.replace('BA_DEF_ "DBName" STRING ;\n', (
                    'BA_DEF_ BO_ "VFrameFormat" ENUM "StandardCAN","ExtendedCAN","reserved","reserved","reserved",'
                    '"reserved","reserved","reserved","reserved","reserved","reserved","reserved","reserved",'
                    '"reserved","StandardCAN_FD","ExtendedCAN_FD";\nBA_DEF_ "DBName" STRING ;\n'))
                text = text.replace('BA_DEF_DEF_ "DBName" "";\n',
                                    'BA_DEF_DEF_ "VFrameFormat" "StandardCAN";\nBA_DEF_DEF_ "DBName" "";\n')
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(text + 'BA_ "VFrameFormat" BO_ 420 14;\n')
            return path
        cfg = TopologyConfig(name="seat", one_socket=True, routing_table=self.cfg.routing_table, table_hw=True,
                             default_peer="Central")
        cfg.cross.also_to_default_peer = True
        cfg.peers.append(PeerNode(name="Central", ip="10.0.9.1", tx_port=41100))
        g = GatewayConfig(output=os.path.join(t, "ZoneC_Gateway.arxml"), ecu="ZoneC",
                          buses=[BusInput(dbc=bus("BusX", False), node="ZoneC"),
                                 BusInput(dbc=bus("BusY", True), node="ZoneC"),
                                 BusInput(dbc=bus("BusZ", True, twice=False), node="ZoneC")])
        g.options.dbc_imported = True
        cfg.ecus.append(EcuNode(name="ZoneC", ip="10.0.9.4", tx_port=41400, gateway=g))
        tp = make_topology_plan(cfg)
        self.assertEqual(tp.errors, [
            "ZoneC/BusY/Twice: header id 0x00000190 (CAN id 0x190) is already used by ZoneC/BusX/Twice from ZoneC to "
            "Central: two PDUs with one header id on one link. Disable one of them or enter another header id."])
        r = {x.key: x for x in tp.plans["ZoneC"].routes if x.direction == "ETH->CAN"}
        x, y, z = r["BusX/SeatCmd"], r["BusY/SeatCmd"], r["BusZ/SeatCmd"]
        self.assertTrue(y.message.fd and not x.message.fd)
        self.assertIsNone(x.fanout_of)
        self.assertIs(y.fanout_of, x)
        self.assertIs(z.fanout_of, x)
        self.assertEqual([v.header_id for v in (x, y, z)], [0x1A4] * 3)
        self.assertEqual(y.header_note, "same as BusX/SeatCmd (1:N)")
        self.assertIn("1:N: forwarded to BusX, BusY, BusZ", x.notes)
        self.assertFalse(any("flag" in w for w in tp.warnings), tp.warnings)
        # with a header id of its own for the second Twice the file is generated: one Ethernet PDU for SeatCmd
        g.buses[1].messages = {"Twice": {"header_id": "0x1190"}}
        tp = make_topology_plan(cfg)
        self.assertEqual(tp.errors, [])
        res = dict(generate_topology(tp))
        out = Base(res["ZoneC"].output)
        ids = {p.rsplit("/", 1)[-1]: h.header_id for p, h in out.header_ids().items()}
        self.assertEqual(ids["SeatCmd_oBusX_Eth_ID"], 0x1A4)
        self.assertNotIn("SeatCmd_oBusY_Eth_ID", ids)
        maps = [out.ref(m, "SOURCE-I-PDU-REF") for m in out.root.iter("{http://autosar.org/schema/r4.0}I-PDU-MAPPING")
                if "SeatCmd" in out.refs(m, "TARGET-I-PDU-REF")[0]]
        self.assertEqual(len(maps), 3)
        self.assertEqual(len(set(maps)), 1)                             # one Ethernet PDU, three CAN buses

    def test_main_window_model(self):
        """gateway/zonal.py: what the main window does with the network, the DBC files and the routing table."""
        from ecucstudio.gateway import dbcread, nodes, routing_table, zonal
        table = [nodes.EthNode("ZoneA", "02:00:00:00:00:02", "10.0.9.2", 41200),
                 nodes.EthNode("Central", "02:00:00:00:00:01", "10.0.9.1", 41100, "hpc"),
                 nodes.EthNode("ZoneB", "", "10.0.9.3", 41300), nodes.EthNode("ZoneC", "", "10.0.9.4", 41400)]
        t = zonal.new_network(table)
        self.assertEqual((t.default_peer, [e.name for e in t.ecus]), ("Central", ["ZoneA", "ZoneB", "ZoneC"]))
        self.assertTrue(t.one_socket and t.table_hw and t.cross.also_to_default_peer)
        self.assertEqual([(r.name, r.role) for r in zonal.node_rows(t)][:2], [("Central", zonal.HPC),
                                                                              ("ZoneA", zonal.ZONAL)])
        self.assertTrue(zonal.missing(t).startswith("2. CAN buses"))
        # DBC files: node and ECU found from the node names
        dbs = {}
        for name in ("BusA", "BusB", "BusC"):
            p = os.path.join(self.tmp, name + ".dbc")
            db = dbs[os.path.abspath(p)] = dbcread.load(p)
            node = zonal.guess_node(t, db)
            zonal.add_dbc(t, p, db, node, zonal.guess_ecu(t, db, node))
        self.assertEqual([(e, b.node) for e, b in zonal.dbc_rows(t)],
                         [("ZoneA", "ZoneA"), ("ZoneA", "ZoneA"), ("ZoneB", "ZoneB")])
        self.assertEqual(zonal.guess_ecu(t, dbs[os.path.abspath(os.path.join(self.tmp, "BusA.dbc"))], "Gw_ZoneB"),
                         "ZoneB")
        self.assertTrue(zonal.missing(t).startswith("3. Routing table"))
        t.routing_table = self.cfg.routing_table
        self.assertTrue(zonal.missing(t).startswith("4. Generate"))
        t.target = "ZoneB"
        self.assertEqual(zonal.missing(t), "")
        # routing table overview before planning
        ov = zonal.overview(t, routing_table.read(t.routing_table), dbs)
        self.assertEqual((ov.rows, ov.messages, ov.signals, ov.hw, ov.problems), (6, 4, 2, 2, 1))
        self.assertEqual(ov.counts, {"CAN -> CAN inside an ECU": 1, "ECU -> ECU over Ethernet": 2,
                                     "ECU -> ECU signal (message only)": 1, "problem": 1, "LIN": 1})
        self.assertEqual([(u.network, u.ecu, u.kind) for u in ov.networks][-1], ("Lin1", "", "LIN"))
        # planning copy: ZoneC has no DBC file (Ethernet node only), only the target is generated
        t.path = os.path.join(self.tmp, "net2.json")
        p = zonal.planning_config(t, dbs)
        self.assertEqual(([e.name for e in p.ecus], [x.name for x in p.peers]), (["ZoneA", "ZoneB"],
                                                                                   ["Central", "ZoneC"]))
        self.assertEqual([(e.name, e.generate) for e in p.ecus], [("ZoneA", False), ("ZoneB", True)])
        self.assertEqual(p.ecu("ZoneB").gateway.output, os.path.join(self.tmp, "ZoneB_Gateway.arxml"))
        tplan = make_topology_plan(p, {}, {})
        self.assertEqual(tplan.errors, [])
        self.assertEqual(len(tplan.enabled_cross), 2)
        # nodes: rename keeps the DBC files, a new HPC turns the old one into a zonal ECU, removing frees the DBCs
        self.assertEqual(zonal.set_node(t, "ZoneB", zonal.NodeRow("ZoneR", zonal.ZONAL, "", "10.0.9.3", 41300)), "")
        self.assertEqual((t.target, len(t.ecu("ZoneR").gateway.buses)), ("ZoneR", 1))
        self.assertIn("letter", zonal.set_node(t, "", zonal.NodeRow("1x", zonal.ZONAL, "", "", None)))
        zonal.make_hpc(t, "ZoneC")
        self.assertEqual((t.default_peer, t.ecu("Central") is not None), ("ZoneC", True))
        zonal.remove_node(t, "ZoneA")
        self.assertEqual(len(t.unassigned), 2)
        self.assertTrue(zonal.missing(t).startswith("2. CAN buses: choose the ECU"))
        zonal.place_dbc(t, t.unassigned[0], "ZoneR")
        self.assertEqual((len(t.unassigned), len(t.ecu("ZoneR").gateway.buses)), (1, 2))
        # the network file keeps the target and the DBC files without ECU (paths relative to it)
        t.save(t.path)
        t2 = type(t).load(t.path)
        self.assertEqual((t2.target, [os.path.basename(b.dbc) for b in t2.unassigned]), ("ZoneR", ["BusB.dbc"]))
        self.assertTrue(os.path.isabs(t2.unassigned[0].dbc))
        self.assertEqual(zonal.default_nodes(t)[0].role, "hpc")


if __name__ == "__main__":
    unittest.main()
