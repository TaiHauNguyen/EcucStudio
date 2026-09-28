"""Tests for the DVCfgCmd integration helpers and unit conversion (no DaVinci run needed)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ecucstudio import davinci, units  # noqa: E402
from ecucstudio.validation import Severity  # noqa: E402

REPORT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<DaVinciExecutionReport name="Generation Execution Report">
  <ValidationResults fatalerrors="0" errors="1" warnings="1" improvements="0" infos="0">
    <ValidationResultId id="90220" message="User config file was not found" origin="MEMMAP">
      <ValidationResult severity="Error" ondemandresult="true">
        <Description>UserConfigFile x could not be found</Description>
        <ErroneousCEs><CE><Object>/ActiveEcuC/MemMap/MemMapGeneral[0:MemMapPreUserConfigFile]</Object>
        <Definition>/MICROSAR/MemMap/MemMapGeneral/MemMapPreUserConfigFile</Definition></CE></ErroneousCEs>
      </ValidationResult>
    </ValidationResultId>
    <ValidationResultId id="02217" message="Incorrect setting" origin="COM">
      <ValidationResult severity="Warning" ondemandresult="false">
        <Description>Transmit confirmation not required by the Com module.</Description>
        <Acknowledgement>dbc generated</Acknowledgement>
      </ValidationResult>
    </ValidationResultId>
  </ValidationResults>
  <GenerationProcessResult result="SUCCESSFUL" lastexecutiontime="2026-09-28 00:54:04" lastexecutionduration="0.648">
    <GenerationResult generationtype="GENERATOR">
      <Name>Det</Name><State>SUCCESSFUL</State>
      <Phases><Phase type="GENERATION" state="SUCCESSFUL"><GeneratedFiles>
        <File generationinfo="FILE_IS_UP_TO_DATE">E:\\GenData\\Det_Cfg.c</File></GeneratedFiles></Phase></Phases>
      <Generator><Name>MICROSAR Det Generator</Name><Version>13.0.0.00</Version><MSN>Det</MSN>
      <Definition>/MICROSAR/Det</Definition></Generator>
    </GenerationResult>
  </GenerationProcessResult>
</DaVinciExecutionReport>"""


class DaVinciTests(unittest.TestCase):
    def test_parse_report(self):
        fd, path = tempfile.mkstemp(suffix=".xml")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(REPORT)
        try:
            rep = davinci.parse_report(path)
        finally:
            os.remove(path)
        self.assertEqual(rep.counts["errors"], 1)
        ids = {r.rule_id: r for r in rep.validation}
        self.assertEqual(set(ids), {"MEMMAP90220", "COM02217"})
        self.assertEqual(ids["MEMMAP90220"].severity, Severity.ERROR)
        self.assertEqual(ids["MEMMAP90220"].obj, "/ActiveEcuC/MemMap/MemMapGeneral[0:MemMapPreUserConfigFile]")
        self.assertEqual(ids["COM02217"].acknowledged, "dbc generated")
        self.assertEqual(ids["COM02217"].short_id, "COM2217")
        self.assertEqual(rep.process_result, "SUCCESSFUL")
        self.assertEqual(rep.generation[0].files[0].info, "FILE_IS_UP_TO_DATE")

    def test_report_without_validation_results(self):
        fd, path = tempfile.mkstemp(suffix=".xml")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write('<DaVinciExecutionReport><GenerationProcessResult result="ERROR"/></DaVinciExecutionReport>')
        try:
            rep = davinci.parse_report(path)
        finally:
            os.remove(path)
        self.assertEqual(rep.validation, [])
        self.assertEqual(rep.process_result, "ERROR")

    def test_generate_cmd(self):
        o = davinci.GenerateOptions(modules=["/MICROSAR/Det", "C:/Program Files/Git/MICROSAR/Com"],
                                    gen_type="REAL", swcs="")
        cmd = davinci.build_generate_cmd("DVCfgCmd.exe", r"E:\p\x.dpa", "r.xml", o)
        self.assertEqual(cmd[1:4], ["-p", r"E:\p\x.dpa", "-g"])
        self.assertIn("/MICROSAR/Det,/MICROSAR/Com", cmd)
        self.assertEqual(cmd[cmd.index("--genType") + 1], "REAL")
        self.assertEqual(cmd[cmd.index("--swcsToGenerate") + 1], "")
        self.assertIn("CreateXmlFile", cmd)

    def test_progress_line(self):
        self.assertEqual(davinci.parse_progress(" 84487 INFO  - Generation\tMODULE_STARTED\tGenerator: Det\t\tX")[:2],
                         ("Generation", "MODULE_STARTED"))
        self.assertIsNone(davinci.parse_progress(" 1 INFO  - Project closed"))

    def test_units(self):
        self.assertEqual(units.to_display("0.01", "SEC", "MSEC"), "10")
        self.assertEqual(units.to_stored("10", "SEC", "MSEC"), "0.01")
        self.assertEqual(units.to_display("abc", "SEC", "MSEC"), "abc")
        self.assertEqual(units.to_display("5", None, None), "5")
        self.assertEqual(units.to_stored("2", "BYTE", "KBYTE"), "2048")


if __name__ == "__main__":
    unittest.main()
