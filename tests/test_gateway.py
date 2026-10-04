"""CAN <-> Ethernet gateway generator (synthetic base file and DBC in tests/fixtures).

Set ECUCSTUDIO_TEST_XSD to an AUTOSAR schema (e.g. AUTOSAR_4-7-0.xsd) to also validate the output.
"""
import codecs
import collections
import json
import os
import shutil
import tempfile
import unittest

from lxml import etree

from ecucstudio.arxml import local, q
from ecucstudio.gateway import dbcread, xmlorder
from ecucstudio.gateway.base import Base
from ecucstudio.gateway.config import BusInput, GatewayConfig, SocketSide
from ecucstudio.gateway.planner import CAN_TO_ETH, ETH_TO_CAN, make_plan
from ecucstudio.gateway.writer import generate

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(HERE, "fixtures", "gateway_base.arxml")
DBC = os.path.join(HERE, "fixtures", "gateway_demo.dbc")
CAN_ONLY = os.path.join(HERE, "fixtures", "gateway_base_can_only.arxml")

try:
    import cantools  # noqa: F401
    HAVE_CANTOOLS = True
except ImportError:
    HAVE_CANTOOLS = False


def index(path):
    root = etree.parse(path).getroot()
    idx = {}
    stack = [(root, "")]
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


def config(out, **eth):
    cfg = GatewayConfig(base=BASE, output=out)
    cfg.buses.append(BusInput(dbc=DBC, node="GwEcu"))
    cfg.ethernet.can_to_eth = SocketSide(local_socket="SA_GwEcu_Tx", remote_socket="SA_Tester_Rx")
    cfg.ethernet.eth_to_can = SocketSide(local_port=42001, remote_ip="10.0.10.2", remote_port=42001)
    for k, v in eth.items():
        setattr(cfg.ethernet, k, v)
    return cfg


