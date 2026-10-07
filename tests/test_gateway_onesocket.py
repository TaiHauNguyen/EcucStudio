"""One socket for both directions, and the Ethernet node table (gateway/nodes.py)."""
import os
import shutil
import tempfile
import unittest

from ecucstudio.gateway import nodes
from ecucstudio.gateway.base import Base
from ecucstudio.gateway.config import BusInput, EthPeer, GatewayConfig, SocketSide
from ecucstudio.gateway.planner import make_plan
from ecucstudio.gateway.writer import generate

try:
    import cantools  # noqa: F401
    HAVE_CANTOOLS = True
except ImportError:
    HAVE_CANTOOLS = False

NS = "{http://autosar.org/schema/r4.0}"
HERE = os.path.dirname(os.path.abspath(__file__))
DBC = os.path.join(HERE, "fixtures", "gateway_demo.dbc")          # node GwEcu: receives and sends messages
TABLE = [nodes.EthNode("Central", "02:00:00:00:00:01", "10.0.9.1", 51100),
         nodes.EthNode("GwEcu", "02:00:00:00:00:02", "10.0.9.2", 51200),
         nodes.EthNode("ZoneB", "02:00:00:00:00:03", "10.0.9.3", 51300)]


class _Settings(dict):
    saved = 0

    def save(self):
        self.saved += 1


