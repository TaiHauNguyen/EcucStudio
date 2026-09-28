"""Core tests against a real DaVinci project (its ECUC files are copied to a temp folder).

Run:  set ECUCSTUDIO_TEST_DPA=<path to the project .dpa>
      python -m unittest discover -s tests -v      (from the EcucStudio folder)
The tests use the Det and Com module configurations of that project.
"""
import glob
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from ecucstudio import arxml  # noqa: E402
from ecucstudio.bswmd import DefinitionRepository  # noqa: E402
from ecucstudio.project import EcucModel, raw_value, read_dpa, value_elements, parse_object_ref  # noqa: E402
from ecucstudio.validation import ValidationContext, default_validator  # noqa: E402

DPA = os.environ.get("ECUCSTUDIO_TEST_DPA", "")
CACHE = os.path.join(tempfile.gettempdir(), "ecucstudio_test_cache")


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


@unittest.skipUnless(DPA and os.path.exists(DPA), "set ECUCSTUDIO_TEST_DPA to a DaVinci project .dpa")
class CoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project = read_dpa(DPA)
        cls.defs = DefinitionRepository(cls.project.sip_dir, cls.project.add_bswmds, cache_dir=CACHE).scan()

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ecucstudio_")
        src = os.path.dirname(self.project.active_ecuc)
        for f in glob.glob(os.path.join(src, "*.arxml")):
            if ".Initial." not in f:
                shutil.copy2(f, self.tmp)
        self.files = sorted(glob.glob(os.path.join(self.tmp, "*.arxml")))
        self.model = EcucModel().load(self.files)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---------------------------------------------------------------- helpers
    def validate(self, *modules):
        ctx = ValidationContext(self.model, self.defs, None, {"modules": list(modules)})
        return default_validator().run(ctx)

    def ids(self, results):
        return sorted({r.rule_id for r in results if r.rule_id.startswith(("AR-ECUC", "Cfg"))})

    def cont(self, path):
        el = self.model.resolve(path)
        self.assertIsNotNone(el, path)
        return el

    def pdef(self, path):
        d = self.defs.find(path)
        self.assertIsNotNone(d, path)
        return d

    # ------------------------------------------------------------------ tests
    def test_roundtrip_identical(self):
        for xf in self.model.files.values():
            with open(xf.path, "rb") as fh:
                self.assertEqual(fh.read(), xf.to_bytes(), xf.path)

    def test_baseline_det_clean(self):
        self.assertEqual(self.ids(self.validate("Det")), [])

    def test_integer_range_and_fix(self):
        c = self.cont("/ActiveEcuC/Det/DetGeneral")
        d = self.pdef("/MICROSAR/Det/DetGeneral/DetGlobalFilterSize")
        self.model.set_value(c, d, str(int(d.max) + 1))
        res = [r for r in self.validate("Det") if r.rule_id == "AR-ECUC02027"]
        self.assertEqual(len(res), 1)
        self.assertIn("is not in range", res[0].message)
        res[0].preferred_action.apply()
        self.assertEqual(self.ids(self.validate("Det")), [])

    def test_wrong_type_and_enum(self):
        c = self.cont("/ActiveEcuC/Det/DetGeneral")
        self.model.set_value(c, self.pdef("/MICROSAR/Det/DetGeneral/DetEnableDet"), "maybe")
        self.assertIn("Cfg00021", self.ids(self.validate("Det")))
        cn = self.cont("/ActiveEcuC/Com/ComConfig")
        sig = next(e for e in self.model.containers_of_def("/MICROSAR/Com/ComConfig/ComSignal"))
        self.model.set_value(sig, self.pdef("/MICROSAR/Com/ComConfig/ComSignal/ComSignalEndianness"), "MIDDLE")
        res = [r for r in self.validate("Com") if r.rule_id == "AR-ECUC03005"]
        self.assertEqual(len(res), 1, cn)
        res[0].preferred_action.apply()
        self.assertNotIn("AR-ECUC03005", self.ids(self.validate("Com")))

    def test_missing_mandatory_parameter(self):
        c = self.cont("/ActiveEcuC/Det/DetGeneral")
        v = self.model.find_values(c, "/MICROSAR/Det/DetGeneral/DetEnableDet")[0]
        self.model.delete_element(v)
        res = [r for r in self.validate("Det") if r.rule_id == "AR-ECUC02008"]
        self.assertEqual(len(res), 1)
        self.assertIn("Mandatory parameter DetEnableDet is missing", res[0].message)
        res[0].preferred_action.apply()
        self.assertEqual(self.ids(self.validate("Det")), [])

    def test_reference_errors(self):
        sig_ref = "/MICROSAR/Com/ComConfig/ComIPdu/ComIPduSignalRef"
        ipdu = next(e for e in self.model.containers_of_def("/MICROSAR/Com/ComConfig/ComIPdu")
                    if self.model.find_values(e, sig_ref))
        d = self.pdef(sig_ref)
        self.model.set_value(ipdu, d, "/ActiveEcuC/Com/DoesNotExist")
        self.assertIn("Cfg00024", self.ids(self.validate("Com")))
        self.model.set_value(ipdu, d, "/ActiveEcuC/Com/ComGeneral")      # wrong target definition
        self.assertIn("AR-ECUC02039", self.ids(self.validate("Com")))

    def test_undo_redo(self):
        c = self.cont("/ActiveEcuC/Det/DetGeneral")
        d = self.pdef("/MICROSAR/Det/DetGeneral/DetEnableDet")
        v = self.model.find_values(c, d.path)[0]
        before = raw_value(v)
        self.model.set_value(c, d, "true")
        self.assertEqual(raw_value(v), "true")
        self.model.undo()
        self.assertEqual(raw_value(v), before)
        self.model.redo()
        self.assertEqual(raw_value(v), "true")

    def test_add_container_save_reload(self):
        com = self.cont("/ActiveEcuC/Com/ComConfig")
        cdef = self.pdef("/MICROSAR/Com/ComConfig/ComIPduGroup")
        el = self.model.add_container(com, cdef, "EcucStudio_TestGroup")
        path = "/ActiveEcuC/Com/ComConfig/EcucStudio_TestGroup"
        self.assertIs(self.model.resolve(path), el)
        untouched = {p: _read(p) for p in self.files if "Com_Com" not in p}
        saved = self.model.save(backup=False)
        self.assertEqual(len(saved), 1)
        for p, data in untouched.items():
            self.assertEqual(_read(p), data, p)
        m2 = EcucModel().load(self.files)
        self.assertIsNotNone(m2.resolve(path))
        new = m2.resolve(path)
        self.assertTrue(new.get("UUID"))
        # mandatory parameters with defaults were created
        names = {v.find(arxml.q("DEFINITION-REF")).text.rsplit("/", 1)[-1] for v in value_elements(new)}
        mandatory = {p.name for p in cdef.params() if p.lower >= 1 and p.default is not None and not p.is_ref}
        self.assertTrue(mandatory <= names, (mandatory, names))
        # the text stays pretty printed
        data = _read(saved[0])
        self.assertIn(b"<SHORT-NAME>EcucStudio_TestGroup</SHORT-NAME>", data)
        self.assertNotIn(b"><ECUC-CONTAINER-VALUE", data)

    def test_rename_updates_references(self):
        target = "/MICROSAR/Com/ComConfig/ComSignal"
        refs = self.model.ref_index()
        sig = next(e for e in self.model.containers_of_def(target)
                   if refs.get(self.model.path_of(e)))
        old = self.model.path_of(sig)
        n = len(refs[old])
        self.model.rename_container(sig, "EcucStudio_Renamed")
        new = old.rsplit("/", 1)[0] + "/EcucStudio_Renamed"
        self.assertEqual(len(self.model.references_to(new)), n)
        self.assertEqual(self.model.references_to(old), [])
        self.model.undo()
        self.assertEqual(len(self.model.references_to(old)), n)

    def test_delete_container_undo(self):
        grp = next(iter(self.model.containers_of_def("/MICROSAR/Com/ComConfig/ComIPduGroup")))
        path = self.model.path_of(grp)
        self.model.delete_element(grp)
        self.assertIsNone(self.model.resolve(path))
        self.model.undo()
        self.assertIsNotNone(self.model.resolve(path))

    def test_parse_object_ref(self):
        self.assertEqual(parse_object_ref("/ActiveEcuC/Com/ComGeneral[0:ComSupportedIPduGroups](value=14)"),
                         ("/ActiveEcuC/Com/ComGeneral", "ComSupportedIPduGroups", 0))
        self.assertEqual(parse_object_ref("/ActiveEcuC/EthSM"), ("/ActiveEcuC/EthSM", None, None))


if __name__ == "__main__":
    unittest.main()