@unittest.skipUnless(HAVE_CANTOOLS, "cantools is not installed")
class GatewayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.out = os.path.join(self.tmp, "network_gw.arxml")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------------ DBC
    def test_dbc_reader(self):
        db = dbcread.load(DBC)
        self.assertEqual(db.name, "Body")
        self.assertEqual(db.baudrate, 500000)
        rx, tx = db.node_messages("GwEcu")
        self.assertEqual({m.name for m in rx}, {"EngineData", "BrakeStatus", "DoorStatus", "ExtSameId"})
        self.assertEqual({m.name for m in tx}, {"GwCommand", "NmGwEcu"})
        m = {x.name: x for x in db.messages}
        self.assertTrue(m["BrakeStatus"].extended and m["BrakeStatus"].fd)
        self.assertEqual(m["BrakeStatus"].can_id, 0x300)
        self.assertEqual(m["EngineData"].cycle_ms, 10)
        self.assertTrue(m["NmGwEcu"].nm)
        sig = {s.name: s for s in m["BrakeStatus"].signals}
        self.assertFalse(sig["BrakePressure"].little_endian)
        self.assertEqual(sig["BrakePressure"].start, 7)

    def test_dbc_encodings(self):
        """DBC files with a UTF-8 BOM, in UTF-16 or with cp1252 characters are read like plain ones."""
        with open(DBC, "rb") as fh:
            plain = fh.read().decode("ascii")
        text = plain.replace('"degC"', '"' + chr(0xB0) + 'C"')   # degree sign: not in ASCII
        variants = {"bom.dbc": codecs.BOM_UTF8 + text.encode("utf-8"),
                    "utf16.dbc": text.encode("utf-16"),
                    "cp1252.dbc": text.encode("cp1252")}
        for name, data in variants.items():
            path = os.path.join(self.tmp, name)
            with open(path, "wb") as fh:
                fh.write(data)
            db = dbcread.load(path)
            self.assertEqual(len(db.messages), 6, name)
            self.assertEqual(db.name, "Body", name)
        bad = os.path.join(self.tmp, "bad.dbc")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("this is not a dbc file\n")
        with self.assertRaises(RuntimeError):
            dbcread.load(bad)

    # ------------------------------------------------------------------ plan
    def test_plan_routes_and_header_ids(self):
        plan = make_plan(config(self.out))
        self.assertEqual(plan.errors, [])
        r = {x.message.name: x for x in plan.routes}
        self.assertFalse(r["NmGwEcu"].enabled)                       # NM excluded by default
        self.assertEqual(len(plan.enabled_routes), 5)
        self.assertEqual(r["GwCommand"].direction, ETH_TO_CAN)
        self.assertEqual(r["EngineData"].direction, CAN_TO_ETH)
        # header id = CAN id padded to 32 bit, flag in bits 29..31 when it collides on the socket
        self.assertEqual(r["DoorStatus"].header_id, 0x200)
        self.assertEqual(r["BrakeStatus"].header_id, 0x300)
        self.assertEqual(r["EngineData"].header_id, 0x20000100)      # 0x100 is used by an existing PDU
        self.assertEqual(r["ExtSameId"].header_id, 0x40000100)       # 0x100 and flag 1 are taken
        self.assertEqual(r["GwCommand"].header_id, 0x300)            # other socket / direction
        self.assertEqual(sum("already used" in w for w in plan.warnings), 2)
        # the bus "Body" exists in the base file: its channel and frames are reused
        self.assertEqual(plan.buses[0].channel, "/Topology/Clusters/Body_Cluster/Body")
        self.assertFalse(plan.buses[0].new_cluster)
        self.assertTrue(r["DoorStatus"].can_ft.endswith("/DoorStatus_FT"))
        self.assertTrue(r["EngineData"].can_side_new)
        self.assertTrue(plan.gateway_new)
        self.assertEqual(plan.id_set, "/Topology/Clusters/VLAN10_Ids")

    def test_user_header_id_collision_is_an_error(self):
        cfg = config(self.out)
        cfg.buses[0].messages = {"EngineData": {"header_id": "0x100"}}
        plan = make_plan(cfg)
        self.assertTrue(any("0x00000100" in e for e in plan.errors), plan.errors)

    def test_extended_flag_option(self):
        cfg = config(self.out)
        cfg.header.extended_flag = True
        plan = make_plan(cfg)
        r = {x.message.name: x for x in plan.routes}
        self.assertEqual(r["BrakeStatus"].header_id, 0x80000300)
        self.assertEqual(r["ExtSameId"].header_id, 0x80000100)

    def test_missing_ethernet_settings(self):
        cfg = config(self.out)
        cfg.ethernet.eth_to_can = SocketSide()
        plan = make_plan(cfg)
        self.assertTrue(any("port" in e for e in plan.errors), plan.errors)

    # ------------------------------------------------------------------ generate
    def test_generate_merges_into_base(self):
        plan = make_plan(config(self.out))
        res = generate(plan)
        self.assertTrue(os.path.isfile(res.output))
        # base file text is kept: the output only adds lines
        with open(BASE, encoding="utf-8") as fh:
            base_lines = fh.read().splitlines()
        with open(res.output, encoding="utf-8") as fh:
            out_lines = fh.read().splitlines()
        it = iter(out_lines)
        self.assertTrue(all(any(line == o for o in it) for line in base_lines), "base content changed")
        root, idx = index(res.output)
        # every reference resolves to an element of the DEST type
        for r in root.iter():
            if isinstance(r.tag, str) and r.get("DEST"):
                target = idx.get(r.text.strip())
                self.assertIsNotNone(target, r.text)
                self.assertEqual(local(target), r.get("DEST"), r.text)
        # children are in schema order
        for el in root.iter():
            order = xmlorder.ORDER.get(local(el)) if isinstance(el.tag, str) else None
            if order:
                ranks = [order.index(local(c)) for c in el if isinstance(c.tag, str) and local(c) in order]
                self.assertEqual(ranks, sorted(ranks), f"order of {local(el)} children")
        # gateway mappings: CAN -> ETH and ETH -> CAN
        gw = idx["/Topology/Ecus/Gateway_GwEcu"]
        maps = [(m.findtext(q("SOURCE-I-PDU-REF")), m.findtext(".//" + q("TARGET-I-PDU-REF")))
                for m in gw.iter(q("I-PDU-MAPPING"))]
        self.assertEqual(len(maps), 5)
        self.assertIn(("/Topology/Clusters/Body_Cluster/Body/DoorStatus_PT",
                       "/Topology/Clusters/EthCluster/Eth_VLAN10/DoorStatus_oBody_Eth_PT"), maps)
        self.assertIn(("/Topology/Clusters/EthCluster/Eth_VLAN10/GwCommand_oBody_Eth_PT",
                       "/Topology/Clusters/Body_Cluster/Body/GwCommand_oBody_PT"), maps)
        # header ids and socket connections
        ids = {p.rsplit("/", 1)[-1]: int(e.findtext(q("HEADER-ID")))
               for p, e in idx.items() if local(e) == "SO-CON-I-PDU-IDENTIFIER"}
        self.assertEqual(ids["EngineData_oBody_Eth_ID"], 0x20000100)
        conn = idx["/Topology/Clusters/EthCluster/Eth_VLAN10/SA_GwEcu_Tx/GwEcu_to_Tester"]
        refs = [x.text for x in conn.iter(q("SO-CON-I-PDU-IDENTIFIER-REF"))]
        self.assertEqual(len(refs), 5)       # existing LampCmd + 4 CAN -> ETH routes
        rx_sock = idx["/Topology/Clusters/EthCluster/Eth_VLAN10/SA_GwEcu_CanGw_Rx"]
        self.assertEqual(rx_sock.findtext(".//" + q("PORT-NUMBER")), "42001")
        self.assertEqual(rx_sock.findtext(q("CONNECTOR-REF")), "/Topology/Ecus/GwEcu/GwEcu_Eth_VLAN10")
        # port directions seen from the gateway ECU
        ports = {p.rsplit("/", 1)[-1]: e.findtext(q("COMMUNICATION-DIRECTION"))
                 for p, e in idx.items() if local(e) in ("I-PDU-PORT", "FRAME-PORT")}
        self.assertEqual(ports["EngineData_oBody_Eth_GwEcu_Eth_VLAN10"], "OUT")
        self.assertEqual(ports["GwCommand_oBody_Eth_GwEcu_Eth_VLAN10"], "IN")
        self.assertEqual(ports["GwCommand_oBody_CN_Body"], "OUT")
        self.assertEqual(ports["EngineData_oBody_CN_Body"], "IN")
        # CAN frame triggering of a new frame
        ft = idx["/Topology/Clusters/Body_Cluster/Body/BrakeStatus_oBody_FT"]
        self.assertEqual(ft.findtext(q("CAN-ADDRESSING-MODE")), "EXTENDED")
        self.assertEqual(ft.findtext(q("CAN-FRAME-RX-BEHAVIOR")), "CAN-FD")
        self.assertEqual(ft.findtext(q("IDENTIFIER")), str(0x300))
        # ETH PDU copies the signal layout and shares the system signals
        eth = idx["/Communication/PDUs/BrakeStatus_oBody_Eth"]
        self.assertEqual(eth.findtext(q("LENGTH")), "16")
        maps = {m.findtext(q("SHORT-NAME")): (m.findtext(q("START-POSITION")), m.findtext(q("PACKING-BYTE-ORDER")))
                for m in eth.iter(q("I-SIGNAL-TO-I-PDU-MAPPING"))}
        self.assertEqual(maps["BrakePressure"], ("7", "MOST-SIGNIFICANT-BYTE-FIRST"))
        s_can = idx["/Communication/Signals/BrakePressure_oBrakeStatus_oBody"]
        s_eth = idx["/Communication/Signals/BrakePressure_oBrakeStatus_oBody_Eth"]
        self.assertEqual(s_can.findtext(q("SYSTEM-SIGNAL-REF")), s_eth.findtext(q("SYSTEM-SIGNAL-REF")))
        # existing CAN PDU: ETH copy of its signals
        door = idx["/Communication/PDUs/DoorStatus_oBody_Eth"]
        self.assertEqual([m.findtext(q("SHORT-NAME")) for m in door.iter(q("I-SIGNAL-TO-I-PDU-MAPPING"))],
                         ["DoorOpen"])
        # new elements are listed in the SYSTEM
        fibex = {x.text for x in idx["/System/GwSystem"].iter(q("FIBEX-ELEMENT-REF"))}
        self.assertIn("/Topology/Ecus/Gateway_GwEcu", fibex)
        self.assertIn("/Communication/PDUs/GwCommand_oBody_Eth", fibex)
        self._xsd(res.output)

    def test_new_cluster_and_rerun(self):
        cfg = config(self.out)
        cfg.buses[0].new_channel = True
        cfg.buses[0].bus = "Chassis"
        res = generate(make_plan(cfg))
        root, idx = index(res.output)
        self.assertIn("/Topology/Clusters/Chassis_Cluster/Chassis", idx)
        self.assertIn("/Topology/Ecus/GwEcu/CN_Chassis", idx)
        self.assertIn("/Topology/Ecus/GwEcu/CT_Chassis", idx)
        cl = idx["/Topology/Clusters/Chassis_Cluster"]
        self.assertEqual(cl.findtext(".//" + q("CAN-FD-BAUDRATE")), "2000000")   # the DBC has an FD frame
        comm = [x.text for x in idx["/Topology/Clusters/Chassis_Cluster/Chassis"].iter(q("COMMUNICATION-CONNECTOR-REF"))]
        self.assertEqual(comm, ["/Topology/Ecus/GwEcu/CN_Chassis"])
        self._xsd(res.output)
        # running again on the output finds every route already in place
        cfg2 = config(os.path.join(self.tmp, "again.arxml"))
        cfg2.base = res.output
        cfg2.buses[0].channel = "Chassis"
        cfg2.ethernet.eth_to_can = SocketSide(local_socket="SA_GwEcu_CanGw_Rx", remote_socket="SA_Remote_CanGw_Tx")
        plan2 = make_plan(cfg2)
        self.assertEqual(plan2.errors, [])
        self.assertEqual(plan2.enabled_routes, [])
        self.assertTrue(all("already routed" in r.reason for r in plan2.routes if r.message.name != "NmGwEcu"))

    def test_config_roundtrip(self):
        cfg = config(self.out)
        cfg.buses[0].messages = {"DoorStatus": {"enabled": False}}
        path = os.path.join(self.tmp, "gw.json")
        cfg.save(path)
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        self.assertEqual(raw["ethernet"]["eth_to_can"]["local_port"], 42001)
        with open(path, "rb") as fh:                    # as saved by Notepad: UTF-8 with BOM
            data = fh.read()
        with open(path, "wb") as fh:
            fh.write(codecs.BOM_UTF8 + data)
        back = GatewayConfig.load(path)
        self.assertEqual(os.path.normcase(back.base), os.path.normcase(BASE))
        self.assertEqual(back.buses[0].messages, {"DoorStatus": {"enabled": False}})
        plan = make_plan(back)
        self.assertFalse({r.message.name: r for r in plan.routes}["DoorStatus"].enabled)

    def test_base_inventory(self):
        b = Base(BASE)
        self.assertEqual(b.ecus(), ["/Topology/Ecus/GwEcu"])
        ch = b.eth_channels()[0]
        self.assertEqual(ch.vlan, 10)
        self.assertEqual({e.ip for e in ch.endpoints}, {"10.0.10.1", "10.0.10.2"})
        local_sock = [s for s in ch.sockets if s.connector][0]
        self.assertEqual(local_sock.port, 42000)
        self.assertEqual(local_sock.connections[0].remotes, ["/Topology/Clusters/EthCluster/Eth_VLAN10/SA_Tester_Rx"])
        self.assertEqual(b.package_for("I-SIGNAL"), "/Communication/Signals")
        self.assertIsNotNone(b.frame_triggering("/Topology/Clusters/Body_Cluster/Body", 0x200, False))

    # ------------------------------------------------------------------ base file without Ethernet
    def _can_only(self, **eth):
        cfg = GatewayConfig(base=CAN_ONLY, output=self.out)
        cfg.buses.append(BusInput(dbc=DBC, node="GwEcu"))
        cfg.ethernet.can_to_eth = SocketSide(local_port=42000, remote_ip="10.0.20.2", remote_port=42000)
        cfg.ethernet.eth_to_can = SocketSide(local_port=42001, remote_ip="10.0.20.2", remote_port=42001)
        for k, v in eth.items():
            setattr(cfg.ethernet, k, v)
        return cfg

    def test_base_without_ethernet_needs_ecu_ip(self):
        plan = make_plan(self._can_only())
        self.assertTrue(any("IP address of GwEcu" in e for e in plan.errors), plan.errors)

    def test_base_without_ethernet_creates_cluster(self):
        plan = make_plan(self._can_only(vlan_id=20, ecu_ip="10.0.20.1", mac="02:00:00:00:00:20"))
        self.assertEqual(plan.errors, [])
        self.assertTrue(plan.eth_cluster_new and plan.eth_channel_new and plan.eth_connector_new)
        self.assertTrue(plan.eth_controller_new and plan.local_endpoint_new)
        res = generate(plan)
        root, idx = index(res.output)
        ch = idx["/Topology/Clusters/EthernetCluster/Channel_VLAN20"]
        self.assertEqual(ch.findtext(".//" + q("VLAN-IDENTIFIER")), "20")
        self.assertEqual([x.text for x in ch.iter(q("COMMUNICATION-CONNECTOR-REF"))],
                         ["/Topology/Ecus/GwEcu/CN_GwEcu_VLAN20"])
        nep = idx["/Topology/Clusters/EthernetCluster/Channel_VLAN20/NEP_GwEcu_VLAN20"]
        self.assertEqual(nep.findtext(".//" + q("IPV-4-ADDRESS")), "10.0.20.1")
        conn = idx["/Topology/Ecus/GwEcu/CN_GwEcu_VLAN20"]
        self.assertEqual(conn.findtext(q("COMM-CONTROLLER-REF")), "/Topology/Ecus/GwEcu/CT_GwEcu_Eth")
        self.assertEqual(conn.findtext(".//" + q("NETWORK-ENDPOINT-REF")),
                         "/Topology/Clusters/EthernetCluster/Channel_VLAN20/NEP_GwEcu_VLAN20")
        ctrl = idx["/Topology/Ecus/GwEcu/CT_GwEcu_Eth"]
        self.assertEqual(ctrl.findtext(".//" + q("MAC-UNICAST-ADDRESS")), "02:00:00:00:00:20")
        self.assertEqual(ctrl.findtext(".//" + q("SEND-ACTIVITY")), "SENT-TAGGED")
        self.assertEqual(ctrl.findtext(".//" + q("VLAN-REF")), "/Topology/Clusters/EthernetCluster/Channel_VLAN20")
        self.assertIsNotNone(idx["/Topology/Clusters/EthernetCluster"].get("UUID"))   # base file uses UUIDs
        fibex = {x.text for x in root.iter(q("FIBEX-ELEMENT-REF"))}
        self.assertIn("/Topology/Clusters/EthernetCluster", fibex)
        sock = idx["/Topology/Clusters/EthernetCluster/Channel_VLAN20/SA_GwEcu_CanGw_Tx"]
        self.assertEqual(sock.findtext(q("CONNECTOR-REF")), "/Topology/Ecus/GwEcu/CN_GwEcu_VLAN20")
        self.assertEqual(len(list(idx["/Topology/Ecus/Gateway_GwEcu"].iter(q("I-PDU-MAPPING")))), 5)
        for r in root.iter():
            if isinstance(r.tag, str) and r.get("DEST"):
                self.assertIn(r.text.strip(), idx, r.text)
        self._xsd(res.output)

    def test_untagged_channel(self):
        plan = make_plan(self._can_only(ecu_ip="10.0.20.1"))
        self.assertEqual(plan.errors, [])
        res = generate(plan)
        root, idx = index(res.output)
        ch = idx["/Topology/Clusters/EthernetCluster/Channel_Untagged"]
        self.assertIsNone(ch.find(q("VLAN")))
        self.assertEqual(idx["/Topology/Ecus/GwEcu/CT_GwEcu_Eth"].findtext(".//" + q("SEND-ACTIVITY")),
                         "SENT-UNTAGGED")
        self._xsd(res.output)

    def test_remote_ip_must_differ_from_ecu_ip(self):
        cfg = self._can_only(vlan_id=20, ecu_ip="10.0.20.1")
        cfg.ethernet.eth_to_can.remote_ip = "10.0.20.1"
        plan = make_plan(cfg)
        self.assertTrue(any("remote IP 10.0.20.1" in e for e in plan.errors), plan.errors)

    def test_new_vlan_in_existing_cluster(self):
        cfg = config(self.out, new_channel=True, vlan_id=30, ecu_ip="10.0.30.1")
        cfg.ethernet.can_to_eth = SocketSide(local_port=43000, remote_ip="10.0.30.2", remote_port=43000)
        cfg.ethernet.eth_to_can = SocketSide(local_port=43001, remote_ip="10.0.30.2", remote_port=43001)
        plan = make_plan(cfg)
        self.assertEqual(plan.errors, [])
        self.assertFalse(plan.eth_cluster_new)
        self.assertFalse(plan.eth_controller_new)          # the ECU's controller is reused
        res = generate(plan)
        root, idx = index(res.output)
        self.assertIn("/Topology/Clusters/EthCluster/Channel_VLAN30", idx)
        port = idx["/Topology/Ecus/GwEcu/GwEcu_EthCtrl/GwEcu_EthPort"]
        self.assertEqual([x.text for x in port.iter(q("VLAN-REF"))],
                         ["/Topology/Clusters/EthCluster/Eth_VLAN10", "/Topology/Clusters/EthCluster/Channel_VLAN30"])
        self.assertEqual(idx["/Topology/Ecus/GwEcu/CN_GwEcu_VLAN30"].findtext(q("COMM-CONTROLLER-REF")),
                         "/Topology/Ecus/GwEcu/GwEcu_EthCtrl")
        self._xsd(res.output)

    # ------------------------------------------------------------------ only DBC files, no base file
    def _no_base(self, **cfg_fields):
        cfg = self._can_only(vlan_id=20, ecu_ip="10.0.20.1")
        cfg.base = ""
        for k, v in cfg_fields.items():
            setattr(cfg, k, v)
        return cfg

    def test_without_base_file(self):
        cfg = self._no_base(schema="AUTOSAR_00048")
        cfg.ethernet.ecu_ip = ""
        self.assertTrue(any("IP address of GwEcu" in e for e in make_plan(cfg).errors))
        cfg.ethernet.ecu_ip = "10.0.20.1"
        plan = make_plan(cfg)
        self.assertEqual(plan.errors, [])
        self.assertEqual(plan.ecu, "/Topology/HardwareComponents/GwEcu")       # ECU = DBC node
        res = generate(plan)
        with open(res.output, encoding="utf-8") as fh:
            self.assertIn("AUTOSAR_00048.xsd", fh.read(600))
        root, idx = index(res.output)
        system = idx["/System/System"]
        fibex = {x.text for x in system.iter(q("FIBEX-ELEMENT-REF"))}
        for path in ("/Topology/HardwareComponents/GwEcu", "/Topology/Clusters/Body_Cluster",
                     "/Topology/Clusters/EthernetCluster", "/Topology/HardwareComponents/Gateway_GwEcu"):
            self.assertIn(path, idx)
            self.assertIn(path, fibex)
        self.assertIn("/Topology/HardwareComponents/GwEcu/CN_Body", idx)
        self.assertIn("/DataTypes/BaseTypes/uint8", idx)
        self.assertEqual(len(list(idx["/Topology/HardwareComponents/Gateway_GwEcu"].iter(q("I-PDU-MAPPING")))), 5)
        for r in root.iter():
            if isinstance(r.tag, str) and r.get("DEST"):
                self.assertIn(r.text.strip(), idx, r.text)
                self.assertEqual(local(idx[r.text.strip()]), r.get("DEST"), r.text)
        self._xsd(res.output)

    def test_without_base_file_ecu_name_and_output(self):
        cfg = self._no_base(ecu="Zone Gateway")
        plan = make_plan(cfg)
        self.assertEqual(plan.errors, [])
        self.assertEqual(plan.ecu, "/Topology/HardwareComponents/Zone_Gateway")
        cfg.output = ""
        self.assertTrue(any("output file" in e for e in make_plan(cfg).errors))

    # ------------------------------------------------------------------ DaVinci project as the source
    def _project(self, extra_input: str = ""):
        """A DaVinci project folder whose merged communication description is the synthetic base file."""
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
            EXTRA
        </FileSet>
        <Merge>
            <Path Id="ECU-INSTANCE" ARPath="/Topology/Ecus/GwEcu"/>
            <Path Id="SYSTEM" ARPath="/System/GwSystem"/>
        </Merge>
    </Display>
