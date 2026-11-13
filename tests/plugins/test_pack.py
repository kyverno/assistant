import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _load import FIXTURES, ROOT, repo_intel

repo_intel()
from repo_intel import pack  # noqa: E402

KYVERNO = ROOT / "container/org/kyverno/org.yaml"


class Globs(unittest.TestCase):
    def test_double_star_crosses_directories_and_single_does_not(self):
        self.assertTrue(pack.glob_match("api/**", "api/a/b/c.go"))
        self.assertTrue(pack.glob_match("pkg/*.go", "pkg/x.go"))
        self.assertFalse(pack.glob_match("pkg/*.go", "pkg/sub/x.go"))

    def test_double_star_slash_matches_zero_directories(self):
        self.assertTrue(pack.glob_match("config/crds/**/*.yaml", "config/crds/a.yaml"))
        self.assertTrue(pack.glob_match("config/crds/**/*.yaml", "config/crds/v1/a.yaml"))

    def test_a_pattern_without_a_slash_matches_the_file_name_at_any_depth(self):
        self.assertTrue(pack.glob_match("*_test.go", "a/b/x_test.go"))
        self.assertTrue(pack.glob_match("zz_generated.deepcopy.go", "api/v1/zz_generated.deepcopy.go"))
        self.assertFalse(pack.glob_match("x_test.go", "a/x_test.go.bak"))


class Parse(unittest.TestCase):
    def test_empty_and_missing_packs_have_no_rules(self):
        self.assertEqual(pack.parse(None).gates, ())
        self.assertEqual(pack.parse({}).classify("anything.go"), "STANDARD")

    def test_unknown_top_level_key_is_an_error(self):
        with self.assertRaises(pack.PackError):
            pack.parse({"maintaner": {}})

    def test_unknown_key_inside_a_section_is_an_error(self):
        with self.assertRaises(pack.PackError):
            pack.parse({"maintainer": {"file_classes": {"genrated": ["x"]}}})

    def test_a_gate_needs_its_required_fields(self):
        with self.assertRaises(pack.PackError):
            pack.parse({"maintainer": {"branch_gates": [{"name": "g"}]}})

    def test_branch_marker_needs_a_branch_group(self):
        gate = {"name": "g", "failure_issue_label": "f", "bypass_label": "b", "branch_marker": "no group"}
        with self.assertRaises(pack.PackError):
            pack.parse({"maintainer": {"branch_gates": [gate]}})

    def test_a_pack_that_is_not_a_mapping_is_an_error(self):
        with self.assertRaises(pack.PackError):
            pack.parse(["a list"])


class OtherOrganisation(unittest.TestCase):
    def setUp(self):
        self.rules = pack.load(str(FIXTURES / "other-org.yaml"))

    def test_classification_follows_the_tiers(self):
        c = self.rules.classify
        self.assertEqual(c("gen/a/b.go"), "GENERATED")
        self.assertEqual(c("x/y.pb.go"), "GENERATED")
        self.assertEqual(c("gen/keep.go"), "STANDARD")        # an exception to the generated glob
        self.assertEqual(c("api/v1/t.go"), "API_SURFACE")
        self.assertEqual(c("core/engine.go"), "ADMISSION_CRITICAL")
        self.assertEqual(c("core/engine_test.go"), "ADMISSION_CRITICAL")   # a higher tier wins over test-only
        self.assertEqual(c("misc/other_test.go"), "TEST_ONLY")
        self.assertEqual(c("README.md"), "STANDARD")

    def test_gate_and_inputs_come_from_the_pack(self):
        self.assertEqual(self.rules.gate.name, "release-gate")
        self.assertEqual(self.rules.gate.bypass_label, "gate-bypass")
        m = self.rules.gate.branch_marker.search("<!-- broken:release-1.2 -->")
        self.assertEqual(m.group("branch"), "release-1.2")
        self.assertTrue(self.rules.is_generated_input_path("schema/a/b.proto"))
        self.assertEqual(self.rules.approval_bots, ("review-bot",))

    def test_no_kyverno_literal_leaks_into_the_summary(self):
        self.assertNotIn("kyverno", repr(self.rules.summary()).lower())


class KyvernoPack(unittest.TestCase):
    def setUp(self):
        self.rules = pack.load(str(KYVERNO))

    def test_the_shipped_pack_parses(self):
        self.assertEqual(self.rules.gate.name, "e2e-gate")

    def test_known_paths(self):
        c = self.rules.classify
        self.assertEqual(c("api/kyverno/v1/zz_generated.deepcopy.go"), "GENERATED")
        self.assertEqual(c("config/crds/kyverno/a.yaml"), "GENERATED")
        self.assertEqual(c("charts/kyverno/README.md"), "GENERATED")
        self.assertEqual(c("pkg/clients/dclient/client.go"), "STANDARD")          # the generated exception
        self.assertEqual(c("pkg/clients/x/y.generated.go"), "GENERATED")
        self.assertEqual(c("api/kyverno/v1/policy_types.go"), "API_SURFACE")
        self.assertEqual(c("pkg/engine/x.go"), "ADMISSION_CRITICAL")
        self.assertEqual(c("pkg/utils/x_test.go"), "TEST_ONLY")
        self.assertEqual(c("docs/guide.md"), "STANDARD")
        self.assertTrue(self.rules.is_generated_input_path("api/kyverno/v1/policy_types.go"))


class Loading(unittest.TestCase):
    def test_no_pack_at_all_is_empty_rules(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(pack.load(str(pathlib.Path(d) / "missing.yaml")).gates, ())

    def test_a_broken_pack_names_the_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "org.yaml"
            p.write_text("maintainer:\n  nope: 1\n")
            with self.assertRaises(pack.PackError) as ctx:
                pack.load(str(p))
            self.assertIn(str(p), str(ctx.exception))

    def test_facts_next_to_the_pack_are_read(self):
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "org.yaml").write_text("name: x\n")
            (pathlib.Path(d) / "facts.md").write_text("The release branch is called trunk.")
            self.assertIn("trunk", pack.load(str(pathlib.Path(d) / "org.yaml")).facts)

    def test_the_environment_variable_adds_a_pack_to_the_search(self):
        old = os.environ.get("ASSISTANT_ORG_FILE")
        os.environ["ASSISTANT_ORG_FILE"] = str(FIXTURES / "other-org.yaml")
        try:
            self.assertIn(FIXTURES / "other-org.yaml", pack.candidate_paths())
        finally:
            os.environ.pop("ASSISTANT_ORG_FILE", None) if old is None else os.environ.__setitem__("ASSISTANT_ORG_FILE", old)


if __name__ == "__main__":
    unittest.main()
