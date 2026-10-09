"""Gateway file for an ECU whose DBC files are imported in its DaVinci project (no project given): only Ethernet +
gateway, the CAN part is referenced by the paths the Vector DBC converter creates (gateway/imported.py)."""
import os
import re
import shutil
import tempfile
import unittest

from ecucstudio.gateway import imported
from ecucstudio.gateway.base import Base
from ecucstudio.gateway.config import BusInput, GatewayConfig, SocketSide
from ecucstudio.gateway.planner import make_plan
from ecucstudio.gateway.writer import generate

HERE = os.path.dirname(os.path.abspath(__file__))
DBC = os.path.join(HERE, "fixtures", "gateway_demo.dbc")
CHASSIS = os.path.join(HERE, "fixtures", "gateway_chassis.dbc")

try:
    import cantools  # noqa: F401
    HAVE_CANTOOLS = True
except ImportError:
    HAVE_CANTOOLS = False


@unittest.skipUnless(HAVE_CANTOOLS, "cantools is not installed")
class ImportedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def config(self):
        cfg = GatewayConfig(base="", output=os.path.join(self.tmp, "Gw_CanEthGateway.arxml"), ecu="GwInst")
        cfg.options.dbc_imported = True
        # header ids entered where a CAN id would be twice on one link: ExtSameId (extended 0x100, like EngineData),
        # Chassis/GwCommand (0x300, like Body/BrakeStatus extended 0x300)
        cfg.buses = [BusInput(dbc=DBC, node="GwEcu", messages={"ExtSameId": {"header_id": "0x1001"}}),
                     BusInput(dbc=CHASSIS, node="GwEcu", messages={"GwCommand": {"header_id": "0x1300"}})]
        e = cfg.ethernet
        e.vlan_id, e.ecu_ip = 20, "10.0.20.1"
        e.can_to_eth = SocketSide(local_port=50000, remote_ip="10.0.20.2", remote_port=50001)
        e.eth_to_can = SocketSide(local_port=50001, remote_ip="10.0.20.2", remote_port=50000)
        return cfg

    def test_view_paths(self):
        view = imported.imported_view([(DBC, "GwEcu")], "GwInst")
        for p in ("/Cluster/Body/CHNL/FT_EngineData", "/Cluster/Body/CHNL/PT_EngineData",
                  "/CanFrame/EngineData_oBody", "/PDU/EngineData_oBody",
                  "/Topology/HardwareComponents/GwInst/CN_Body/FP_EngineData_Rx",
                  "/Topology/HardwareComponents/GwInst/CN_Body/PP_GwCommand_Tx"):
            self.assertIn(p, view.by_path, p)

    def test_gateway_only(self):
        plan = make_plan(self.config())
        self.assertEqual(plan.errors, [])
        self.assertTrue(plan.delta)
        self.assertTrue(plan.enabled_routes and plan.enabled_can_routes)
        res = generate(plan)
        with open(res.output, encoding="utf-8") as fh:
            text = fh.read()
        for tag in ("<CAN-CLUSTER", "<CAN-FRAME>", "<CAN-FRAME-TRIGGERING", "<SYSTEM>", "<FRAME-PORT>"):
            self.assertNotIn(tag, text)
        # every reference outside the file points to an element the DBC import creates
        out = Base(res.output)
        view = imported.imported_view([(DBC, "GwEcu"), (CHASSIS, "GwEcu")], "GwInst")
        refs = set(re.findall(r'DEST="[^"]+">([^<]+)<', text))
        outside = {r for r in refs if r not in out.by_path}
        self.assertTrue(outside)
        self.assertEqual({r for r in outside if r not in view.by_path}, set())
        self.assertIn("/Cluster/Body/CHNL/PT_EngineData", outside)
        # the ECU instance and its connectors are skeletons (no UUID: DaVinci has them from the DBC import)
        ecu = out.el("/Topology/HardwareComponents/GwInst")
        self.assertIsNotNone(ecu)
        self.assertIsNone(ecu.get("UUID"))

    def test_regeneration_unchanged(self):
        from ecucstudio.gateway.regen import config_from_file
        cfg = self.config()
        generate(make_plan(cfg))
        with open(cfg.output, "rb") as fh:
            v1 = fh.read()
        cfg2, _ = config_from_file(cfg.output)
        self.assertTrue(cfg2.options.dbc_imported)
        plan = make_plan(cfg2)
        self.assertEqual(plan.errors, [])
        self.assertEqual({r.change for r in plan.enabled_routes}, {"kept"})
        generate(plan)
        with open(cfg.output, "rb") as fh:
            self.assertEqual(fh.read(), v1)

    def test_can_to_can_in_extension_file(self):
        """CAN -> CAN routes go into the .vsde file of the DBC converter (PduR only, no Com), not the gateway file."""
        from ecucstudio.gateway import vsde
        from ecucstudio.gateway.regen import config_from_file
        cfg = self.config()
        plan = make_plan(cfg)
        self.assertEqual(plan.errors, [])
        self.assertTrue(plan.enabled_can_routes)
        self.assertTrue(all(cr.vsde for cr in plan.enabled_can_routes))
        self.assertTrue(any("Gw_CanEthGateway.vsde" in i and "Com" in i for i in plan.infos), plan.infos)
        res = generate(plan)
        self.assertEqual(res.extension, os.path.join(self.tmp, "Gw_CanEthGateway.vsde"))
        got = vsde.read(res.extension)
        want = [("GwEcu", cr.src.bus.name, cr.src.message.name, cr.dst.bus.name, cr.dst.message.name)
                for cr in plan.enabled_can_routes]
        self.assertEqual(sorted(got), sorted(want))
        self.assertIn(("GwEcu", "Body", "EngineData", "Chassis", "EngineData"), got)   # ECU of the DBC node
        # the gateway file maps no CAN PDU to a CAN PDU (DaVinci makes those from the .vsde file)
        out = Base(res.output)
        for m in out.root.iter("{http://autosar.org/schema/r4.0}I-PDU-MAPPING"):
            src, dst = out.ref(m, "SOURCE-I-PDU-REF"), out.refs(m, "TARGET-I-PDU-REF")[0]
            self.assertFalse(src.startswith("/Cluster/Body/CHNL/") and dst.startswith("/Cluster/Chassis/CHNL/"),
                             (src, dst))
        # regeneration: the routes of the .vsde file are kept, both files stay the same
        with open(res.output, "rb") as fh:
            v1 = fh.read()
        with open(res.extension, "rb") as fh:
            x1 = fh.read()
        cfg2, _ = config_from_file(cfg.output)
        cfg2.options.only_previous = True
        plan = make_plan(cfg2)
        self.assertEqual(plan.errors, [])
        self.assertEqual({cr.change for cr in plan.enabled_can_routes}, {"kept"})
        self.assertEqual(len(plan.enabled_can_routes), len(want))
        cfg2.options.only_previous = False
        generate(make_plan(cfg2))
        with open(res.output, "rb") as fh:
            self.assertEqual(fh.read(), v1)
        with open(res.extension, "rb") as fh:
            self.assertEqual(fh.read(), x1)
        # without CAN -> CAN routes the existing .vsde file is emptied (the DaVinci project may list it); the messages
        # come from Ethernet then: EngineData (0x100) next to ExtSameId (extended 0x100) and GW_BrakeStatus (extended
        # 0x300) next to Body/GwCommand (0x300) need their own header ids
        cfg2.options.can_routes = False
        cfg2.buses[1].messages.update({"EngineData": {"header_id": "0x1100"},
                                       "GW_BrakeStatus": {"header_id": "0x1301"}})
        generate(make_plan(cfg2))
        self.assertEqual(vsde.read(res.extension), [])

    def test_eth_to_can_not_sent_by_com(self):
        """CAN PDUs fed from Ethernet: the .vsde file routes each to itself for the node, so Com does not send it."""
        from ecucstudio.gateway import vsde
        cfg = self.config()
        plan = make_plan(cfg)
        self.assertEqual(plan.errors, [])
        tx = [r for r in plan.enabled_routes if r.direction == "ETH->CAN"]
        self.assertTrue(tx)
        self.assertTrue(all(r.no_com for r in tx))
        fed = [cr.dst for cr in plan.enabled_can_routes]                 # fed from CAN: no Ethernet route
        self.assertTrue(fed and not any(r.no_com for r in fed))
        # every PDU the node sends on Chassis comes from Ethernet / Body: its Com Tx I-PDU group goes away (BswM)
        self.assertTrue(any(w.startswith("Chassis: Com of GwInst no longer sends any PDU") for w in plan.warnings),
                        plan.warnings)
        res = generate(plan)
        got = vsde.read(res.extension, tx=True)
        self.assertEqual(sorted(got), sorted(("GwEcu", r.bus.name, r.message.name, r.bus.name, r.message.name)
                                             for r in tx))
        self.assertEqual(len(vsde.read(res.extension)), len(plan.enabled_can_routes))   # CAN -> CAN unchanged
        # regeneration: same .vsde file; option off: no PDU routed to itself
        with open(res.extension, "rb") as fh:
            x1 = fh.read()
        from ecucstudio.gateway.regen import config_from_file
        cfg2, _ = config_from_file(cfg.output)
        generate(make_plan(cfg2))
        with open(res.extension, "rb") as fh:
            self.assertEqual(fh.read(), x1)
        cfg2.options.eth_no_com = False
        plan = make_plan(cfg2)
        self.assertFalse(any(r.no_com for r in plan.routes))
        generate(plan)
        self.assertEqual(vsde.read(res.extension, tx=True), [])

    def test_extension_file_uses_the_ecu_attribute(self):
        """Node named per bus (Gw_BusA, Gw_BusB) with the DBC attribute ECU = Gw: the converter's ECU is Gw."""
        from ecucstudio.gateway import vsde

        def dbc(bus, node, sender, receiver):
            text = (f'VERSION ""\n\nNS_ :\n\nBS_:\n\nBU_: {node} Other\n\n'
                    f'BO_ 300 Relay: 4 {sender}\n SG_ RelaySig : 0|8@1+ (1,0) [0|255] "" {receiver}\n\n'
                    'BA_DEF_ "DBName" STRING ;\nBA_DEF_ BU_ "ECU" STRING ;\nBA_DEF_DEF_ "DBName" "";\n'
                    f'BA_DEF_DEF_ "ECU" "";\nBA_ "DBName" "{bus}";\nBA_ "ECU" BU_ {node} "Gw";\n')
            path = os.path.join(self.tmp, f"{bus}.dbc")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            return path
        cfg = self.config()
        cfg.options.eth_routes = False
        cfg.buses = [BusInput(dbc=dbc("BusA", "Gw_BusA", "Other", "Gw_BusA"), node="Gw_BusA"),
                     BusInput(dbc=dbc("BusB", "Gw_BusB", "Gw_BusB", "Other"), node="Gw_BusB")]
        plan = make_plan(cfg)
        self.assertEqual(plan.errors, [])
        self.assertEqual([cr.vsde for cr in plan.enabled_can_routes], [True])
        res = generate(plan)
        self.assertEqual(vsde.read(res.extension), [("Gw", "BusA", "Relay", "BusB", "Relay")])
        # one routing for the ECU (gateway) + one per node named otherwise (the converter drops that bus's signals)
        with open(res.extension, encoding="utf-8") as fh:
            refs = re.findall(r"<ECU-INSTANCE-REF>(\w+)<", fh.read())
        self.assertEqual(refs, ["Gw", "Gw_BusA", "Gw_BusB"])

    def test_eth_to_can_fanout(self):
        """The central node sends one Ethernet PDU, the zone ECU forwards it to every CAN bus with that CAN id
        (ETH -> CAN 1:N): the same CAN id from the same Ethernet node is one message."""
        from ecucstudio.gateway.regen import config_from_file

        def dbc(bus, start, length=8):
            text = (f'VERSION ""\n\nNS_ :\n\nBS_:\n\nBU_: Zone Rcv\n\n'
                    f'BO_ 300 HpcCmd: 8 Zone\n SG_ Cmd : {start}|8@1+ (1,0) [0|255] "" Rcv\n\n'
                    f'BO_ 303 LenCmd: {length} Zone\n SG_ Len : 0|8@1+ (1,0) [0|255] "" Rcv\n\n'
                    f'BO_ 301 OwnCmd: 8 Zone\n SG_ Own : 0|8@1+ (1,0) [0|255] "" Rcv\n\n'
                    f'BO_ 302 NoIl: 8 Zone\n SG_ NoIlSig : 0|8@1+ (1,0) [0|255] "" Rcv\n\n'
                    f'BA_DEF_ "DBName" STRING ;\nBA_DEF_ BO_ "GenMsgILSupport" ENUM "No","Yes";\n'
                    f'BA_DEF_DEF_ "DBName" "";\nBA_DEF_DEF_ "GenMsgILSupport" "Yes";\nBA_ "DBName" "{bus}";\n'
                    f'BA_ "GenMsgILSupport" BO_ 302 0;\n')
            path = os.path.join(self.tmp, f"{bus}.dbc")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            return path
        cfg = self.config()
        cfg.options.can_routes = False
        cfg.buses = [BusInput(dbc=dbc("BusA", 0), node="Zone"), BusInput(dbc=dbc("BusB", 0), node="Zone"),
                     BusInput(dbc=dbc("BusC", 8, 12), node="Zone")]      # BusC: other signal layout / length
        # own Ethernet PDU chosen by the user: then also an own header id (0x12D twice on one link is an error)
        cfg.buses[1].messages = {"OwnCmd": {"eth_pdu": "OwnCmd_B", "header_id": "0x112D"}}
        plan = make_plan(cfg)
        # LenCmd has another length on BusC: not one Ethernet PDU, and the CAN id 0x12F from the same node twice
        self.assertEqual(plan.errors, [
            "ETH->CAN BusC/LenCmd: header id 0x0000012F (CAN id 0x12F) is already used by BusA/LenCmd from 10.0.20.2 "
            "to GwInst (not 1:N with BusA/LenCmd: length differs (BusA 8, BusC 12)): two PDUs with one header id on "
            "one link. Disable one of them or enter another header id."])
        r = {x.key: x for x in plan.routes}
        ln = r["BusC/LenCmd"]
        self.assertIsNone(ln.fanout_of)
        self.assertEqual(ln.header_note, "conflict: 0x0000012F used by BusA/LenCmd")
        self.assertIs(r["BusB/LenCmd"].fanout_of, r["BusA/LenCmd"])
        # with a header id of its own BusC/LenCmd is fine
        cfg.buses[2].messages = {"LenCmd": {"header_id": "0x112F"}}
        plan = make_plan(cfg)
        self.assertEqual(plan.errors, [])
        r = {x.key: x for x in plan.routes}
        a, b, c = r["BusA/HpcCmd"], r["BusB/HpcCmd"], r["BusC/HpcCmd"]
        self.assertIsNone(a.fanout_of)
        self.assertIs(b.fanout_of, a)
        self.assertIs(c.fanout_of, a)                   # same CAN id and sender: one message, also with another layout
        self.assertIn("1:N although the signal layout differs from BusA in the DBC files", c.notes)
        self.assertTrue(any(w.startswith("ETH -> CAN 1:N: the DBC files define another signal layout on 1 bus(es) "
                                         "(BusC/HpcCmd)") for w in plan.warnings), plan.warnings)
        self.assertIn("1:N: forwarded to BusA, BusB, BusC", a.notes)
        self.assertEqual(r["BusC/LenCmd"].header_id, 0x112F)
        self.assertEqual((b.eth_pdu, b.header_id), (a.eth_pdu, a.header_id))
        self.assertEqual((c.eth_pdu, c.header_id, c.header_note), (a.eth_pdu, 0x12C, "same as BusA/HpcCmd (1:N)"))
        self.assertIsNone(r["BusB/OwnCmd"].fanout_of)
        self.assertEqual(r["BusB/OwnCmd"].fanout_reason, "not 1:N with BusA/OwnCmd: own Ethernet PDU / header id chosen")
        self.assertIs(r["BusC/OwnCmd"].fanout_of, r["BusA/OwnCmd"])
        # 1:N switched off for one bus: an own Ethernet PDU, but the CAN id from the same node is an error
        cfg_f = self.config()
        cfg_f.options.can_routes = False
        cfg_f.buses = [BusInput(dbc=x.dbc, node="Zone", messages=dict(x.messages)) for x in cfg.buses]
        cfg_f.buses[1].messages = {"OwnCmd": {"fanout": False}}
        pf = make_plan(cfg_f)
        rf = {x.key: x for x in pf.routes}
        self.assertIsNone(rf["BusB/OwnCmd"].fanout_of)
        self.assertIn("own Ethernet PDU chosen (1:N off)", rf["BusB/OwnCmd"].fanout_reason)
        self.assertEqual(len(pf.errors), 1, pf.errors)
        self.assertTrue(pf.errors[0].startswith("ETH->CAN BusB/OwnCmd: header id 0x0000012D"), pf.errors)
        # GenMsgILSupport = No: DaVinci imports it without PDU triggering -> not routed
        self.assertFalse(r["BusA/NoIl"].enabled)
        self.assertIn("GenMsgILSupport", r["BusA/NoIl"].reason)
        res = generate(plan)
        out = Base(res.output)
        maps = [(out.ref(m, "SOURCE-I-PDU-REF"), out.refs(m, "TARGET-I-PDU-REF")[0])
                for m in out.root.iter("{http://autosar.org/schema/r4.0}I-PDU-MAPPING")]
        src = [s for s, d in maps if d in ("/Cluster/BusA/CHNL/PT_HpcCmd", "/Cluster/BusB/CHNL/PT_HpcCmd")]
        self.assertEqual(len(src), 2)
        self.assertEqual(len(set(src)), 1)                               # one Ethernet PDU, two CAN buses
        with open(res.output, encoding="utf-8") as fh:
            text = fh.read()
        self.assertEqual(text.count(f"<SHORT-NAME>{a.eth_pdu}</SHORT-NAME>"), 1)
        self.assertNotIn(f"<SHORT-NAME>HpcCmd_oBusB_Eth</SHORT-NAME>", text)
        # regeneration: same file, no warning
        cfg2, _ = config_from_file(cfg.output)
        plan = make_plan(cfg2)
        self.assertEqual([w for w in plan.warnings if "already exists" in w], [])
        self.assertEqual({x.change for x in plan.enabled_routes}, {"kept"})
        generate(plan)
        with open(res.output, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), text)
        # option off: an Ethernet PDU per bus, so the CAN ids from the same node clash (errors)
        cfg2.options.eth_fanout = False
        plan = make_plan(cfg2)
        self.assertTrue(all(x.fanout_of is None for x in plan.routes))
        self.assertNotEqual(r["BusA/HpcCmd"].eth_pdu, {x.key: x for x in plan.routes}["BusB/HpcCmd"].eth_pdu)
        self.assertTrue(any(e.startswith("ETH->CAN BusB/HpcCmd: header id 0x0000012C") for e in plan.errors),
                        plan.errors)

    def test_one_ecu_of_the_network(self):
        """DBC files of the whole network -> gateway file of one target ECU (gateway only): ECU -> ECU over Ethernet,
        the CAN part referenced; the other ECUs only tell where messages go."""
        from ecucstudio.gateway import start
        from ecucstudio.gateway.topology import generate_topology, make_topology_plan

        def dbc(name, nodes, msgs):
            text = 'VERSION ""\n\nNS_ :\n\nBS_:\n\nBU_: ' + " ".join(nodes) + "\n\n"
            for mname, cid, length, sender, receiver in msgs:
                text += f'BO_ {cid} {mname}: {length} {sender}\n SG_ {mname}Sig : 0|8@1+ (1,0) [0|255] "" {receiver}\n\n'
            text += f'BA_DEF_ "DBName" STRING ;\nBA_DEF_DEF_ "DBName" "";\nBA_ "DBName" "{name}";\n'
            path = os.path.join(self.tmp, f"{name}.dbc")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            return path
        groups = {"ZC1": [(dbc("PowerBus", ["ZC1", "Engine"], [("LockCmd", 0x201, 2, "ZC1", "Engine")]), "ZC1")],
                  "ZC2": [(dbc("BodyBus", ["ZC2", "Key"], [("LockCmd", 0x201, 2, "Key", "ZC2")]), "ZC2")]}
        t = start.topology_for_dbcs(groups, self.tmp, "AUTOSAR_00052", {"ZC1": "10.0.5.11", "ZC2": "10.0.5.12"},
                                    generate={"ZC1": False, "ZC2": True}, vlan=5, gateway_only=True)
        t.ecu("ZC2").gateway.ecu = "Zone2"                  # its ECU instance in DaVinci
        t.save(os.path.join(self.tmp, "gateway_network.json"))
        tp = make_topology_plan(t)
        self.assertEqual(tp.errors, [])
        self.assertTrue(tp.plans["ZC2"].delta)
        res = dict(generate_topology(tp))
        self.assertEqual(set(res), {"ZC2"})
        out = res["ZC2"].output
        with open(out, encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn("<CAN-CLUSTER", text)
        self.assertIn(">/Cluster/BodyBus/CHNL/PT_LockCmd<", text)
        self.assertIn("<SHORT-NAME>Zone2</SHORT-NAME>", text)
        self.assertIn("SA_ZC1_CanGw_Rx", text)               # sent to ZC1
        # regeneration with the network file: identical
        from ecucstudio.gateway.topology import TopologyConfig
        with open(out, "rb") as fh:
            v1 = fh.read()
        tp2 = make_topology_plan(TopologyConfig.load(t.path))
        generate_topology(tp2)
        with open(out, "rb") as fh:
            self.assertEqual(fh.read(), v1)

    def test_topology_eth_to_can_fanout(self):
        """ZC1 sends a message over Ethernet, ZC2 forwards that one PDU to two of its buses (no header id clash)."""
        from ecucstudio.gateway import start
        from ecucstudio.gateway.topology import generate_topology, make_topology_plan

        def dbc(name, nodes, sender, receiver):
            text = ('VERSION ""\n\nNS_ :\n\nBS_:\n\nBU_: ' + " ".join(nodes) + "\n\n"
                    f'BO_ 513 LockCmd: 2 {sender}\n SG_ LockSig : 0|8@1+ (1,0) [0|255] "" {receiver}\n\n'
                    f'BA_DEF_ "DBName" STRING ;\nBA_DEF_DEF_ "DBName" "";\nBA_ "DBName" "{name}";\n')
            path = os.path.join(self.tmp, f"{name}.dbc")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            return path
        groups = {"ZC1": [(dbc("PowerBus", ["ZC1", "Engine"], "Engine", "ZC1"), "ZC1")],
                  "ZC2": [(dbc("BodyBus", ["ZC2", "Key"], "ZC2", "Key"), "ZC2"),
                          (dbc("DoorBus", ["ZC2", "Door"], "ZC2", "Door"), "ZC2")]}
        t = start.topology_for_dbcs(groups, self.tmp, "AUTOSAR_00052", {"ZC1": "10.0.5.11", "ZC2": "10.0.5.12"},
                                    generate={"ZC1": True, "ZC2": True}, vlan=5, gateway_only=True)
        t.save(os.path.join(self.tmp, "gateway_network.json"))
        tp = make_topology_plan(t)
        self.assertEqual(tp.errors, [])
        r = {x.key: x for x in tp.plans["ZC2"].enabled_routes}
        self.assertIs(r["DoorBus/LockCmd"].fanout_of, r["BodyBus/LockCmd"])
        self.assertEqual(r["DoorBus/LockCmd"].header_id, tp.plans["ZC1"].enabled_routes[0].header_id)
        out = dict(generate_topology(tp))["ZC2"].output
        with open(out, encoding="utf-8") as fh:
            text = fh.read()
        src = re.findall(r'<SOURCE-I-PDU-REF DEST="PDU-TRIGGERING">([^<]+)<', text)
        self.assertEqual(len(src), 2)
        self.assertEqual(len(set(src)), 1)

if __name__ == "__main__":
    unittest.main()
