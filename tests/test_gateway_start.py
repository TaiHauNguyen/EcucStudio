"""Guided start of the gateway generator (gateway/start.py): the three situations of the start wizard."""
import os
import shutil
import tempfile
import unittest

from ecucstudio.gateway import dbcread, start
from ecucstudio.gateway.config import BusInput, GatewayConfig
from ecucstudio.gateway.planner import make_plan
from ecucstudio.gateway.suggest import apply, suggest
from ecucstudio.gateway.writer import generate

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures")
BASE = os.path.join(FIX, "gateway_base.arxml")
DBC = os.path.join(FIX, "gateway_demo.dbc")
CHASSIS = os.path.join(FIX, "gateway_chassis.dbc")
# header ids entered where a CAN id would be twice on one link (ExtSameId: extended 0x100 like EngineData;
# Chassis/GwCommand: 0x300 like Body/BrakeStatus extended 0x300)
BODY_IDS = {"ExtSameId": {"header_id": "0x1001"}}
CHASSIS_IDS = {"GwCommand": {"header_id": "0x1300"}}

try:
    import cantools  # noqa: F401
    HAVE_CANTOOLS = True
except ImportError:
    HAVE_CANTOOLS = False


@unittest.skipUnless(HAVE_CANTOOLS, "cantools is not installed")
class StartTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def renamed(self, src, old, new):
        path = os.path.join(self.tmp, f"{new}.dbc")
        with open(src, encoding="utf-8") as fh:
            text = fh.read().replace(old, new)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def project(self, extra=""):
        proj = os.path.join(self.tmp, "Proj")
        os.makedirs(os.path.join(proj, "Config", "System"), exist_ok=True)
        shutil.copy(BASE, os.path.join(proj, "Config", "System", "Communication.arxml"))
        dpa = os.path.join(proj, "Proj.dpa")
        extra_file = (f'<File Order="1" EcuInstance="GwEcu" Hash="0" FileCategory="communication_system_extract">'
                      f'{extra}</File>') if extra else ""
        with open(dpa, "w", encoding="utf-8") as fh:
            fh.write(f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<ProjectAssistant Version="5.24.40 SP3">
    <General><Name>Proj</Name></General>
    <References><OEMCommunicationExtract>Config/System/Communication.arxml</OEMCommunicationExtract></References>
    <Display>
        <FileSet Id="">
            <File Order="0" EcuInstance="GwEcu" Hash="0" FileCategory="legacy_communication_data">$(DpaProjectFolder)/Body.dbc</File>
            {extra_file}
        </FileSet>
        <Merge>
            <Path Id="ECU-INSTANCE" ARPath="/Topology/Ecus/GwEcu"/>
            <Path Id="SYSTEM" ARPath="/System/GwSystem"/>
        </Merge>
    </Display>
</ProjectAssistant>
''')
        return dpa

    # ------------------------------------------------------------------ node suggestion
    def test_guess_nodes(self):
        one = start.guess_nodes([dbcread.load(DBC)])[0]
        self.assertEqual(one.node, "GwEcu")                     # most messages, to be checked
        self.assertFalse(one.sure)
        both = start.guess_nodes([dbcread.load(DBC), dbcread.load(CHASSIS)])
        self.assertEqual([g.node for g in both], ["GwEcu", "GwEcu"])
        self.assertTrue(all(g.sure for g in both))              # in every DBC
        # different names per bus: the gateway-like name wins; the new ECU gets their common prefix
        body = self.renamed(DBC, "GwEcu", "CGW_Body")
        chassis = self.renamed(CHASSIS, "GwEcu", "CGW_Chassis")
        g = start.guess_nodes([dbcread.load(body), dbcread.load(chassis)])
        self.assertEqual([x.node for x in g], ["CGW_Body", "CGW_Chassis"])
        self.assertEqual(start.ecu_name_for([x.node for x in g]), "CGW")
        self.assertEqual(start.ecu_name_for(["GwEcu", "GwEcu"]), "GwEcu")

    # ------------------------------------------------------------------ case 1: only DBC files
    def test_case_dbc_files(self):
        out = start.default_output(self.tmp, "GwEcu")
        self.assertTrue(out.endswith("GwEcu_Gateway.arxml"))
        cfg = start.config_for_dbcs([(DBC, "GwEcu"), (CHASSIS, "GwEcu")], out, "GwEcu",
                                    start.DAVINCI_SCHEMAS["5.24 or older"])
        self.assertEqual(cfg.schema, "AUTOSAR_00049")
        cfg.buses[0].messages, cfg.buses[1].messages = dict(BODY_IDS), dict(CHASSIS_IDS)
        apply(cfg, suggest(cfg))
        plan = make_plan(cfg)
        self.assertEqual(plan.errors, [])
        self.assertTrue(plan.enabled_routes and plan.enabled_can_routes)
        self.assertTrue(start.is_gateway_file(generate(plan).output))
        self.assertFalse(start.is_gateway_file(BASE))

    # ------------------------------------------------------------------ case 2: project with DBC files
    def test_case_project(self):
        dpa = self.project()
        info = start.read_project(dpa)
        self.assertEqual(info.project.ecu_name, "GwEcu")
        self.assertEqual([c[1] for c in info.channels], ["Body"])
        self.assertEqual(info.gateway_files, [])
        out = start.default_output(info.project.dir, "GwEcu", project=True)
        cfg = start.config_for_project(dpa, [info.channels[0][0]], out)
        apply(cfg, suggest(cfg, info.base))
        plan = make_plan(cfg)
        self.assertEqual(plan.errors, [])
        self.assertTrue(plan.delta)
        generate(plan)
        # the project lists the generated file: the wizard offers to update it instead of making a second one
        dpa2 = self.project(extra=out)
        self.assertEqual([os.path.normcase(p) for p in start.project_gateway_files(dpa2)], [os.path.normcase(out)])

    # ------------------------------------------------------------------ case 3: update a gateway file
    def test_case_update(self):
        out = os.path.join(self.tmp, "GwEcu_Gateway.arxml")
        cfg = start.config_for_dbcs([(DBC, "GwEcu")], out, "GwEcu", "AUTOSAR_00052")
        cfg.buses[0].messages = dict(BODY_IDS)
        apply(cfg, suggest(cfg))
        generate(make_plan(cfg))
        info = start.read_update(out)
        self.assertEqual(os.path.normcase(info.cfg.previous), os.path.normcase(out))
        self.assertEqual([b.node for b in info.cfg.buses], ["GwEcu"])
        self.assertEqual(info.routes, 5)
        # add a bus, keep only the messages of the file on the old one
        info.cfg.buses.append(BusInput(dbc=CHASSIS, node="GwEcu", messages=dict(CHASSIS_IDS)))
        info.cfg.options.only_previous = True
        plan = make_plan(info.cfg)
        self.assertEqual(plan.errors, [])
        self.assertTrue(any(r.change == "kept" for r in plan.enabled_routes))
        self.assertTrue(any(r.change == "new" and r.bus.name == "Chassis" for r in plan.enabled_routes))

    def test_update_lists_unused_project_channels(self):
        delta = os.path.join(self.tmp, "Proj", "GwEcu_CanEthGateway.arxml")
        dpa = self.project(extra=delta)
        cfg = GatewayConfig(base=dpa, output=delta, buses=[BusInput(channel="Body")])
        apply(cfg, suggest(cfg))
        generate(make_plan(cfg))
        info = start.read_update(delta)
        self.assertEqual(info.unused_channels, [])               # Body is the only channel and is used
        self.assertEqual(os.path.normcase(info.cfg.base), os.path.normcase(dpa))


    def test_project_ecu_with_partner_dbc(self):
        """The ECU of a DaVinci project (DBC files imported) and a partner ECU known from its DBC: the project's file
        holds only Ethernet + gateway (no CAN cluster / frame: nothing is imported twice in DaVinci)."""
        from ecucstudio.gateway.topology import generate_topology, make_topology_plan
        dpa = self.project()
        info = start.read_project(dpa)
        self.assertEqual(start.project_ecu_ip(info), "10.0.10.1")
        self.assertEqual(start.channels_in_use(info), [info.channels[0][0]])
        partner = os.path.join(self.tmp, "Cabin.dbc")
        with open(partner, "w", encoding="utf-8") as fh:
            fh.write('VERSION ""\n\nNS_ :\n\nBS_:\n\nBU_: ZC9 Seat\n\n'
                     'BO_ 512 DoorStatus: 2 ZC9\n SG_ DoorOpen : 0|1@1+ (1,0) [0|1] "" Seat\n\n'
                     'BA_DEF_ "DBName" STRING ;\nBA_DEF_DEF_ "DBName" "";\nBA_ "DBName" "Cabin";\n')
        groups = {"GwEcu": [], "ZC9": [(partner, "ZC9")]}
        t = start.topology_for_dbcs(groups, self.tmp, "AUTOSAR_00052", {"GwEcu": "10.0.10.1", "ZC9": "10.0.10.9"},
                                    generate={"GwEcu": True, "ZC9": False}, vlan=10,
                                    projects={"GwEcu": (dpa, start.channels_in_use(info))})
        t.ethernet.channel = "VLAN10"
        t.save(os.path.join(self.tmp, "topology.json"))
        tp = make_topology_plan(t)
        self.assertEqual(tp.errors, [])
        self.assertEqual([c.key for c in tp.enabled_cross], ["GwEcu/Body_Cluster/DoorStatus -> ZC9/Cabin/DoorStatus"])
        self.assertTrue(tp.plans["GwEcu"].delta)
        res = dict(generate_topology(tp))
        self.assertEqual(set(res), {"GwEcu"})                         # ZC9 is only referenced
        out = res["GwEcu"].output
        self.assertEqual(os.path.dirname(out), os.path.dirname(dpa))  # next to the project
        with open(out, encoding="utf-8") as fh:
            text = fh.read()
        for tag in ("<CAN-CLUSTER", "<CAN-FRAME ", "<CAN-FRAME>", "<CAN-FRAME-TRIGGERING"):
            self.assertNotIn(tag, text)
        self.assertIn("<I-PDU-MAPPING>", text)
        self.assertIn("SA_ZC9_CanGw_Rx", text)

    # ------------------------------------------------------------------ several ECUs found by their node names
    def network_dbcs(self):
        """PowerBus has ZC1, BodyBus has ZC2, ChassisBus and SensorBus have ZC3 (synthetic)."""
        def dbc(name, nodes, msgs):
            text = 'VERSION ""\n\nNS_ :\n\nBS_:\n\nBU_: ' + " ".join(nodes) + "\n\n"
            for mname, cid, length, sender, receiver in msgs:
                text += f'BO_ {cid} {mname}: {length} {sender}\n SG_ {mname}Sig : 0|8@1+ (1,0) [0|255] "" {receiver}\n\n'
            text += f'BA_DEF_ "DBName" STRING ;\nBA_DEF_DEF_ "DBName" "";\nBA_ "DBName" "{name}";\n'
            path = os.path.join(self.tmp, f"{name}.dbc")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            return path
        return [dbc("PowerBus", ["ZC1", "Engine"], [("LockCmd", 0x201, 2, "ZC1", "Engine"),
                                                    ("Torque", 0x300, 8, "Engine", "ZC1")]),
                dbc("BodyBus", ["ZC2", "Key"], [("LockCmd", 0x201, 2, "Key", "ZC2")]),
                dbc("ChassisBus", ["ZC3", "Abs"], [("WheelInfo", 0x120, 8, "Abs", "ZC3")]),
                dbc("SensorBus", ["ZC3", "Radar"], [("WheelInfo", 0x120, 8, "ZC3", "Radar")])]

    def test_ecus_from_node_names(self):
        from ecucstudio.gateway import paths
        from ecucstudio.gateway.topology import generate_topology, make_topology_plan
        files = self.network_dbcs()
        dbs = [dbcread.load(f) for f in files]
        guesses = start.guess_nodes(dbs)
        self.assertEqual([g.node for g in guesses], ["ZC1", "ZC2", "ZC3", "ZC3"])
        groups = start.group_ecus([(f, g.node, start.ecu_for_node(g.node, db.name))
                                   for f, g, db in zip(files, guesses, dbs)])
        self.assertEqual({e: [os.path.basename(p) for p, _n in v] for e, v in groups.items()},
                         {"ZC1": ["PowerBus.dbc"], "ZC2": ["BodyBus.dbc"], "ZC3": ["ChassisBus.dbc", "SensorBus.dbc"]})
        ips = dict(zip(groups, start.suggest_ips(len(groups), 20)))
        t = start.topology_for_dbcs(groups, self.tmp, "AUTOSAR_00049", ips, vlan=20)
        t.save(os.path.join(self.tmp, "topology.json"))
        tp = make_topology_plan(t)
        self.assertEqual(tp.errors, [])
        # BodyBus -> PowerBus: CAN -> ETH on ZC2, ETH -> CAN on ZC1
        self.assertEqual([c.key for c in tp.enabled_cross], ["ZC2/BodyBus/LockCmd -> ZC1/PowerBus/LockCmd"])
        r2 = {r.message.name: r for r in tp.plans["ZC2"].enabled_routes}
        r1 = {r.message.name: r for r in tp.plans["ZC1"].enabled_routes}
        self.assertEqual((r2["LockCmd"].direction, r2["LockCmd"].peers), ("CAN->ETH", ["ZC1"]))
        self.assertEqual((r1["LockCmd"].direction, r1["LockCmd"].peers), ("ETH->CAN", ["ZC2"]))
        # ChassisBus -> SensorBus: inside ZC3, no Ethernet
        self.assertEqual([(c.src.bus.name, c.dst.bus.name) for c in tp.plans["ZC3"].enabled_can_routes],
                         [("ChassisBus", "SensorBus")])
        self.assertEqual(tp.plans["ZC3"].enabled_routes, [])
        # no central node: a message no ECU needs is not routed
        self.assertNotIn("Torque", r1)
        res = dict(generate_topology(tp))
        self.assertEqual(set(res), {"ZC1", "ZC2", "ZC3"})
        rep = paths.report_of_topology(tp)
        p = {(x.names, x.origin): x for x in rep.paths}
        self.assertEqual(p[("LockCmd", "Key @ BodyBus")].gateways, ["ZC2", "ZC1"])
        self.assertEqual(p[("LockCmd", "Key @ BodyBus")].destination, "PowerBus → Engine")
        self.assertEqual(p[("WheelInfo", "Abs @ ChassisBus")].via, ["ZC3"])
        # one ECU named after its buses stays one ECU
        self.assertEqual(start.ecu_for_node("XGW_Body", "Body"), "XGW")
        self.assertEqual(start.ecu_for_node("ZC1", "PowerBus"), "ZC1")

if __name__ == "__main__":
    unittest.main()
