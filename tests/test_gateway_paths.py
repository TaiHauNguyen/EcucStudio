"""Message path report (gateway/paths.py): origin, gateways passed and destination of every message by CAN id."""
import contextlib
import io
import os
import shutil
import tempfile
import unittest

from ecucstudio.gateway import paths
from ecucstudio.gateway.config import BusInput, GatewayConfig
from ecucstudio.gateway.planner import make_plan
from ecucstudio.gateway.suggest import apply, suggest

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures")
DBC = os.path.join(FIX, "gateway_demo.dbc")
CHASSIS = os.path.join(FIX, "gateway_chassis.dbc")
TOPO = os.path.join(FIX, "topology")

try:
    import cantools  # noqa: F401
    HAVE_CANTOOLS = True
except ImportError:
    HAVE_CANTOOLS = False


def _plan(tmp, buses, name="gw"):
    cfg = GatewayConfig(base="", output=os.path.join(tmp, f"{name}.arxml"), buses=buses)
    apply(cfg, suggest(cfg))
    plan = make_plan(cfg)
    assert plan.ok, plan.errors
    return plan


@unittest.skipUnless(HAVE_CANTOOLS, "cantools is not installed")
class PathReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def rows(self, rep):
        return {(p.names, p.origin, p.destination): p for p in rep.paths}

    def test_one_gateway(self):
        plan = _plan(self.tmp, [BusInput(dbc=DBC, node="GwEcu"), BusInput(dbc=CHASSIS, node="GwEcu")])
        rep = paths.report_of_plan(plan)
        r = self.rows(rep)
        # EngineData (0x100): received on Body, sent on Chassis (CAN -> CAN) and to Ethernet (1:N)
        self.assertEqual(r[("EngineData", "Engine @ Body", "Chassis → Steering")].via, ["GwEcu"])
        eth = r[("EngineData", "Engine @ Body", "default (Ethernet)")]
        self.assertEqual(eth.via[0], "GwEcu")
        self.assertTrue(eth.via[1].startswith("[ETH 0x"))
        self.assertEqual(eth.can_id, "0x100")
        # the extended id 0x100 is another message; its two flows through the ECU are not mixed
        ext = [p for p in rep.paths if p.names == "ExtSameId"]
        self.assertEqual(sorted((p.origin, p.destination) for p in ext),
                         [("Brake @ Body", "default (Ethernet)"), ("default (Ethernet)", "Chassis → Steering")])
        self.assertEqual(ext[0].can_id, "0x00000100")
        # renamed pair BrakeStatus -> GW_BrakeStatus: one message
        self.assertIn(("BrakeStatus / GW_BrakeStatus", "Brake @ Body", "Chassis → Steering"), r)
        # not routed: the CAN -> CAN pairs that do not fit, with the reason
        reasons = {(x[1], x[4].split(" (")[0]) for x in rep.not_routed}
        self.assertIn(("DoorStatus", "signal layout differs"), reasons)
        html_path, csv_path = paths.write_report(rep, os.path.join(self.tmp, "gw"))
        with open(html_path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("EngineData", text)
        self.assertIn('id="filter"', text)
        with open(csv_path, encoding="utf-8-sig") as fh:
            self.assertTrue(fh.readline().startswith("CAN ID;Message;From;Via;To;Gateways"))

    def test_two_gateways_on_one_bus(self):
        """GwEcu forwards EngineData from Body to Chassis; a second gateway (Steering) takes it from Chassis to
        Ethernet: one path through both gateways."""
        p1 = _plan(self.tmp, [BusInput(dbc=DBC, node="GwEcu"), BusInput(dbc=CHASSIS, node="GwEcu")], "gw1")
        p2 = _plan(self.tmp, [BusInput(dbc=CHASSIS, node="Steering")], "gw2")
        rep = paths.build_report({"GwEcu": p1, "Steering": p2}, (), "two gateways")
        chain = [p for p in rep.paths if p.names == "EngineData" and p.gateways == ["GwEcu", "Steering"]]
        self.assertEqual(len(chain), 1)
        self.assertEqual(chain[0].origin, "Engine @ Body")
        self.assertIn("bus Chassis", chain[0].via)
        # a gateway is never reported as the origin on the bus it forwards to
        self.assertFalse([p for p in rep.paths if p.origin.startswith("GwEcu")])

    def test_topology(self):
        from ecucstudio.gateway.topology import EcuNode, PeerNode, TopoEthernet, TopologyConfig, make_topology_plan

        def ecu(name, ip, dbcs, gen=True):
            g = GatewayConfig(output=os.path.join(self.tmp, f"{name}.arxml"),
                              buses=[BusInput(dbc=os.path.join(TOPO, f"{d}.dbc"), node=name) for d in dbcs])
            return EcuNode(name=name, ip=ip, generate=gen, gateway=g)
        t = TopologyConfig(name="Demo", ethernet=TopoEthernet(vlan_id=60), peers=[PeerNode("Central", "10.0.60.1")],
                           default_peer="Central",
                           ecus=[ecu("ZoneA", "10.0.60.11", ["Sensor"], False),
                                 ecu("ZoneB", "10.0.60.12", ["Power", "Chassis"]),
                                 ecu("ZoneC", "10.0.60.13", ["Body"])])
        rep = paths.report_of_topology(make_topology_plan(t))
        r = self.rows(rep)
        ws = r[("WheelSpeed", "Abs @ Chassis", "Body → Door")]
        self.assertEqual(ws.gateways, ["ZoneB", "ZoneC"])
        self.assertEqual(ws.via, ["ZoneB", "[ETH 0x00000120]", "ZoneC"])
        self.assertEqual(r[("DoorState", "Door @ Body", "Power → Engine")].gateways, ["ZoneC", "ZoneB"])
        # same CAN id on two buses = one message (two origins, both to Central)
        same = [p for p in rep.paths if p.can_id == "0x300"]
        self.assertEqual({p.names for p in same}, {"PowerState / RadarObj"})
        self.assertEqual({p.origin for p in same}, {"Engine @ Power", "Radar @ Sensor"})
        self.assertEqual(r[("LightCmd", "Central (Ethernet)", "Body → Door")].gateways, ["ZoneC"])
        self.assertTrue(any("received by 2 ECU buses" in x[4] for x in rep.not_routed))

    def test_cli(self):
        from ecucstudio.gateway import cli
        cfg = GatewayConfig(base="", output=os.path.join(self.tmp, "gw.arxml"),
                            buses=[BusInput(dbc=DBC, node="GwEcu")])
        apply(cfg, suggest(cfg))
        conf = os.path.join(self.tmp, "gw.json")
        cfg.save(conf)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(cli.main(["report", conf]), 0)
        self.assertIn("EngineData", buf.getvalue())
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "gw_message_paths.html")))


if __name__ == "__main__":
    unittest.main()
