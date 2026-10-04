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
        apply(cfg, suggest(cfg))
        generate(make_plan(cfg))
        info = start.read_update(out)
        self.assertEqual(os.path.normcase(info.cfg.previous), os.path.normcase(out))
        self.assertEqual([b.node for b in info.cfg.buses], ["GwEcu"])
        self.assertEqual(info.routes, 5)
        # add a bus, keep only the messages of the file on the old one
        info.cfg.buses.append(BusInput(dbc=CHASSIS, node="GwEcu"))
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


if __name__ == "__main__":
    unittest.main()
