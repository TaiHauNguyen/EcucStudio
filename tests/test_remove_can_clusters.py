"""scripts/remove_can_clusters.py: remove a CAN cluster and what only it uses from a network file."""
import contextlib
import importlib.util
import io
import os
import shutil
import tempfile
import unittest

from lxml import etree

from tests.test_gateway import HAVE_CANTOOLS, config, index

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "scripts", "remove_can_clusters.py")
_spec = importlib.util.spec_from_file_location("remove_can_clusters", SCRIPT)
rcc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rcc)


def resolved(path):
    """References of the file whose target is in the file."""
    root, idx = index(path)
    return {(etree.QName(e).localname, e.text.strip()) for e in root.iter()
            if isinstance(e.tag, str) and e.get("DEST") and e.text and e.text.strip() in idx}


@unittest.skipUnless(HAVE_CANTOOLS, "cantools is not installed")
class RemoveCanClustersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        from ecucstudio.gateway.planner import make_plan
        from ecucstudio.gateway.writer import generate
        self.net = os.path.join(self.tmp, "network.arxml")
        generate(make_plan(config(self.net)))           # Body (CAN) <-> Ethernet gateway on the base file

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_main(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = rcc.main(list(args))
        return code, out.getvalue()

    def test_remove_cluster_and_what_only_it_uses(self):
        code, text = self.run_main(self.net, "--list")
        self.assertEqual(code, 0)
        self.assertIn("Body_Cluster", text)
        out = os.path.join(self.tmp, "without_body.arxml")
        code, text = self.run_main(self.net, "body", "-o", out)           # name without _Cluster, any case
        self.assertEqual(code, 0, text)
        _, before = index(self.net)
        _, after = index(out)
        gone = set(before) - set(after)
        for p in ("/Topology/Clusters/Body_Cluster", "/Topology/Ecus/GwEcu/CN_Body", "/Topology/Ecus/GwEcu/CT_Body",
                  "/Communication/Frames/DoorStatus",                                   # frame of the bus
                  "/Topology/Clusters/EthCluster/Eth_VLAN10/DoorStatus_oBody_Eth_PT",         # gateway partner
                  "/Topology/Clusters/VLAN10_Ids/DoorStatus_oBody_Eth_ID",                   # its socket id
                  "/Communication/PDUs/DoorStatus_oBody_Eth"):                                # its PDU
            self.assertIn(p, gone, p)
        for p in ("/Topology/Ecus/GwEcu", "/Topology/Clusters/EthCluster/Eth_VLAN10",
                  "/Topology/Clusters/EthCluster/Eth_VLAN10/LampCmd_Eth_PT",                  # not routed: stays
                  "/Topology/Ecus/Gateway_GwEcu", "/System/GwSystem"):
            self.assertIn(p, after, p)
        self.assertEqual(index(out)[0].findall(".//{*}I-PDU-MAPPING"), [])                  # every route was Body's
        # every reference that pointed into the file still does (none left dangling)
        targets = {t for _tag, t in resolved(self.net)}
        root, idx = index(out)
        dangling = [e.text for e in root.iter() if isinstance(e.tag, str) and e.get("DEST") and e.text
                    and e.text.strip() in targets and e.text.strip() not in idx]
        self.assertEqual(dangling, [])
        self.assertIn("Also removed (check them)", text)
        # same line ends and declaration as the input
        with open(self.net, "rb") as a, open(out, "rb") as b:
            self.assertEqual(a.readline(), b.readline())

    def test_keep_partners_dry_run_and_errors(self):
        out = os.path.join(self.tmp, "keep.arxml")
        code, _ = self.run_main(self.net, "Body_Cluster", "--keep-partners", "-o", out)
        self.assertEqual(code, 0)
        _, after = index(out)
        self.assertIn("/Topology/Clusters/EthCluster/Eth_VLAN10/DoorStatus_oBody_Eth_PT", after)
        self.assertNotIn("/Topology/Clusters/Body_Cluster", after)
        code, text = self.run_main(self.net, "Body", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("CAN-CLUSTER", text)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "network_without_Body.arxml")))
        self.assertEqual(self.run_main(self.net, "Nope")[0], 2)
        self.assertEqual(self.run_main(self.net, "Body", "-o", self.net)[0], 2)               # never overwrite


if __name__ == "__main__":
    unittest.main()
