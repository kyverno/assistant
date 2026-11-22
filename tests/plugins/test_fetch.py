"""repo-intel's fetch and sequence_prs against a fake GitHub: gate state, competing PRs, the PR cache, the size cap."""
import io
import json
import pathlib
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _load import FIXTURES, repo_intel

repo_intel()
from repo_intel import fetch, pack, sequence_tools  # noqa: E402

RULES = pack.load(str(FIXTURES / "other-org.yaml"))
NO_PACK = pack.Rules()
REPO = "acme/widget"


def pr_node(number, base="main", body="", files=(), contexts=(), closing=(), more_closing=False):
    return {
        "number": number, "title": f"PR {number}", "url": f"https://github.com/{REPO}/pull/{number}", "body": body,
        "author": {"login": "someone"}, "authorAssociation": "CONTRIBUTOR", "createdAt": "2026-11-01T00:00:00Z",
        "baseRefName": base, "headRefName": f"b{number}", "additions": 1, "deletions": 1, "changedFiles": len(files),
        "milestone": None,
        "labels": {"pageInfo": {"hasNextPage": False}, "nodes": []},
        "files": {"pageInfo": {"hasNextPage": False}, "nodes": [{"path": f} for f in files]},
        "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []},
        "reviews": {"nodes": []}, "mergeStateStatus": "CLEAN", "bumpCommits": {"nodes": []},
        "commits": {"nodes": [{"commit": {"statusCheckRollup": {"contexts": {
            "pageInfo": {"hasNextPage": False}, "nodes": list(contexts)}}}}]},
        "closingIssuesReferences": {"pageInfo": {"hasNextPage": more_closing}, "nodes": list(closing)},
    }


def issue_ref(number, *others, more=False):
    return {"number": number, "state": "OPEN", "milestone": None, "labels": {"nodes": []},
            "closedByPullRequestsReferences": {"pageInfo": {"hasNextPage": more},
                                               "nodes": [{"number": n, "title": f"PR {n}", "state": s} for n, s in others]}}


class FakeGitHub:
    def __init__(self, prs, failure_issues=()):
        self.prs = {p["number"]: p for p in prs}
        self.failure_issues = list(failure_issues)
        self.calls = []

    def __call__(self, query, variables):
        self.calls.append(query)
        if "search(" in query:
            return {"search": {"issueCount": len(self.prs), "nodes": [{"number": n} for n in sorted(self.prs)]}}
        if "pullRequest(number" in query:
            return {"repository": {"pullRequest": self.prs.get(variables["number"])}}
        if "defaultBranchRef" in query:
            repo = {"defaultBranchRef": {"name": "main"}}
            if "issues(" in query:
                start = int(variables.get("after") or 0)
                page = self.failure_issues[start:start + 100]
                more = start + 100 < len(self.failure_issues)
                repo["issues"] = {"pageInfo": {"hasNextPage": more, "endCursor": str(start + 100) if more else None},
                                  "nodes": page}
            return {"repository": repo}
        raise AssertionError(f"unexpected query: {query[:80]}")


class Base(unittest.TestCase):
    rules = RULES

    def setUp(self):
        fetch._PRS.clear()
        fetch._REPO_STATE.clear()
        fetch._PACK_SHOWN.clear()
        patcher = mock.patch.object(fetch, "_rules", lambda repo=None: self.rules)
        patcher.start()
        self.addCleanup(patcher.stop)

    def github(self, *args, **kw):
        gh = FakeGitHub(*args, **kw)
        patcher = mock.patch.object(fetch, "_graphql", gh)
        patcher.start()
        self.addCleanup(patcher.stop)
        return gh