@unittest.skipUnless(HAVE_CANTOOLS, "cantools is not installed")
class OneSocketTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def config(self):
        cfg = GatewayConfig(base="", output=os.path.join(self.tmp, "GwEcu_Gw.arxml"), ecu="GwEcu")
        cfg.options.dbc_imported = True
        cfg.options.can_routes = False
        cfg.buses = [BusInput(dbc=DBC, node="GwEcu")]
        cfg.ethernet.vlan_id = 9
        cfg.ethernet.default_peer = "Central"
        cfg.ethernet.one_socket = True
        return cfg

    def test_node_table_fills_empty_fields(self):
        cfg = self.config()
        cfg.ethernet.peers = [EthPeer("ZoneB")]
        cfg.ethernet.can_to_eth.remote_port = 40000                         # typed by the user: kept
        done = nodes.fill_gateway(cfg, TABLE)
        e = cfg.ethernet
        self.assertEqual((e.ecu_ip, e.mac, e.can_to_eth.local_port), ("10.0.9.2", "02:00:00:00:00:02", 51200))
        self.assertEqual((e.can_to_eth.remote_ip, e.can_to_eth.remote_port), ("10.0.9.1", 40000))
        self.assertEqual((e.peers[0].can_to_eth.remote_ip, e.peers[0].can_to_eth.remote_port), ("10.0.9.3", 51300))
        self.assertIsNone(e.eth_to_can.local_port)                          # one socket: not used
        self.assertTrue(any(x.startswith("GwEcu (this ECU)") for x in done), done)
        self.assertEqual(nodes.fill_gateway(cfg, TABLE), [])                 # nothing empty any more
        # a socket per direction: sends from the port base, receives on port base + 1
        cfg2 = self.config()
        cfg2.ethernet.one_socket = False
        nodes.fill_gateway(cfg2, TABLE)
        e2 = cfg2.ethernet
        self.assertEqual((e2.can_to_eth.local_port, e2.eth_to_can.local_port), (51200, 51201))
        self.assertEqual((e2.can_to_eth.remote_port, e2.eth_to_can.remote_port), (51101, 51100))
        # node of a DBC named per bus, case-insensitive
        self.assertIs(nodes.find(TABLE, "gwecu_Body"), TABLE[1])
        self.assertIsNone(nodes.find(TABLE, "Other"))

    def test_node_table_settings(self):
        st = _Settings()
        nodes.save(TABLE, st)
        self.assertEqual(st.saved, 1)
        self.assertEqual(nodes.load(st), TABLE)
        st[nodes.SETTINGS_KEY].append({"name": " ", "ip": "1.2.3.4"})        # no name: ignored
        self.assertEqual(len(nodes.load(st)), 3)

    def test_one_socket_for_both_directions(self):
        cfg = self.config()
        nodes.fill_gateway(cfg, TABLE)
        plan = make_plan(cfg)
        self.assertEqual(plan.errors, [])
        tx, rx = plan.sides[("CAN->ETH", "Central")], plan.sides[("ETH->CAN", "Central")]
        self.assertEqual((tx.local, tx.remote, tx.connection), (rx.local, rx.remote, rx.connection))
        self.assertEqual((tx.local_port, tx.remote_port), (51200, 51100))
        res = generate(plan)
        out = Base(res.output)
        socks = {s.findtext(NS + "SHORT-NAME") for s in out.root.iter(NS + "SOCKET-ADDRESS")}
        self.assertEqual(socks, {"SA_GwEcu_CanGw", "SA_Remote_CanGw"})
        conns = list(out.root.iter(NS + "STATIC-SOCKET-CONNECTION"))
        self.assertEqual(len(conns), 1)
        ids = [x.text.rsplit("/", 1)[-1] for x in conns[0].iter(NS + "SO-CON-I-PDU-IDENTIFIER-REF")]
        self.assertTrue(any(r.direction == "CAN->ETH" for r in plan.enabled_routes))
        self.assertTrue(any(r.direction == "ETH->CAN" for r in plan.enabled_routes))
        self.assertEqual(len(ids), len(plan.enabled_routes))                # both directions on one connection
        # regeneration: same file; a configuration without the key (older version) keeps two sockets
        from ecucstudio.gateway.regen import config_from_file
        with open(res.output, "rb") as fh:
            v1 = fh.read()
        cfg2, _ = config_from_file(res.output)
        self.assertTrue(cfg2.ethernet.one_socket)
        generate(make_plan(cfg2))
        with open(res.output, "rb") as fh:
            self.assertEqual(fh.read(), v1)
        d = cfg.to_dict()
        del d["ethernet"]["one_socket"]
        self.assertFalse(GatewayConfig.from_dict(d).ethernet.one_socket)

    def test_topology_one_socket(self):
        from ecucstudio.gateway import start
        from ecucstudio.gateway.topology import TopologyConfig, generate_topology, make_topology_plan

        def dbc(name, nodes_, sender, receiver):
            text = ('VERSION ""\n\nNS_ :\n\nBS_:\n\nBU_: ' + " ".join(nodes_) + "\n\n"
                    f'BO_ 513 LockCmd: 2 {sender}\n SG_ LockSig : 0|8@1+ (1,0) [0|255] "" {receiver}\n\n'
                    f'BO_ 514 Status: 2 {receiver}\n SG_ StatusSig : 0|8@1+ (1,0) [0|255] "" {sender}\n\n'
                    f'BA_DEF_ "DBName" STRING ;\nBA_DEF_DEF_ "DBName" "";\nBA_ "DBName" "{name}";\n')
            path = os.path.join(self.tmp, f"{name}.dbc")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            return path
        table = [nodes.EthNode("Central", "", "10.0.9.1", 51100), nodes.EthNode("ZoneA", "02:00:00:00:00:0A",
                                                                                "10.0.9.2", 51200),
                 nodes.EthNode("ZoneB", "", "10.0.9.3", 51300)]
        groups = {"ZoneA": [(dbc("BusA", ["ZoneA", "Engine"], "Engine", "ZoneA"), "ZoneA")],
                  "ZoneB": [(dbc("BusB", ["ZoneB", "Key"], "ZoneB", "Key"), "ZoneB")]}
        t = start.topology_for_dbcs(groups, self.tmp, "AUTOSAR_00052", {}, {"ZoneA": True, "ZoneB": True}, vlan=9,
                                    peer=("Central", ""), gateway_only=True, one_socket=True, eth_nodes=table)
        self.assertTrue(t.one_socket)
        self.assertEqual([(n.name, n.ip, n.tx_port) for n in [*t.ecus, *t.peers]],
                         [("ZoneA", "10.0.9.2", 51200), ("ZoneB", "10.0.9.3", 51300), ("Central", "10.0.9.1", 51100)])
        self.assertEqual(t.ecu("ZoneA").mac, "02:00:00:00:00:0A")
        t.save(os.path.join(self.tmp, "gateway_network.json"))
        self.assertTrue(TopologyConfig.load(t.path).one_socket)
        tp = make_topology_plan(t)
        self.assertEqual(tp.errors, [])
        a = tp.plans["ZoneA"]
        to_b, from_b = a.sides[("CAN->ETH", "ZoneB")], a.sides[("ETH->CAN", "ZoneB")]
        self.assertEqual((to_b.local, to_b.remote, to_b.connection), (from_b.local, from_b.remote, from_b.connection))
        self.assertEqual((to_b.local_port, to_b.remote_port), (51200, 51300))
        self.assertTrue(to_b.local.endswith("/SA_ZoneA_CanGw") and to_b.remote.endswith("/SA_ZoneB_CanGw"))
        res = dict(generate_topology(tp))
        out = Base(res["ZoneA"].output)
        self.assertEqual(out.root.findtext(f".//{NS}MAC-UNICAST-ADDRESS"), "02:00:00:00:00:0A")
        # every message is paired between the zones (nothing for Central): one socket each side, no _Tx / _Rx
        socks = {s.findtext(NS + "SHORT-NAME") for s in out.root.iter(NS + "SOCKET-ADDRESS")}
        self.assertEqual(socks, {"SA_ZoneA_CanGw", "SA_ZoneB_CanGw"})
        self.assertEqual(len(list(out.root.iter(NS + "STATIC-SOCKET-CONNECTION"))), 1)


if __name__ == "__main__":
    unittest.main()
