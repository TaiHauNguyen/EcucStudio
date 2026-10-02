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