class Gate(Base):
    def test_the_fetch_reads_open_failure_issues_and_marks_blocked_prs(self):
        self.github([pr_node(1), pr_node(2, base="release-1.0")],
                    failure_issues=[{"number": 90, "title": "broken", "url": "u", "body": "<!-- broken:main -->"}])
        out = fetch.fetch_pr_candidates(REPO, "label:ready")
        self.assertEqual(out["gate"]["blocking_branches"], ["main"])
        self.assertFalse(out["gate"]["blocks_all_branches"])
        blocked = {p["number"]: p["gate_blocked"] for p in out["prs"]}
        self.assertEqual(blocked, {1: True, 2: False})

    def test_every_page_of_failure_issues_is_read(self):
        issues = [{"number": n, "title": "t", "url": "u", "body": "<!-- broken:release-1.0 -->"} for n in range(1, 151)]
        issues.append({"number": 999, "title": "t", "url": "u", "body": "<!-- broken:main -->"})
        self.github([pr_node(1)], failure_issues=issues)
        out = fetch.fetch_pr_candidates(REPO, "label:ready")
        self.assertEqual(out["gate"]["blocking_branches"], ["main", "release-1.0"])
        self.assertFalse(out["gate"]["open_failure_issues_truncated"])
        self.assertTrue(out["prs"][0]["gate_blocked"])

    def test_more_failure_issues_than_it_reads_blocks_every_branch(self):
        issues = [{"number": n, "title": "t", "url": "u", "body": "<!-- broken:release-1.0 -->"}
                  for n in range(1, fetch._GATE_PAGES_MAX * 100 + 2)]
        self.github([pr_node(1)], failure_issues=issues)
        out = fetch.fetch_pr_candidates(REPO, "label:ready")
        self.assertTrue(out["gate"]["open_failure_issues_truncated"])
        self.assertTrue(out["gate"]["blocks_all_branches"])
        self.assertTrue(out["prs"][0]["gate_blocked"])

    def test_a_failure_issue_without_a_branch_blocks_every_branch(self):
        self.github([pr_node(1, base="release-1.0")],
                    failure_issues=[{"number": 90, "title": "broken", "url": "u", "body": "no marker"}])
        out = fetch.fetch_pr_candidates(REPO, "label:ready")
        self.assertTrue(out["gate"]["blocks_all_branches"])
        self.assertTrue(out["prs"][0]["gate_blocked"])

    def test_the_gate_check_state_comes_from_the_status_contexts(self):
        contexts = [{"__typename": "StatusContext", "context": "Release Gate", "state": "FAILURE"},
                    {"__typename": "CheckRun", "name": "unit", "status": "COMPLETED", "conclusion": "SUCCESS"}]
        self.github([pr_node(1, contexts=contexts), pr_node(2)])
        prs = {p["number"]: p for p in fetch.fetch_pr_candidates(REPO, "label:ready")["prs"]}
        self.assertEqual(prs[1]["gate_checks"], {"release-gate": "FAILURE"})
        self.assertEqual(prs[1]["ci_state"], "SUCCESS")
        self.assertEqual(prs[2]["gate_checks"], {"release-gate": "MISSING"})

    def test_no_gate_in_the_pack_means_no_gate_fields(self):
        self.rules = NO_PACK
        gh = self.github([pr_node(1)])
        out = fetch.fetch_pr_candidates(REPO, "label:ready")
        self.assertNotIn("gate", out)
        self.assertNotIn("gate_blocked", out["prs"][0])
        self.assertFalse(any("issues(" in q for q in gh.calls))


class CompetingPrs(Base):
    def test_other_prs_closing_the_same_issue_come_with_every_pr(self):
        self.github([pr_node(1, closing=[issue_ref(50, (1, "OPEN"), (2, "OPEN"), (3, "MERGED"))])])
        issue = fetch.fetch_pr_candidates(REPO, "label:ready")["prs"][0]["closing_issues"][0]
        self.assertEqual([(o["number"], o["state"]) for o in issue["other_prs"]], [(2, "OPEN"), (3, "MERGED")])


class PackBrief(Base):
    def test_the_pack_brief_comes_once_per_repository(self):
        self.github([pr_node(1)])
        self.assertEqual(fetch.fetch_pr_candidates(REPO, "label:ready")["pack"]["branch_gates"], ["release-gate"])
        self.assertNotIn("pack", fetch.fetch_pr_candidates(REPO, "label:ready"))


