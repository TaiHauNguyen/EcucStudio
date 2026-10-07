"""CAN -> CAN routes from a routing table (gateway/routing_table.py, Planner._plan_table): message rows become PduR
routes, signal rows a Com signal gateway in the .vsde file; other rows are only reported."""
import csv
import os
import re
import shutil
import tempfile
import unittest

from ecucstudio.gateway import paths, report, routing_table, vsde
from ecucstudio.gateway.config import BusInput, GatewayConfig, SocketSide
from ecucstudio.gateway.planner import make_plan
from ecucstudio.gateway.writer import generate

try:
    import cantools  # noqa: F401
    HAVE_CANTOOLS = True
except ImportError:
    HAVE_CANTOOLS = False
try:
    import openpyxl  # noqa: F401
    HAVE_OPENPYXL = True
except ImportError:
    HAVE_OPENPYXL = False

HEADER = ["", "Signal name", "Src Message/PDU name", "Src Protocol (CAN/Ethernet/LIN)",
          "Receive CAN Container PduID(Hex)/ ETH Pdu ID (Hex)", "Name", "Length",
          "Routing Type (Signal = 1, Message = 0)", "HW-Accelerator (LLCE/PFE) (Yes = 1, No = 0)",
          "Lin1", "BusA", "BusB", "BusC", "Zone_ETH",
          "Dest Signal name", "Dest Message/PDU name", "Dest Protocol (CAN/Ethernet/LIN)",
          "Transmit CAN Container PduID(Hex)/ ETH Pdu ID (Hex)", "Dest Default Value"]
ROWS = [  # index, signal, message, protocol, id, name, length, type, hw, Lin1, BusA, BusB, BusC, ETH, dst ...
    ["0", "", "EngineData", "CAN_FD", "0x100", "", "", "0", "0", "", "S", "D", "D", "", "", "EngineData", "CAN_FD",
     "0x100", ""],
    ["1", "EngSpeed", "EngineData", "CAN_FD", "0x100", "", "", "1", "0", "", "S", "D", "", "", "Speed", "DashInfo",
     "CAN_FD", "0x300", "0"],
    ["2", "EngTemp", "EngineData", "CAN_FD", "0x100", "", "", "1", "0", "", "S", "D", "", "", "Temp", "DashInfo",
     "CAN_FD", "0x300", "0"],
    ["3", "", "EngineStatus", "CAN_FD", "0x101", "", "", "0", "1", "", "S", "D", "", "", "", "EngineStatus", "CAN_FD",
     "0x101", ""],
    ["4", "LinSig", "LinMsg", "LIN", "0x10", "", "", "1", "0", "S", "", "D", "", "", "LinSig", "DashInfo", "CAN_FD",
     "0x300", ""],
    ["5", "", "EngineData", "CAN_FD", "0x100", "", "", "0", "0", "", "S", "", "", "D", "", "EngineData", "ETH",
     "0x100", ""],
    ["6", "OnlySig", "Only", "CAN_FD", "0x102", "", "", "1", "0", "", "S", "D", "", "", "Spare", "DashInfo", "CAN_FD",
     "0x300", ""],
    ["7", "", "EngineData", "CAN_FD", "0x100", "", "", "0", "0", "", "", "D", "", "", "", "EngineData", "CAN_FD",
     "0x100", ""],
]


def _dbc(folder, name, nodes, msgs, file_name=None):
    text = 'VERSION ""\n\nNS_ :\n\nBS_:\n\nBU_: ' + " ".join(nodes) + "\n\n"
    for mid, mname, length, sender, sigs in msgs:
        text += f"BO_ {mid} {mname}: {length} {sender}\n"
        for sname, start, slen, rcv in sigs:
            text += f' SG_ {sname} : {start}|{slen}@1+ (1,0) [0|0] "" {rcv}\n'
        text += "\n"
    text += f'BA_DEF_ "DBName" STRING ;\nBA_DEF_DEF_ "DBName" "";\nBA_ "DBName" "{name}";\n'
    path = os.path.join(folder, (file_name or name) + ".dbc")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _engine(rcv):
    return [("EngSpeed", 0, 16, rcv), ("EngTemp", 16, 8, rcv), ("EngMode", 24, 4, rcv)]


