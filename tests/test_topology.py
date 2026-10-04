"""Multi-ECU (topology) mode: zone ECUs on one Ethernet network, synthetic DBC files in tests/fixtures/topology.

ZoneA (Sensor bus, reference only), ZoneB (Power + Chassis buses), ZoneC (Body bus) and the peer Central (default):
- WheelSpeed: ZoneB receives it on Chassis, ZoneC sends it on Body      -> ZoneB -> ZoneC directly
- DoorState:  ZoneC receives it on Body, ZoneB sends it on Power        -> ZoneC -> ZoneB directly
- VehSpeed:   ZoneA and ZoneB receive it, ZoneC sends it                 -> N:1, not routed without a link
- GearPos:    ZoneB receives it on Power and sends it on Chassis         -> CAN -> CAN inside ZoneB
- RadarObj (ZoneA) and PowerState (ZoneB) both have CAN id 0x300 and go to Central -> flag at Central
"""
import contextlib
import io
import os
import shutil
import tempfile
import unittest

from lxml import etree

from ecucstudio.arxml import local, q
from ecucstudio.gateway.config import BusInput, GatewayConfig
from ecucstudio.gateway.topology import (EcuNode, PeerNode, TopoEthernet, TopologyConfig, generate_topology,
                                         make_topology_plan)
from ecucstudio.gateway.topology.writer import contract_rows

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures", "topology")
BASE = os.path.join(HERE, "fixtures", "gateway_base.arxml")

try:
    import cantools  # noqa: F401
    HAVE_CANTOOLS = True
except ImportError:
    HAVE_CANTOOLS = False


def index(path):
    root = etree.parse(path).getroot()
    idx, stack = {}, [(root, "")]
    while stack:
        el, p = stack.pop()
        sn = el.find(q("SHORT-NAME"))
        if sn is not None and sn.text:
            p = p + "/" + sn.text.strip()
            idx[p] = el
        for c in el:
            if isinstance(c.tag, str) and c.tag != q("SHORT-NAME"):
                stack.append((c, p))
    return root, idx