class SequenceByNumber(Base):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(pack, "load", lambda *a, **kw: self.rules)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_tool(self, numbers):
        return json.loads(sequence_tools.handle_sequence_prs({"pr_numbers": numbers, "repo": REPO}))

    def test_sequence_prs_reads_the_fetched_records(self):
        gh = self.github([pr_node(1), pr_node(2, body="Requires #1")])
        fetch.fetch_pr_candidates(REPO, "label:ready")
        before = len(gh.calls)
        out = self.run_tool([1, 2])
        self.assertTrue(out["success"])
        self.assertEqual([[p["pr"] for p in t["prs"]] for t in out["tiers"]], [[1], [2]])
        self.assertFalse(any("pullRequest(number" in q for q in gh.calls[before:]))

    def test_sequence_prs_fetches_numbers_it_has_not_seen(self):
        gh = self.github([pr_node(7, files=["a.go"]), pr_node(8, files=["a.go"])])
        out = self.run_tool([7, 8])
        self.assertTrue(out["success"])
        self.assertEqual(out["file_overlaps"][0]["prs"], [7, 8])
        self.assertEqual(sum("pullRequest(number" in q for q in gh.calls), 2)

    def test_sequence_prs_applies_the_gate_itself(self):
        self.github([pr_node(1)], failure_issues=[{"number": 90, "title": "t", "url": "u", "body": "<!-- broken:main -->"}])
        out = self.run_tool([1])
        self.assertTrue(out["tiers"][0]["prs"][0]["gate_blocked"])
        self.assertEqual(out["gate_status"]["open_failure_issues"][0]["number"], 90)


    def test_a_pr_that_cannot_be_read_is_named_not_dropped(self):
        self.github([pr_node(1)])
        out = self.run_tool([1, 404])
        self.assertTrue(out["success"])
        self.assertEqual([p["pr"] for t in out["tiers"] for p in t["prs"]], [1])
        self.assertEqual(out["not_sequenced"][0]["pr"], 404)
        self.assertIn("not found", out["not_sequenced"][0]["why"])

    def test_cut_off_closing_issues_and_competitors_are_reported(self):
        self.github([pr_node(1, closing=[issue_ref(5, (9, "OPEN"), more=True)]), pr_node(2, more_closing=True)])
        out = self.run_tool([1, 2])
        self.assertEqual(len(out["incomplete"]), 2)
        self.assertTrue(out["incomplete"][0].startswith("#1:"))
        self.assertIn("closing issues", out["incomplete"][1])


class SizeCap(Base):
    def test_a_result_over_the_cap_drops_bodies_then_trailing_prs(self):
        self.github([pr_node(n, body="x" * 3000, files=[f"d{n}/f{i}.go" for i in range(30)]) for n in range(1, 41)])
        with mock.patch.object(fetch, "RESULT_CHAR_CAP", 20_000):
            out = fetch.fetch_pr_candidates(REPO, "label:ready", limit=40)
        self.assertLessEqual(len(json.dumps(out)), 20_000)
        self.assertIn("trimmed", out)
        self.assertNotIn("body", out["prs"][0])
        self.assertEqual(len(out["prs"]) + len(out["not_shown"]), 40)

    def test_a_digest_lists_directories_for_a_large_pr(self):
        self.github([pr_node(1, files=[f"pkg/a/f{i}.go" for i in range(12)] + ["pkg/b/x.go"])])
        p = fetch.fetch_pr_candidates(REPO, "label:ready", detail="digest")["prs"][0]
        self.assertNotIn("changed_files", p)
        self.assertEqual(p["changed_dirs"], ["pkg/a", "pkg/b"])
        self.assertEqual(len(fetch.cached_prs(REPO, [1])[0][0]["changed_files"]), 13)


class Network(unittest.TestCase):
    def http_error(self, code, body=b"", headers=None):
        return urllib.error.HTTPError("u", code, "x", headers or {}, io.BytesIO(body))

    def test_a_gateway_error_is_retried_once(self):
        ok = mock.MagicMock()
        ok.__enter__.return_value.read.return_value = b'{"data": {}}'
        with mock.patch("urllib.request.urlopen", side_effect=[self.http_error(502), ok]) as op:
            self.assertEqual(fetch._open(mock.Mock(), "GraphQL"), {"data": {}})
        self.assertEqual(op.call_count, 2)

    def test_a_rate_limit_says_when_to_try_again(self):
        err = self.http_error(403, b"API rate limit exceeded", {"retry-after": "30"})
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaisesRegex(fetch.FetchError, "about 30 seconds"):
                fetch._open(mock.Mock(), "GraphQL")


if __name__ == "__main__":
    unittest.main()