</ProjectAssistant>
'''.replace("EXTRA", f'<File Order="1" EcuInstance="GwEcu" Hash="0" FileCategory="communication_system_extract">'
                      f'{extra_input}</File>' if extra_input else ""))
        return dpa

    def test_project_reader(self):
        from ecucstudio.gateway import dvproject
        p = dvproject.read(self._project())
        self.assertEqual(p.ecu_path, "/Topology/Ecus/GwEcu")
        self.assertEqual(p.system_path, "/System/GwSystem")
        self.assertTrue(p.communication.endswith(os.path.join("Config", "System", "Communication.arxml")))
        self.assertEqual(p.inputs[0].category, "legacy_communication_data")
        base = dvproject.load_communication(p)
        self.assertEqual(dvproject.ecu_can_channels(base, p.ecu_path), [("/Topology/Clusters/Body_Cluster/Body", 1, 0)])
        db = dvproject.channel_database(base, "/Topology/Clusters/Body_Cluster/Body", p.ecu_path)
        m = db.messages[0]
        self.assertEqual((db.name, m.name, m.can_id, m.length, m.cycle_ms, m.receivers), ("Body_Cluster", "DoorStatus",
                                                                                        0x200, 2, 100, ["GwEcu"]))

    def test_project_as_source_writes_an_additional_input_file(self):
        cfg = GatewayConfig(base=self._project(), output=self.out)
        cfg.buses.append(BusInput(channel="Body"))
        cfg.ethernet.can_to_eth = SocketSide(local_socket="SA_GwEcu_Tx", remote_socket="SA_Tester_Rx")
        plan = make_plan(cfg)
        self.assertEqual(plan.errors, [])
        self.assertTrue(plan.delta)
        self.assertEqual([r.message.name for r in plan.enabled_routes], ["DoorStatus"])
        r = plan.enabled_routes[0]
        self.assertEqual(r.can_pt, "/Topology/Clusters/Body_Cluster/Body/DoorStatus_PT")   # existing CAN element
        res = generate(plan)
        root, idx = index(res.output)
        base_root, base_idx = index(BASE)
        # only Ethernet / gateway content: no CAN cluster, frame or CAN PDU is defined again
        self.assertNotIn("CAN-FRAME", {local(e) for e in idx.values()})
        self.assertNotIn("/Topology/Clusters/Body_Cluster", idx)
        self.assertIn("/Communication/PDUs/DoorStatus_oBody_Cluster_Eth", idx)
        # existing parents are skeletons: SHORT-NAME only, no UUID (DaVinci rejects a UUID used in two files)
        conn = idx["/Topology/Clusters/EthCluster/Eth_VLAN10/SA_GwEcu_Tx/GwEcu_to_Tester"]
        self.assertEqual([x.text for x in conn.iter(q("SO-CON-I-PDU-IDENTIFIER-REF"))],
                         ["/Topology/Clusters/VLAN10_Ids/DoorStatus_oBody_Cluster_Eth_ID"])
        for p in ("/Topology/Clusters/EthCluster", "/Topology/Ecus/GwEcu"):
            self.assertIsNone(idx[p].get("UUID"), p)
        self.assertNotIn("SYSTEM", {local(e) for e in idx.values()})     # DaVinci builds the project SYSTEM
        base_uuids = {e.get("UUID") for e in base_root.iter() if isinstance(e.tag, str) and e.get("UUID")}
        self.assertFalse(base_uuids & {e.get("UUID") for e in root.iter() if isinstance(e.tag, str) and e.get("UUID")})
        # every reference resolves in project + new file
        union = dict(base_idx)
        union.update(idx)
        for ref in root.iter():
            if isinstance(ref.tag, str) and ref.get("DEST"):
                self.assertIn(ref.text.strip(), union, ref.text)
                self.assertEqual(local(union[ref.text.strip()]), ref.get("DEST"), ref.text)
        gw = [m for m in root.iter(q("I-PDU-MAPPING"))]
        self.assertEqual([m.findtext(q("SOURCE-I-PDU-REF")) for m in gw], [r.can_pt])
        self._xsd(res.output)

    # ------------------------------------------------------------------ suggested Ethernet settings
    def test_suggest_with_existing_ethernet(self):
        from ecucstudio.gateway.suggest import apply, suggest
        cfg = GatewayConfig(base=BASE, output=self.out)
        cfg.buses.append(BusInput(dbc=DBC, node="GwEcu"))
        sugg = {x.field: x.value for x in suggest(cfg)}
        self.assertEqual(sugg["ethernet.channel"], "/Topology/Clusters/EthCluster/Eth_VLAN10")
        self.assertNotIn("ethernet.ecu_ip", sugg)                      # the ECU already has an address
        self.assertEqual(sugg["ethernet.can_to_eth.remote_ip"], "10.0.10.2")      # partner of the ECU
        self.assertEqual((sugg["ethernet.can_to_eth.local_port"], sugg["ethernet.eth_to_can.local_port"]),
                         (42001, 42002))                               # next free pair after 42000
        apply(cfg, suggest(cfg))
        plan = make_plan(cfg)
        self.assertEqual(plan.errors, [])
        self._xsd(generate(plan).output)

    def test_suggest_without_ethernet(self):
        from ecucstudio.gateway.suggest import apply, suggest
        cfg = GatewayConfig(base=CAN_ONLY, output=self.out)
        cfg.buses.append(BusInput(dbc=DBC, node="GwEcu"))
        cfg.ethernet.vlan_id = 30
        sugg = {x.field: x.value for x in suggest(cfg)}
        self.assertEqual(sugg["ethernet.ecu_ip"], "192.168.30.1")
        self.assertEqual(sugg["ethernet.eth_to_can.remote_ip"], "192.168.30.2")
        self.assertEqual(sugg["ethernet.mac"], "02:00:C0:A8:1E:01")     # new controller
        self.assertEqual(sugg["ethernet.can_to_eth.local_port"], 50000)
        apply(cfg, suggest(cfg))
        plan = make_plan(cfg)
        self.assertEqual(plan.errors, [])
        self._xsd(generate(plan).output)

    def test_suggest_keeps_user_values_and_new_vlan(self):
        from ecucstudio.gateway.suggest import apply, suggest
        cfg = GatewayConfig(base=BASE, output=self.out, buses=[BusInput(dbc=DBC, node="GwEcu")])
        cfg.ethernet.new_channel = True
        cfg.ethernet.can_to_eth.local_port = 4000
        sugg = suggest(cfg)
        fields = {x.field: x.value for x in sugg}
        self.assertEqual(fields["ethernet.vlan_id"], 20)                # next free VLAN after 10
        self.assertEqual(fields["ethernet.ecu_ip"], "192.168.20.1")
        applied = apply(cfg, sugg)
        self.assertEqual(cfg.ethernet.can_to_eth.local_port, 4000)      # user value kept
        self.assertNotIn("ethernet.can_to_eth.local_port", {x.field for x in applied})
        self.assertEqual(make_plan(cfg).errors, [])

    # ------------------------------------------------------------------ editing an existing gateway
    def _generated(self):
        cfg = config(self.out)
        generate(make_plan(cfg))
        return self.out

    def test_existing_routes(self):
        from ecucstudio.gateway.existing import GatewayModel
        m = GatewayModel(self._generated())
        self.assertEqual(collections.Counter(r.direction for r in m.routes), {"CAN->ETH": 4, "ETH->CAN": 1})
        r = m.find_routes("BrakeStatus_oBody_Eth")[0]
        self.assertEqual((r.can.can_id, r.can.extended, r.can.fd, r.can.channel_name), (0x300, True, True, "Body"))
        self.assertEqual([h.header_id for h in r.eth.ids], [0x300])
        self.assertEqual([h.connections for h in r.eth.ids],
                         [["/Topology/Clusters/EthCluster/Eth_VLAN10/SA_GwEcu_Tx/GwEcu_to_Tester"]])
        self.assertEqual(m.find_routes("DoorStatus")[0].can.frame, "DoorStatus")      # frame from the base file
        self.assertEqual(m.check(), [])
        self.assertEqual(len(m.sockets()), 4)
        self.assertEqual({e.owner for e in m.endpoints()}, {"GwEcu", ""})

    def test_existing_header_id(self):
        from ecucstudio.gateway.existing import EditError, GatewayModel
        m = GatewayModel(self._generated())
        engine = m.find_routes("EngineData_oBody_Eth")[0].eth.ids[0].path
        with self.assertRaises(EditError):                 # used by LampCmd on the same socket
            m.set_header_id(engine, "0x100")
        with self.assertRaises(EditError):
            m.set_header_id(engine, "0x1FFFFFFFF")
        self.assertFalse(m.dirty)
        m.set_header_id(engine, "00004660")                # leading zeros: decimal
        m.set_header_id(engine, "0x1100")
        self.assertTrue(m.dirty)
        m.save()
        m2 = GatewayModel(self.out)
        self.assertEqual(m2.find_routes("EngineData_oBody_Eth")[0].eth.ids[0].header_id, 0x1100)
        self.assertTrue(os.path.exists(self.out + ".bak"))

    def test_existing_move_connection(self):
        from ecucstudio.gateway.existing import GatewayModel
        m = GatewayModel(self._generated())
        h = m.find_routes("DoorStatus_oBody_Eth")[0].eth.ids[0]
        rx_conn = "/Topology/Clusters/EthCluster/Eth_VLAN10/SA_GwEcu_CanGw_Rx/SA_GwEcu_CanGw_Rx_to_SA_Remote_CanGw_Tx"
        self.assertIn(rx_conn, m.connection_choices(h.path))
        m.move_header_id(h.path, h.connections[0], rx_conn)
        self.assertEqual(m.find_routes("DoorStatus_oBody_Eth")[0].eth.ids[0].connections, [rx_conn])
        m.save()
        root, idx = index(self.out)
        tx = idx["/Topology/Clusters/EthCluster/Eth_VLAN10/SA_GwEcu_Tx/GwEcu_to_Tester"]
        self.assertNotIn(h.path, [x.text for x in tx.iter(q("SO-CON-I-PDU-IDENTIFIER-REF"))])
        self._xsd(self.out)

    def test_existing_delete_route(self):
        from ecucstudio.gateway.existing import GatewayModel
        m = GatewayModel(self._generated())
        removed = m.delete_routes(m.find_routes("GwCommand_oBody_Eth"))
        self.assertEqual(removed["I-PDU-MAPPING"], 1)
        self.assertEqual(len(m.routes), 4)
        m.save()
        root, idx = index(self.out)
        for gone in ("/Communication/PDUs/GwCommand_oBody_Eth",
                     "/Topology/Clusters/EthCluster/Eth_VLAN10/GwCommand_oBody_Eth_PT",
                     "/Topology/Ecus/GwEcu/GwEcu_Eth_VLAN10/GwCommand_oBody_Eth_GwEcu_Eth_VLAN10",
                     "/Topology/Clusters/VLAN10_Ids/GwCommand_oBody_Eth_ID",
                     "/Communication/Signals/Cmd_oGwCommand_oBody_Eth"):
            self.assertNotIn(gone, idx)
        for kept in ("/Communication/PDUs/GwCommand_oBody", "/Communication/Frames/GwCommand_oBody",
                     "/Topology/Clusters/Body_Cluster/Body/GwCommand_oBody_PT",
                     "/Communication/SystemSignals/Cmd_oGwCommand_oBody"):
            self.assertIn(kept, idx)
        fibex = {x.text for x in root.iter(q("FIBEX-ELEMENT-REF"))}
        self.assertNotIn("/Communication/PDUs/GwCommand_oBody_Eth", fibex)
        for r in root.iter():
            if isinstance(r.tag, str) and r.get("DEST"):
                self.assertIn(r.text.strip(), idx, r.text)
        self._xsd(self.out)
        # mapping only: the Ethernet PDU stays
        m = GatewayModel(self.out)
        m.delete_routes(m.find_routes("DoorStatus_oBody_Eth"), cleanup=False)
        self.assertIn("/Communication/PDUs/DoorStatus_oBody_Eth", m.base.by_path)

    def test_existing_sockets_and_endpoints(self):
        from ecucstudio.gateway.existing import EditError, GatewayModel
        m = GatewayModel(self._generated())
        rx = next(s for s in m.sockets() if s.name == "SA_GwEcu_CanGw_Rx")
        with self.assertRaises(EditError):                 # same address and protocol as SA_GwEcu_Tx
            m.set_port(rx.path, 42000)
        m.set_port(rx.path, 42100)
        tester = next(e for e in m.endpoints() if e.name == "NEP_Tester")
        with self.assertRaises(EditError):
            m.set_endpoint(tester.path, ip="10.0.10.1")    # the ECU's address
        with self.assertRaises(EditError):
            m.set_endpoint(tester.path, ip="10.0.10.300")
        m.set_endpoint(tester.path, ip="10.0.10.20", mask="255.255.0.0")
        m.save()
        m2 = GatewayModel(self.out)
        self.assertEqual(next(s for s in m2.sockets() if s.name == "SA_GwEcu_CanGw_Rx").port, 42100)
        self.assertEqual({(e.ip, e.mask) for e in m2.endpoints() if e.name == "NEP_Tester"},
                         {("10.0.10.20", "255.255.0.0")})

    def test_existing_cli(self):
        import contextlib
        import io
        from ecucstudio.gateway import cli
        path = self._generated()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(cli.main(["routes", path]), 0)
            self.assertEqual(cli.main(["edit", path, "--header", "DoorStatus_oBody_Eth=0x2200",
                                       "--delete", "GwCommand_oBody_Eth"]), 0)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(["edit", path, "--header", "EngineData_oBody_Eth=0x2200"]), 1)
        self.assertIn("5 route(s)", buf.getvalue())
        from ecucstudio.gateway.existing import GatewayModel
        m = GatewayModel(path)
        self.assertEqual(len(m.routes), 4)
        self.assertEqual(m.find_routes("DoorStatus_oBody_Eth")[0].eth.ids[0].header_id, 0x2200)

    def test_existing_project_file(self):
        """The additional input file of a DaVinci project: the CAN side is outside the file."""
        from ecucstudio.gateway.existing import GatewayModel
        cfg = GatewayConfig(base=self._project(), output=self.out)
        cfg.buses.append(BusInput(channel="Body"))
        cfg.ethernet.can_to_eth = SocketSide(local_socket="SA_GwEcu_Tx", remote_socket="SA_Tester_Rx")
        generate(make_plan(cfg))
        m = GatewayModel(self.out)
        self.assertEqual([r.direction for r in m.routes], ["?->ETH"])
        self.assertIn("not in this file", m.routes[0].notes[0])
        m.set_header_id(m.routes[0].eth.ids[0].path, "0x7777")
        m.delete_routes(m.routes)
        self.assertEqual(m.routes, [])
        m.save()

    # ------------------------------------------------------------------ regenerating an imported gateway file
    def _dbc_only(self, out):
        cfg = GatewayConfig(base="", output=out)
        cfg.buses.append(BusInput(dbc=DBC, node="GwEcu"))
        e = cfg.ethernet
        e.vlan_id, e.ecu_ip = 20, "10.0.20.1"
        e.can_to_eth = SocketSide(local_port=42000, remote_ip="10.0.20.2", remote_port=42000)
        e.eth_to_can = SocketSide(local_port=42001, remote_ip="10.0.20.2", remote_port=42001)
        return cfg

    def test_regen_unchanged_is_identical(self):
        from ecucstudio.gateway.regen import config_from_file, read_meta
        generate(make_plan(self._dbc_only(self.out)))
        with open(self.out, "rb") as fh:
            v1 = fh.read()
        meta = read_meta(Base(self.out).root)
        self.assertTrue(meta["owned"]["elements"])
        cfg, notes = config_from_file(self.out)
        self.assertEqual(os.path.normcase(cfg.previous), os.path.normcase(self.out))
        plan = make_plan(cfg)
        self.assertEqual(plan.errors, [])
        self.assertEqual({r.change for r in plan.enabled_routes}, {"kept"})
        self.assertEqual(plan.removed, [])
        generate(plan)
        with open(self.out, "rb") as fh:
            self.assertEqual(fh.read(), v1)
        self._xsd(self.out)

    def test_regen_changes_keep_header_ids(self):
        from ecucstudio.gateway.existing import GatewayModel
        from ecucstudio.gateway.regen import config_from_file
        generate(make_plan(self._dbc_only(self.out)))
        before = {r.eth.name: r.eth.ids[0].header_id for r in GatewayModel(self.out).routes}
        self.assertEqual(before["ExtSameId_oBody_Eth"], 0x20000100)      # flag because EngineData has 0x100
        cfg, _ = config_from_file(self.out)
        cfg.buses[0].messages["EngineData"] = {"enabled": False}       # remove a message
        cfg.buses.append(BusInput(dbc=DBC, node="GwEcu", new_channel=True, bus="Chassis"))   # add a bus
        plan = make_plan(cfg)
        self.assertEqual(plan.errors, [])
        self.assertEqual([p.eth_pdu for p in plan.removed], ["EngineData_oBody_Eth"])
        changes = collections.Counter(r.change for r in plan.enabled_routes)
        self.assertEqual(changes, {"kept": 4, "new": 5})
        generate(plan)
        after = {r.eth.name: r.eth.ids[0].header_id for r in GatewayModel(self.out).routes}
        self.assertNotIn("EngineData_oBody_Eth", after)
        for name in ("DoorStatus_oBody_Eth", "ExtSameId_oBody_Eth", "BrakeStatus_oBody_Eth", "GwCommand_oBody_Eth"):
            self.assertEqual(after[name], before[name], name)              # kept, even 0x20000100
        self.assertNotIn(after["EngineData_oChassis_Eth"], {after["DoorStatus_oBody_Eth"], after["ExtSameId_oBody_Eth"]})
        root, idx = index(self.out)
        self.assertNotIn("/Communication/PDUs/EngineData_oBody_Eth", idx)
        self.assertIn("/Topology/Clusters/Chassis_Cluster/Chassis", idx)
        for r in root.iter():
            if isinstance(r.tag, str) and r.get("DEST"):
                self.assertIn(r.text.strip(), idx, r.text)
        self._xsd(self.out)
        # removing the bus again removes its routes
        cfg, _ = config_from_file(self.out)
        cfg.buses = cfg.buses[:1]
        plan = make_plan(cfg)
        self.assertEqual(len(plan.removed), 5)
        generate(plan)
        self.assertNotIn("/Topology/Clusters/Chassis_Cluster", index(self.out)[1])

    def _imported(self, gen_cfg: GatewayConfig, dpa: str):
        """Simulate the DaVinci import of the gateway file generated with *gen_cfg*: the project's communication
        description becomes base + gateway (same elements at the same paths)."""
        cfg = GatewayConfig(base=BASE, output=os.path.join(os.path.dirname(dpa), "Config", "System",
                                                             "Communication.arxml"), ethernet=gen_cfg.ethernet)
        off = {"enabled": False}
        cfg.buses.append(BusInput(dbc=DBC, node="GwEcu", channel="Body", bus="Body_Cluster", tx=False,
                                  messages={"EngineData": off, "BrakeStatus": off, "ExtSameId": off}))
        plan = make_plan(cfg)
        self.assertEqual([r.message.name for r in plan.enabled_routes], ["DoorStatus"])
        generate(plan)

    def test_regen_project_file(self):
        from ecucstudio.gateway.regen import config_from_file
        delta = os.path.join(self.tmp, "GwEcu_CanEthGateway.arxml")
        dpa = self._project(extra_input=delta)
        cfg = GatewayConfig(base=dpa, output=delta)
        cfg.buses.append(BusInput(channel="Body"))
        cfg.ethernet.can_to_eth = SocketSide(local_socket="SA_GwEcu_Tx", remote_socket="SA_Tester_Rx")
        generate(make_plan(cfg))
        with open(delta, "rb") as fh:
            v1 = fh.read()
        self._imported(cfg, dpa)
        # without "previous" the imported routes look already routed: nothing would be written again
        plain = make_plan(GatewayConfig(base=dpa, output=delta, buses=[BusInput(channel="Body")],
                                        ethernet=cfg.ethernet))
        self.assertEqual(plain.enabled_routes, [])
        # with the previous file the imported elements are taken out and everything is regenerated
        cfg2, _ = config_from_file(delta)
        plan = make_plan(cfg2)
        self.assertEqual(plan.errors, [])
        self.assertEqual([r.change for r in plan.enabled_routes], ["kept"])
        generate(plan)
        with open(delta, "rb") as fh:
            self.assertEqual(fh.read(), v1)

    def test_regen_old_file_without_metadata(self):
        """Files of older versions carry no configuration: it is reconstructed from the content."""
        from ecucstudio.gateway.regen import config_from_file
        delta = os.path.join(self.tmp, "GwEcu_CanEthGateway.arxml")
        dpa = self._project(extra_input=delta)
        cfg = GatewayConfig(base=dpa, output=delta)
        cfg.buses.append(BusInput(channel="Body"))
        cfg.ethernet.can_to_eth = SocketSide(local_port=43000, remote_ip="10.0.10.7", remote_port=43000)
        cfg.ethernet.eth_to_can = SocketSide(local_port=43001, remote_ip="10.0.10.7", remote_port=43001)
        generate(make_plan(cfg))
        xf = Base(delta).xf                                  # strip the metadata = file of an older version
        admin = xf.root.find(q("ADMIN-DATA"))
        xf.root.remove(admin)
        xf.save(backup=False)
        before_root, before = index(delta)
        self._imported(cfg, dpa)
        cfg2, notes = config_from_file(delta)
        self.assertTrue(any("older version" in n for n in notes))
        self.assertEqual(os.path.normcase(cfg2.base), os.path.normcase(dpa))      # project found
        self.assertTrue(cfg2.options.only_previous)
        self.assertEqual(cfg2.ethernet.can_to_eth.local_port, 43000)
        self.assertEqual(cfg2.ethernet.can_to_eth.remote_ip, "10.0.10.7")
        plan = make_plan(cfg2)
        self.assertEqual(plan.errors, [])
        generate(plan)
        after_root, after = index(delta)
        self.assertEqual(set(before), set(after))
        self.assertEqual({p: e.get("UUID") for p, e in before.items()}, {p: e.get("UUID") for p, e in after.items()})

    def test_regen_cli(self):
        import contextlib
        import io
        from ecucstudio.gateway import cli
        generate(make_plan(self._dbc_only(self.out)))
        conf = os.path.join(self.tmp, "gw.json")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(cli.main(["reopen", self.out, "-o", conf]), 0)
            self.assertEqual(cli.main(["plan", self.out]), 0)
            self.assertEqual(cli.main(["generate", self.out]), 0)
        self.assertIn("5 route(s) kept, 0 new, 0 removed", buf.getvalue())
        with open(conf, encoding="utf-8") as fh:
            self.assertTrue(json.load(fh)["previous"])

    def test_new_vlan_that_already_exists(self):
        plan = make_plan(config(self.out, new_channel=True, vlan_id=10, ecu_ip="10.0.10.5"))
        self.assertTrue(any("already has VLAN 10" in e for e in plan.errors), plan.errors)

    def _xsd(self, path):
        xsd = os.environ.get("ECUCSTUDIO_TEST_XSD")
        if not xsd:
            return
        schema = etree.XMLSchema(etree.parse(xsd))
        ok = schema.validate(etree.parse(path))
        self.assertTrue(ok, "\n".join(str(e) for e in list(schema.error_log)[:10]))


if __name__ == "__main__":
    unittest.main()