@unittest.skipUnless(HAVE_CANTOOLS, "cantools is not installed")
class TopologyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ecu(self, name, ip, dbcs, generate=True):
        g = GatewayConfig(output=os.path.join(self.tmp, f"{name}_Gateway.arxml"),
                          buses=[BusInput(dbc=os.path.join(FIX, f"{d}.dbc"), node=name) for d in dbcs])
        return EcuNode(name=name, ip=ip, generate=generate, gateway=g)

    def topology(self, save=True):
        t = TopologyConfig(name="Demo", ethernet=TopoEthernet(vlan_id=60),
                           peers=[PeerNode("Central", "10.0.60.1")], default_peer="Central",
                           ecus=[self.ecu("ZoneA", "10.0.60.11", ["Sensor"], generate=False),
                                 self.ecu("ZoneB", "10.0.60.12", ["Power", "Chassis"]),
                                 self.ecu("ZoneC", "10.0.60.13", ["Body"])])
        if save:
            t.save(os.path.join(self.tmp, "topology.json"))
            t = TopologyConfig.load(t.path)
        return t

    def refs_resolve(self, path):
        root, idx = index(path)
        for r in root.iter():
            if isinstance(r.tag, str) and r.get("DEST"):
                self.assertIn(r.text.strip(), idx, f"{path}: {r.text}")
                self.assertEqual(local(idx[r.text.strip()]), r.get("DEST"), r.text)
        return root, idx

    def xsd(self, path):
        xsd = os.environ.get("ECUCSTUDIO_TEST_XSD")
        if xsd:
            schema = etree.XMLSchema(etree.parse(xsd))
            self.assertTrue(schema.validate(etree.parse(path)), str(schema.error_log)[:500])

    # ------------------------------------------------------------------ planning
    def test_pairing_and_contract(self):
        tp = make_topology_plan(self.topology())
        self.assertEqual(tp.errors, [])
        cross = {c.key: c for c in tp.cross}
        self.assertEqual(set(cross), {"ZoneB/Chassis/WheelSpeed -> ZoneC/Body/WheelSpeed",
                                      "ZoneC/Body/DoorState -> ZoneB/Power/DoorState",
                                      "ZoneA/Sensor/VehSpeed -> ZoneC/Body/VehSpeed"})
        self.assertTrue(cross["ZoneB/Chassis/WheelSpeed -> ZoneC/Body/WheelSpeed"].enabled)
        self.assertTrue(cross["ZoneC/Body/DoorState -> ZoneB/Power/DoorState"].enabled)
        n1 = cross["ZoneA/Sensor/VehSpeed -> ZoneC/Body/VehSpeed"]
        self.assertFalse(n1.enabled)
        self.assertIn("received by 2 ECU buses", n1.reason)
        # sender sends to the other zone only (not to Central), receiver takes it from the sender
        b = {(r.bus.name, r.message.name): r for r in tp.plans["ZoneB"].routes}
        c = {(r.bus.name, r.message.name): r for r in tp.plans["ZoneC"].routes}
        self.assertEqual(b[("Chassis", "WheelSpeed")].peers, ["ZoneC"])
        self.assertEqual(c[("Body", "WheelSpeed")].peers, ["ZoneB"])
        self.assertEqual(c[("Body", "WheelSpeed")].eth_pdu, "WheelSpeed_oChassis_Eth")      # sender's name
        self.assertEqual(c[("Body", "WheelSpeed")].header_id, b[("Chassis", "WheelSpeed")].header_id)
        self.assertEqual(c[("Body", "LightCmd")].peers, ["Central"])                          # no zone sends it
        self.assertFalse(c[("Body", "VehSpeed")].enabled)                                    # N:1
        # CAN -> CAN inside ZoneB stays
        self.assertEqual([(x.src.message.name, x.dst.bus.name) for x in tp.plans["ZoneB"].enabled_can_routes],
                         [("GearPos", "Chassis")])
        # Central receives from every zone on one socket: 0x300 and 0x150 collide
        self.assertEqual(tp.header_ids["ZoneA/Sensor/RadarObj"], 0x300)
        self.assertEqual(tp.header_ids["ZoneB/Power/PowerState"], 0x20000300)
        self.assertEqual(tp.header_ids["ZoneB/Chassis/VehSpeed"], 0x20000150)
        rows = {(r[0], r[2], r[8]): r for r in contract_rows(tp)}
        self.assertEqual(rows[("ZoneB", "WheelSpeed", "ZoneC")][9], "10.0.60.13:50001")
        self.assertEqual(rows[("ZoneB", "WheelSpeed", "ZoneC")][10], "Body/WheelSpeed")
        self.assertEqual(rows[("Central", "LightCmd", "ZoneC")][7], "10.0.60.1:50000")
        self.assertNotIn(("ZoneB", "WheelSpeed", "Central"), rows)

    def test_generate_both_ends_match(self):
        t = self.topology()
        res = dict(generate_topology(make_topology_plan(t)))
        self.assertEqual(set(res), {"ZoneB", "ZoneC"})                       # ZoneA is only referenced
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "ZoneA_Gateway.arxml")))
        self.assertTrue(os.path.isfile(t.lock_path) and os.path.isfile(t.contract_path))
        _, b = self.refs_resolve(res["ZoneB"].output)
        _, c = self.refs_resolve(res["ZoneC"].output)
        ch = "/Topology/Clusters/EthernetCluster/Channel_VLAN60"
        ids = "/Topology/Clusters/CanEthGateway_Ids"
        for idx in (b, c):                                                   # same identifier, same header id
            self.assertEqual(idx[f"{ids}/WheelSpeed_oChassis_Eth_ID"].findtext(q("HEADER-ID")), str(0x120))
            self.assertEqual(idx[f"{ids}/DoorState_oBody_Eth_ID"].findtext(q("HEADER-ID")), str(0x220))
            self.assertIn("/Communication/PDUs/WheelSpeed_oChassis_Eth", idx)
        conn_b = b[f"{ch}/SA_ZoneB_CanGw_Tx/SA_ZoneB_CanGw_Tx_to_SA_ZoneC_CanGw_Rx"]
        self.assertEqual([x.text.rsplit("/", 1)[-1] for x in conn_b.iter(q("SO-CON-I-PDU-IDENTIFIER-REF"))],
                         ["WheelSpeed_oChassis_Eth_ID"])
        self.assertEqual(b[f"{ch}/SA_ZoneC_CanGw_Rx"].findtext(".//" + q("PORT-NUMBER")), "50001")
        self.assertEqual(b[f"{ch}/NEP_ZoneC_VLAN60"].findtext(".//" + q("IPV-4-ADDRESS")), "10.0.60.13")
        conn_c = c[f"{ch}/SA_ZoneC_CanGw_Rx/SA_ZoneC_CanGw_Rx_to_SA_ZoneB_CanGw_Tx"]
        self.assertEqual([x.text.rsplit("/", 1)[-1] for x in conn_c.iter(q("SO-CON-I-PDU-IDENTIFIER-REF"))],
                         ["WheelSpeed_oChassis_Eth_ID"])
        self.assertEqual(c[f"{ch}/SA_ZoneB_CanGw_Tx"].findtext(".//" + q("PORT-NUMBER")), "50000")
        self.assertIsNotNone(c[f"{ch}/SA_ZoneC_CanGw_Rx"].find(q("CONNECTOR-REF")))    # local socket of ZoneC
        for r in res.values():
            self.xsd(r.output)

    def test_lock_file_keeps_header_ids(self):
        t = self.topology()
        res = dict(generate_topology(make_topology_plan(t)))
        before = {}
        for name, r in res.items():
            with open(r.output, "rb") as fh:
                before[name] = fh.read()
        # unchanged topology: identical files (the previous files are found automatically)
        tp = make_topology_plan(TopologyConfig.load(t.path))
        self.assertEqual(tp.errors, [])
        for name, r in generate_topology(tp):
            with open(r.output, "rb") as fh:
                self.assertEqual(fh.read(), before[name], name)
        # without ZoneA, PowerState would get 0x300 again: the lock file keeps 0x20000300 for the other ECUs
        t2 = TopologyConfig.load(t.path)
        t2.ecus = t2.ecus[1:]
        tp = make_topology_plan(t2)
        self.assertEqual(tp.header_ids["ZoneB/Power/PowerState"], 0x20000300)
        r = next(x for x in tp.plans["ZoneB"].routes if x.message.name == "PowerState")
        self.assertEqual(r.header_note, "kept (lock file)")

    def test_links_and_options(self):
        t = self.topology()
        t.links.append({"src": "ZoneB/Chassis/VehSpeed", "dst": "ZoneC/Body/VehSpeed"})
        t.routes["ZoneB/Chassis/WheelSpeed -> ZoneC/Body/WheelSpeed"] = {"also_to_default_peer": True,
                                                                          "header_id": "0x1120"}
        t.cross.from_default_peer = False
        tp = make_topology_plan(t)
        self.assertEqual(tp.errors, [])
        cross = {x.key: x for x in tp.cross}
        link = cross["ZoneB/Chassis/VehSpeed -> ZoneC/Body/VehSpeed"]
        self.assertTrue(link.enabled)
        self.assertEqual(link.match, "link")
        b = {r.message.name: r for r in tp.plans["ZoneB"].routes if r.bus.name == "Chassis"}
        c = {r.message.name: r for r in tp.plans["ZoneC"].routes}
        self.assertEqual(b["WheelSpeed"].peers, ["ZoneC", "Central"])
        self.assertEqual(b["WheelSpeed"].header_id, 0x1120)
        self.assertEqual(c["WheelSpeed"].header_id, 0x1120)
        self.assertFalse(c["LightCmd"].enabled)                              # no source and no default source
        self.assertIn("no source", c["LightCmd"].reason)
        # deselect a pair: the receiver's message comes from Central again (from_default_peer back on)
        t.cross.from_default_peer = True
        t.routes["ZoneC/Body/DoorState -> ZoneB/Power/DoorState"] = {"enabled": False}
        tp = make_topology_plan(t)
        r = next(x for x in tp.plans["ZoneB"].routes if x.message.name == "DoorState")
        self.assertEqual(r.peers, ["Central"])

    def test_checks(self):
        t = self.topology()
        t.ecus[2].ip = "10.0.60.12"
        self.assertTrue(any("has the IP address of ZoneB" in e for e in make_topology_plan(t).errors))
        t = self.topology()
        t.default_peer = "Nobody"
        self.assertTrue(any("default peer Nobody" in e for e in make_topology_plan(t).errors))
        t = self.topology()
        t.ecus[1].gateway.output = ""
        self.assertTrue(any("ZoneB: select the output file" in e for e in make_topology_plan(t).errors))
        t = self.topology()
        t.ecus[1].name = "Zone B"
        self.assertTrue(any("letters, digits" in e for e in make_topology_plan(t).errors))

    def test_config_roundtrip_and_cli(self):
        from ecucstudio.gateway import cli
        t = self.topology()
        back = TopologyConfig.load(t.path)
        self.assertEqual(back.to_dict(), t.to_dict())
        with open(t.path, encoding="utf-8") as fh:
            self.assertNotIn(self.tmp.replace("\\", "/"), fh.read().replace("\\\\", "/"))   # relative paths
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(cli.main(["topology", "plan", t.path]), 0)
            self.assertEqual(cli.main(["topology", "generate", t.path, "--ecu", "ZoneC"]), 0)
        self.assertIn("2 ECU -> ECU route(s)", buf.getvalue())
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "ZoneC_Gateway.arxml")))
        self.assertFalse(os.path.isfile(os.path.join(self.tmp, "ZoneB_Gateway.arxml")))

    # ------------------------------------------------------------------ DaVinci project + DBC-only ECU
    def _project(self):
        proj = os.path.join(self.tmp, "Proj")
        os.makedirs(os.path.join(proj, "Config", "System"))
        shutil.copy(BASE, os.path.join(proj, "Config", "System", "Communication.arxml"))
        dpa = os.path.join(proj, "Proj.dpa")
        with open(dpa, "w", encoding="utf-8") as fh:
            fh.write('''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<ProjectAssistant Version="5.24.40 SP3">
    <General><Name>Proj</Name></General>
    <References><OEMCommunicationExtract>Config/System/Communication.arxml</OEMCommunicationExtract></References>
    <Display>
        <FileSet Id="">
            <File Order="0" EcuInstance="GwEcu" Hash="0" FileCategory="legacy_communication_data">$(DpaProjectFolder)/Body.dbc</File>
        </FileSet>
        <Merge>
            <Path Id="ECU-INSTANCE" ARPath="/Topology/Ecus/GwEcu"/>
            <Path Id="SYSTEM" ARPath="/System/GwSystem"/>
        </Merge>
    </Display>
</ProjectAssistant>
''')
        return dpa

    def test_project_and_dbc_ecus(self):
        """GwEcu comes from a DaVinci project (DoorStatus received on Body), ZoneC only from a DBC (sends it)."""
        dbc = os.path.join(self.tmp, "Door.dbc")
        with open(os.path.join(FIX, "Body.dbc"), encoding="utf-8") as fh:
            text = fh.read()
        text = text.replace("BO_ 544 DoorState: 2 Door", "BO_ 512 DoorStatus: 2 ZoneC").replace('" ZoneC\n', '" Door\n')
        with open(dbc, "w", encoding="utf-8") as fh:
            fh.write(text)
        gw = EcuNode(name="GwEcu", ip="10.0.10.1", gateway=GatewayConfig(
            base=self._project(), output=os.path.join(self.tmp, "GwEcu_Gateway.arxml"),
            buses=[BusInput(channel="Body")]))
        zc = EcuNode(name="ZoneC", ip="10.0.10.3", gateway=GatewayConfig(
            output=os.path.join(self.tmp, "ZoneC_Gateway.arxml"), buses=[BusInput(dbc=dbc, node="ZoneC")]))
        t = TopologyConfig(ethernet=TopoEthernet(channel="VLAN10", vlan_id=10), peers=[PeerNode("Tester", "10.0.10.2")],
                           default_peer="Tester", ecus=[gw, zc])
        t.save(os.path.join(self.tmp, "mixed.json"))
        tp = make_topology_plan(TopologyConfig.load(t.path))
        self.assertEqual(tp.errors, [])
        self.assertEqual([c.key for c in tp.enabled_cross], ["GwEcu/Body_Cluster/DoorStatus -> ZoneC/Body/DoorStatus"])
        self.assertTrue(tp.plans["GwEcu"].delta)                              # additional input file
        res = dict(generate_topology(tp))
        _, g = self.refs_resolve(res["ZoneC"].output)
        hid = next(e for p, e in g.items() if "/DoorStatus_o" in p and p.endswith("_Eth_ID")).findtext(q("HEADER-ID"))
        with open(res["GwEcu"].output, encoding="utf-8") as fh:
            delta = fh.read()
        self.assertIn("SA_ZoneC_CanGw_Rx", delta)
        self.assertIn(f"<HEADER-ID>{hid}</HEADER-ID>", delta)
        # the IP address of a project ECU must be the one of its project
        t2 = TopologyConfig.load(t.path)
        t2.ecus[0].ip = "10.0.10.9"
        self.assertTrue(any("is 10.0.10.1 in the base" in e for e in make_topology_plan(t2).errors))


if __name__ == "__main__":
    unittest.main()
