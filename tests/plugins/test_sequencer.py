import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _load import FIXTURES, repo_intel

repo_intel()
from repo_intel import pack, sequencer  # noqa: E402

RULES = pack.load(str(FIXTURES / "other-org.yaml"))


def pr(number, **kw):
    return {"number": number, **kw}


def tier_of(result, number):
    for t in result["tiers"]:
        for p in t["prs"]:
            if p["pr"] == number:
                return t["tier"], p
    raise AssertionError(f"#{number} is in no tier")


class HardEdges(unittest.TestCase):
    def seq(self, prs, **kw):
        return sequencer.sequence(prs, "main", rules=RULES, **kw)

    def test_independent_prs_share_one_tier(self):
        r = self.seq([pr(1, changed_files=["a.txt"]), pr(2, changed_files=["b.txt"])])
        self.assertEqual(len(r["tiers"]), 1)
        self.assertEqual(r["unresolved"], [])

    def test_a_stacked_pr_follows_the_pr_it_is_based_on(self):
        r = self.seq([pr(1, head_branch="feat-a", base_branch="main"), pr(2, head_branch="feat-b", base_branch="feat-a")])
        self.assertLess(tier_of(r, 1)[0], tier_of(r, 2)[0])
        self.assertIn("must follow #1 (stacked)", tier_of(r, 2)[1]["hard_constraints"])

    def test_an_explicit_reference_orders_the_prs(self):
        r = self.seq([pr(5, body="Depends on #6"), pr(6)])
        self.assertLess(tier_of(r, 6)[0], tier_of(r, 5)[0])

    def test_a_reference_to_another_repository_is_ignored(self):
        r = sequencer.sequence([pr(5, body="Blocked by other/repo#6"), pr(6)], "main", rules=RULES, repo="example-org/x")
        self.assertEqual(len(r["tiers"]), 1)

    def test_a_generated_input_lands_before_the_generated_output(self):
        r = self.seq([pr(10, changed_files=["gen/out.go"]), pr(11, changed_files=["schema/a.proto"])])
        self.assertLess(tier_of(r, 11)[0], tier_of(r, 10)[0])

    def test_a_dependency_bump_of_an_input_module_counts_as_an_input(self):
        r = self.seq([pr(10, changed_files=["gen/out.go"]), pr(12, dependency_bumps=["example.com/schema"])])
        self.assertLess(tier_of(r, 12)[0], tier_of(r, 10)[0])

    def test_a_pr_changing_input_and_output_precedes_an_output_only_pr(self):
        r = self.seq([pr(20, changed_files=["gen/x.go"]), pr(21, changed_files=["schema/a.proto", "gen/y.go"])])
        self.assertLess(tier_of(r, 21)[0], tier_of(r, 20)[0])

    def test_two_inputs_to_the_same_output_are_left_to_a_human(self):
        r = self.seq([pr(30, changed_files=["schema/a.proto"]), pr(31, changed_files=["schema/b.proto"]), pr(32, changed_files=["gen/z.go"])])
        self.assertEqual([u["prs"] for u in r["unresolved"]], [[30, 31]])

    def test_two_prs_closing_the_same_issue_are_unresolved_not_ordered(self):
        r = self.seq([pr(40, closing_issues=[9]), pr(41, closing_issues=[{"number": 9}])])
        self.assertEqual(r["unresolved"][0]["prs"], [40, 41])
        self.assertEqual(len(r["tiers"]), 1)

    def test_a_cycle_is_reported_and_left_out_of_the_tiers(self):
        r = self.seq([pr(50, body="Depends on #51"), pr(51, body="Depends on #50"), pr(52)])
        self.assertEqual(sorted(r["cycles"][0]), [50, 51])
        self.assertEqual([p["pr"] for t in r["tiers"] for p in t["prs"]], [52])


class Annotations(unittest.TestCase):
    def test_rebase_flag_marks_a_pr_that_shares_a_file_with_an_earlier_tier(self):
        r = sequencer.sequence([pr(1, changed_files=["a.txt"]), pr(2, changed_files=["a.txt"], body="Depends on #1")], "main", rules=RULES)
        self.assertFalse(tier_of(r, 1)[1]["rebase_flag"])
        self.assertTrue(tier_of(r, 2)[1]["rebase_flag"])

    def test_file_classification_is_the_highest_tier_among_the_files(self):
        r = sequencer.sequence([pr(1, changed_files=["README.md", "core/x.go", "x_test.go"])], "main", rules=RULES)
        self.assertEqual(tier_of(r, 1)[1]["file_classification"], "ADMISSION_CRITICAL")

    def test_gate_blocks_prs_on_a_branch_with_an_open_failure_issue(self):
        prs = [pr(1, base_branch="release-1"), pr(2, base_branch="main"), pr(3, base_branch="release-1", labels=["gate-bypass"])]
        r = sequencer.sequence(prs, "main", blocking_issue_branches=["release-1"], rules=RULES)
        self.assertTrue(tier_of(r, 1)[1]["gate_blocked"])
        self.assertFalse(tier_of(r, 2)[1]["gate_blocked"])
        self.assertFalse(tier_of(r, 3)[1]["gate_blocked"])      # the bypass label

    def test_an_issue_without_a_branch_blocks_every_branch(self):
        r = sequencer.sequence([pr(1, base_branch="main")], "main", blocking_issue_branches=[None], rules=RULES)
        self.assertTrue(tier_of(r, 1)[1]["gate_blocked"])

    def test_a_truncated_file_list_is_flagged(self):
        r = sequencer.sequence([pr(1, files_truncated=True)], "main", rules=RULES)
        self.assertTrue(any("files_truncated" in w for w in tier_of(r, 1)[1]["warnings"]))


class WithoutAPack(unittest.TestCase):
    def test_nothing_is_classified_and_nothing_is_gated(self):
        prs = [pr(1, changed_files=["gen/a.go", "core/x.go"], base_branch="main"), pr(2, changed_files=["schema/a.proto"])]
        r = sequencer.sequence(prs, "main", blocking_issue_branches=[None])
        self.assertEqual(len(r["tiers"]), 1)
        self.assertEqual(tier_of(r, 1)[1]["file_classification"], "STANDARD")
        self.assertFalse(tier_of(r, 1)[1]["gate_blocked"])


class ThroughTheTool(unittest.TestCase):
    def test_the_handler_returns_json_with_tiers(self):
        from repo_intel import sequence_tools
        out = json.loads(sequence_tools.handle_sequence_prs({"prs": [pr(1), pr(2, body="Requires #1")], "repo_default_branch": "main"}))
        self.assertTrue(out["success"])
        self.assertEqual(len(out["tiers"]), 2)

    def test_the_tool_schemas_name_no_organisation(self):
        from repo_intel import fetch_tools, pack_tools, sequence_tools
        schemas = [fetch_tools.FETCH_PR_CANDIDATES_SCHEMA, fetch_tools.FETCH_PR_DETAILS_SCHEMA, fetch_tools.FETCH_ISSUE_CANDIDATES_SCHEMA,
                   fetch_tools.FETCH_ISSUE_DETAILS_SCHEMA, fetch_tools.FETCH_FILE_DIFF_OVERLAP_SCHEMA, sequence_tools.SEQUENCE_PRS_SCHEMA,
                   pack_tools.REPO_PACK_SCHEMA]
        text = json.dumps(schemas).lower()
        for word in ("kyverno", "e2e", "coderabbit", "zz_generated"):
            self.assertNotIn(word, text)


if __name__ == "__main__":
    unittest.main()
