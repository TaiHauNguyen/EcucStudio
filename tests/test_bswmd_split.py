"""A module definition split over several BSWMD files (AUTOSAR splitable ECUC-MODULE-DEF)."""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ecucstudio.bswmd import DefinitionRepository  # noqa: E402

HEAD = ('<?xml version="1.0" encoding="UTF-8"?>\n<AUTOSAR xmlns="http://autosar.org/schema/r4.0"><AR-PACKAGES>'
        '<AR-PACKAGE><SHORT-NAME>MICROSAR</SHORT-NAME><ELEMENTS><ECUC-MODULE-DEF><SHORT-NAME>EcuM</SHORT-NAME>'
        '<LOWER-MULTIPLICITY>0</LOWER-MULTIPLICITY><UPPER-MULTIPLICITY>1</UPPER-MULTIPLICITY>'
        '<REFINED-MODULE-DEF-REF DEST="ECUC-MODULE-DEF">/AUTOSAR/EcucDefs/EcuM</REFINED-MODULE-DEF-REF>'
        '<CONTAINERS><ECUC-PARAM-CONF-CONTAINER-DEF><SHORT-NAME>EcuMConfiguration</SHORT-NAME>'
        '<SUB-CONTAINERS><ECUC-PARAM-CONF-CONTAINER-DEF><SHORT-NAME>EcuMCommonConfiguration</SHORT-NAME>')
TAIL = ('</ECUC-PARAM-CONF-CONTAINER-DEF></SUB-CONTAINERS></ECUC-PARAM-CONF-CONTAINER-DEF></CONTAINERS>'
        '</ECUC-MODULE-DEF></ELEMENTS></AR-PACKAGE></AR-PACKAGES></AUTOSAR>')

CORE = HEAD + ('<PARAMETERS><ECUC-BOOLEAN-PARAM-DEF><SHORT-NAME>EcuMCoreParam</SHORT-NAME>'
               '</ECUC-BOOLEAN-PARAM-DEF></PARAMETERS>') + TAIL
PART = HEAD + ('<SUB-CONTAINERS><ECUC-PARAM-CONF-CONTAINER-DEF><SHORT-NAME>EcuMWakeupSource</SHORT-NAME>'
               '<LOWER-MULTIPLICITY>1</LOWER-MULTIPLICITY><UPPER-MULTIPLICITY-INFINITE>true'
               '</UPPER-MULTIPLICITY-INFINITE><PARAMETERS><ECUC-INTEGER-PARAM-DEF><SHORT-NAME>EcuMWakeupSourceId'
               '</SHORT-NAME><MIN>0</MIN><MAX>31</MAX></ECUC-INTEGER-PARAM-DEF></PARAMETERS>'
               '</ECUC-PARAM-CONF-CONTAINER-DEF></SUB-CONTAINERS>') + TAIL


class SplitModuleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        with open(os.path.join(self.tmp, "EcuM_bswmd__core.arxml"), "w", encoding="utf-8") as fh:
            fh.write(CORE)
        with open(os.path.join(self.tmp, "EcuM_bswmd_Flex.arxml"), "w", encoding="utf-8") as fh:
            fh.write(PART)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_parts_are_merged(self):
        repo = DefinitionRepository(None, [self.tmp]).scan()
        self.assertEqual(len(repo.module_index["/MICROSAR/EcuM"]["files"]), 2)
        base = "/MICROSAR/EcuM/EcuMConfiguration/EcuMCommonConfiguration"
        self.assertIsNotNone(repo.find(base + "/EcuMCoreParam"))
        ws = repo.find(base + "/EcuMWakeupSource")
        self.assertIsNotNone(ws)
        self.assertEqual(ws.multiplicity_str(), "1..*")
        self.assertEqual(repo.find(base + "/EcuMWakeupSource/EcuMWakeupSourceId").max, 31)
        # the common container exists once and has both children
        common = repo.find(base)
        self.assertEqual(sorted(c.name for c in common.children), ["EcuMCoreParam", "EcuMWakeupSource"])
        self.assertEqual(len(repo.module("/MICROSAR/EcuM").files), 2)


if __name__ == "__main__":
    unittest.main()
