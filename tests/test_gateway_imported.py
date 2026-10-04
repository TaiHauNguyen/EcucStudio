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
        cfg.buses = [BusInput(dbc=DBC, node="GwEcu"), BusInput(dbc=CHASSIS, node="GwEcu")]
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


if __name__ == "__main__":
    unittest.main()