@unittest.skipUnless(HAVE_CANTOOLS, "cantools is not installed")
class RoutingTableTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        t = self.tmp
        self.a = _dbc(t, "BusA", ["GwEcu", "Engine", "Other"], [
            (256, "EngineData", 8, "Engine", _engine("GwEcu")),
            (257, "EngineStatus", 8, "Engine", [("StatusA", 0, 8, "GwEcu"), ("StatusB", 8, 8, "GwEcu")]),
            (258, "Only", 8, "Engine", [("OnlySig", 0, 8, "Other")])])
        self.b = _dbc(t, "BusB", ["GwEcu", "Dash"], [
            (256, "EngineData", 8, "GwEcu", _engine("Dash")),
            (257, "EngineStatus", 8, "GwEcu", [("StatusA", 0, 8, "Dash"), ("StatusB", 8, 8, "Dash")]),
            (768, "DashInfo", 8, "GwEcu", [("Speed", 0, 16, "Dash"), ("Temp", 16, 8, "Dash"),
                                           ("Spare", 24, 8, "Dash")])])
        self.c = _dbc(t, "BusC", ["GwEcu", "Body"], [(256, "EngineData", 8, "GwEcu", _engine("Body"))])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def table(self, rows=ROWS, name="routing.csv", header=HEADER):
        path = os.path.join(self.tmp, name)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(header)
            w.writerows(rows)
        return path

    def config(self, table=None, imported=True):
        cfg = GatewayConfig(base="", output=os.path.join(self.tmp, "Gw.arxml"), ecu="GwEcu")
        cfg.options.dbc_imported = imported
        cfg.buses = [BusInput(dbc=self.a, node="GwEcu"), BusInput(dbc=self.b, node="GwEcu"),
                     BusInput(dbc=self.c, node="GwEcu")]
        e = cfg.ethernet
        e.vlan_id, e.ecu_ip, e.one_socket = 20, "10.0.20.1", True
        e.can_to_eth = SocketSide(local_port=50000, remote_ip="10.0.20.2", remote_port=50001)
        cfg.routing_table = table if table is not None else self.table()
        return cfg

    # ------------------------------------------------------------------ reading
    def test_read_csv(self):
        t = routing_table.read(self.table())
        self.assertEqual(t.networks, ["Lin1", "BusA", "BusB", "BusC", "Zone_ETH"])     # Name / Length are not
        self.assertEqual(len(t.rows), 8)
        r = t.rows[0]
        self.assertEqual((r.label, r.routing, r.hw, r.source, r.dests, r.src_id, r.dst_id),
                         ("row 2 #0", "message", False, "BusA", ["BusB", "BusC"], 0x100, 0x100))
        s = t.rows[1]
        self.assertEqual((s.routing, s.what, s.target_msg, s.target_signal), ("signal", "EngineData.EngSpeed",
                                                                              "DashInfo", "Speed"))
        self.assertTrue(t.rows[3].hw)
        self.assertEqual(t.rows[7].problems, ["no source network (S)"])
        self.assertEqual(routing_table.parse_hex("11c"), 0x11C)
        # problems of a row: unknown routing type, value other than S / D, id that is not hex
        bad = [["9", "", "EngineData", "CAN_FD", "0xZZ", "", "", "2", "0", "", "S", "X", "", "", "", "EngineData",
                "CAN_FD", "0x100", ""]]
        p = routing_table.read(self.table(bad, "bad.csv")).rows[0].problems
        self.assertTrue(any("Routing Type" in x for x in p) and any("'X' in column BusB" in x for x in p) and
                        any("not hex" in x for x in p), p)
        with self.assertRaises(ValueError):
            routing_table.read(self.table([], "nohead.csv", header=["a", "b"]))

    @unittest.skipUnless(HAVE_OPENPYXL, "openpyxl is not installed")
    def test_read_xlsx(self):
        """Excel: numbers come as numbers (an id typed as 100 in the hex column is 0x100), header below a title."""
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Gateway routing table"])
        ws.append(HEADER)
        for row in ROWS:
            ws.append([int(x) if x.isdigit() else (x or None) for x in row])
        ws.cell(row=3, column=5, value=100)
        path = os.path.join(self.tmp, "routing.xlsx")
        wb.save(path)
        t = routing_table.read(path)
        self.assertEqual(len(t.rows), 8)
        self.assertEqual((t.rows[0].label, t.rows[0].src_id, t.rows[1].routing), ("row 3 #0", 0x100, "signal"))
        self.assertEqual([r.source for r in t.rows][:3], ["BusA"] * 3)

    # ------------------------------------------------------------------ planning
    def test_message_and_signal_routes(self):
        cfg = self.config()
        plan = make_plan(cfg)
        self.assertEqual(plan.errors, [])
        # message row 0: BusA -> BusB and BusC through PduR (.vsde); Com keeps the signal-routed source signals
        self.assertEqual([(cr.key, cr.enabled, cr.vsde, cr.row) for cr in plan.can_routes],
                         [("BusA/EngineData->BusB/EngineData", True, True, "row 2 #0"),
                          ("BusA/EngineData->BusC/EngineData", True, True, "row 2 #0")])
        self.assertEqual(plan.can_routes[0].keep_signals, ["EngSpeed", "EngTemp"])
        # signal rows 1 and 2: Com signal gateway EngineData -> DashInfo
        self.assertEqual([(sr.key, sr.enabled) for sr in plan.signal_routes],
                         [("BusA/EngineData.EngSpeed->BusB/DashInfo.Speed", True),
                          ("BusA/EngineData.EngTemp->BusB/DashInfo.Temp", True)])
        status = [st.status for st in plan.table_rows]
        self.assertEqual(status, ["routed", "routed", "routed", "HW accelerator", "LIN", "Ethernet", "problem",
                                  "problem"])
        self.assertIn("GwEcu does not receive Only", plan.table_rows[6].detail)
        # target messages: not also fed from Ethernet (CAN -> CAN, Com signal gateway, HW accelerator)
        eth = {(r.bus.name, r.message.name): r for r in plan.routes if r.direction == "ETH->CAN"}
        self.assertEqual(eth["BusB", "EngineData"].reason, "fed from BusA (CAN->CAN)")
        self.assertEqual(eth["BusB", "DashInfo"].reason, "Com sends it (signals routed from BusA)")
        self.assertTrue(eth["BusB", "EngineStatus"].reason.startswith("routed by the HW accelerator from BusA"))
        self.assertFalse(any(r.enabled for r in eth.values()))
        self.assertTrue(any("ComSignalGateway" in i for i in plan.infos), plan.infos)
        self.assertTrue(any(i.startswith("Routing table networks: BusA = BusA, BusB = BusB, BusC = BusC")
                            for i in plan.infos), plan.infos)
        # .vsde: PduR routings with SOURCE-SIGNALS, one COM-SIGNAL-ROUTING
        res = generate(plan)
        with open(res.extension, encoding="utf-8") as fh:
            text = fh.read()
        self.assertEqual(len(res.signal_routes), 2)
        self.assertEqual(text.count("<SOURCE-SIGNALS>"), 2)
        self.assertEqual(text.count("<COM-SIGNAL-ROUTING>"), 1)
        maps = re.findall(r"<SOURCE-I-PDU-REF>(\w+)</SOURCE-I-PDU-REF>\s*<SOURCE-SIGNAL-REF>(\w+)</SOURCE-SIGNAL-REF>"
                          r"\s*<TARGET-I-PDU-REF>(\w+)</TARGET-I-PDU-REF>\s*<TARGET-SIGNAL-REF>(\w+)<", text)
        self.assertEqual(maps, [("EngineData", "EngSpeed", "DashInfo", "Speed"),
                                ("EngineData", "EngTemp", "DashInfo", "Temp")])
        self.assertEqual(sorted(vsde.read(res.extension))[:2],
                         [("GwEcu", "BusA", "EngineData", "BusB", "EngineData"),
                          ("GwEcu", "BusA", "EngineData", "BusC", "EngineData")])
        # report: route table rows and the routing table section
        rows = report.route_rows(plan)
        self.assertTrue(any(r[2] == "SIGNAL" and r[4] == "EngineData.EngSpeed -> DashInfo.Speed" for r in rows))
        rep = paths.report_of_plan(plan)
        self.assertEqual([r[6] for r in rep.table], status)
        html_path, csv_path = paths.write_report(rep, os.path.join(self.tmp, "Gw"))
        with open(html_path, encoding="utf-8") as fh:
            self.assertIn('id="rtable"', fh.read())
        with open(csv_path, encoding="utf-8-sig") as fh:
            self.assertIn("Routing table", fh.read())
        # generating again from the gateway file gives the same files
        from ecucstudio.gateway.regen import config_from_file
        with open(res.extension, "rb") as fh:
            x1 = fh.read()
        cfg2, _ = config_from_file(res.output)
        self.assertEqual(os.path.normcase(cfg2.routing_table), os.path.normcase(cfg.routing_table))
        generate(make_plan(cfg2))
        with open(res.extension, "rb") as fh:
            self.assertEqual(fh.read(), x1)

    def test_hw_rows_and_overrides(self):
        # a row of the accelerator is not checked against the DBC files (the ECU need not receive it there)
        hw = ["8", "", "Only", "CAN_FD", "0x102", "", "", "0", "1", "", "S", "D", "", "", "", "DashInfo", "CAN_FD",
              "0x300", ""]
        plan = make_plan(self.config(self.table(ROWS + [hw], "hw.csv")))
        self.assertEqual((plan.table_rows[-1].status, plan.table_rows[-1].detail),
                         ("HW accelerator", "BusA/Only -> BusB/DashInfo: HW-Accelerator = 1 (LLCE / PFE routes it)"))
        cfg = self.config()
        cfg.options.table_hw = True
        plan = make_plan(cfg)
        self.assertIn("BusA/EngineStatus->BusB/EngineStatus", [cr.key for cr in plan.enabled_can_routes])
        self.assertEqual(plan.table_rows[3].status, "routed")
        # a signal route can be switched off like a CAN -> CAN route (can_gateway); its target is fed again from
        # Ethernet when no other signal goes into it
        cfg.options.table_hw = False
        for sr in plan.signal_routes:
            cfg.can_gateway[sr.key] = {"enabled": False}
        plan = make_plan(cfg)
        self.assertEqual([sr.enabled for sr in plan.signal_routes], [False, False])
        self.assertEqual(plan.table_rows[1].status, "off")
        dash = next(r for r in plan.routes if r.key == "BusB/DashInfo" and r.direction == "ETH->CAN")
        self.assertTrue(dash.enabled)
        self.assertEqual(plan.can_routes[0].keep_signals, [])

    def test_conflicts(self):
        rows = [
            # two sources for BusB/EngineData: the second row is off
            ["0", "", "EngineData", "CAN_FD", "0x100", "", "", "0", "0", "", "S", "D", "", "", "", "EngineData",
             "CAN_FD", "0x100", ""],
            ["1", "", "EngineData", "CAN_FD", "0x100", "", "", "0", "0", "", "", "D", "S", "", "", "EngineData",
             "CAN_FD", "0x100", ""],
            # a signal into a message that is routed as a whole: off
            ["2", "EngTemp", "EngineData", "CAN_FD", "0x100", "", "", "1", "0", "", "S", "D", "", "", "EngTemp",
             "EngineData", "CAN_FD", "0x100", ""],
            # the same target signal twice: the second is off
            ["3", "EngTemp", "EngineData", "CAN_FD", "0x100", "", "", "1", "0", "", "S", "D", "", "", "Temp",
             "DashInfo", "CAN_FD", "0x300", ""],
            ["4", "EngMode", "EngineData", "CAN_FD", "0x100", "", "", "1", "0", "", "S", "D", "", "", "Temp",
             "DashInfo", "CAN_FD", "0x300", ""],
        ]
        cfg = self.config(self.table(rows))
        cfg.buses[2] = BusInput(dbc=_dbc(self.tmp, "BusC", ["GwEcu", "Body"],
                                         [(256, "EngineData", 8, "Body", _engine("GwEcu"))]), node="GwEcu")
        plan = make_plan(cfg)
        self.assertEqual(plan.errors, [])
        self.assertEqual([cr.enabled for cr in plan.can_routes], [True, False])
        self.assertIn("one source per PDU", plan.can_routes[1].reason)
        self.assertEqual([(sr.enabled, sr.reason.split(" (")[0]) for sr in plan.signal_routes],
                         [(False, "BusB/EngineData is routed as a whole from BusA/EngineData"), (True, ""),
                          (False, "Temp is already set from BusA/EngineData")])
        self.assertTrue(any("EngMode has 4 bit, Temp 8 bit" in w for w in plan.warnings), plan.warnings)

    def test_signal_routes_need_the_dbc_files_in_davinci(self):
        plan = make_plan(self.config(imported=False))          # new network file: no .vsde, no signal routes
        self.assertEqual(plan.errors, [])
        self.assertTrue(plan.signal_routes)
        self.assertFalse(any(sr.enabled for sr in plan.signal_routes))
        self.assertIn("imported in DaVinci", plan.signal_routes[0].reason)
        dash = next(r for r in plan.routes if r.key == "BusB/DashInfo" and r.direction == "ETH->CAN")
        self.assertTrue(dash.enabled)

    def test_network_names(self):
        """Network columns are matched by bus / DBName / DBC file name (also one word of it), or set per bus."""
        rows = [["0", "", "EngineData", "CAN_FD", "0x100", "", "", "0", "0", "", "S", "D", "", "", "", "EngineData",
                 "CAN_FD", "0x100", ""]]
        header = [h if h not in ("BusA", "BusB") else {"BusA": "PwrNet", "BusB": "BodyNet"}[h] for h in HEADER]
        cfg = self.config(self.table(rows, header=header))
        cfg.buses[0] = BusInput(dbc=_dbc(self.tmp, "Powertrain", ["GwEcu", "Engine"],
                                         [(256, "EngineData", 8, "Engine", _engine("GwEcu"))],
                                         file_name="Vehicle_PwrNet_v2"), node="GwEcu")
        plan = make_plan(cfg)
        self.assertEqual([cr.enabled for cr in plan.can_routes], [])        # BusB has no column yet
        self.assertEqual(plan.table_rows[0].status, "not this ECU")
        cfg.buses[1].table_network = "bodynet"
        plan = make_plan(cfg)
        self.assertEqual([cr.key for cr in plan.enabled_can_routes], ["Powertrain/EngineData->BusB/EngineData"])
        self.assertTrue(any(i.startswith("Routing table networks: PwrNet = Powertrain, BodyNet = BusB")
                            for i in plan.infos), plan.infos)

    def test_without_table_and_bad_table(self):
        cfg = self.config()
        cfg.routing_table = ""
        plan = make_plan(cfg)
        self.assertEqual(plan.table_rows, [])
        self.assertTrue(any(cr.match == "same name" for cr in plan.can_routes))    # paired by name as before
        cfg.routing_table = os.path.join(self.tmp, "missing.xlsx")
        plan = make_plan(cfg)
        self.assertTrue(plan.errors and plan.errors[0].startswith("Routing table missing.xlsx"), plan.errors)

    def test_topology_routing_table(self):
        from ecucstudio.gateway.topology import TopologyConfig
        from ecucstudio.gateway.topology.config import EcuNode
        from ecucstudio.gateway.topology.planner import TopologyPlanner
        t = TopologyConfig(name="net", routing_table=self.table(), table_hw=True, one_socket=True)
        t.ecus.append(EcuNode(name="GwEcu", ip="10.0.20.1", gateway=self.config(table="")))
        path = os.path.join(self.tmp, "sub", "gateway_network.json")
        os.makedirs(os.path.dirname(path))
        t.save(path)
        with open(path, encoding="utf-8") as fh:
            self.assertIn('"routing_table": "../routing.csv"', fh.read().replace("\\\\", "/"))
        t2 = TopologyConfig.load(path)
        self.assertEqual((os.path.normcase(t2.routing_table), t2.table_hw),
                         (os.path.normcase(os.path.join(self.tmp, "routing.csv")), True))
        gc = TopologyPlanner(t2)._ecu_config(t2.ecus[0], {})
        self.assertEqual((gc.routing_table, gc.options.table_hw), (t2.routing_table, True))


if __name__ == "__main__":
    unittest.main()
