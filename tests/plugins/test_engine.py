"""The engine (repo-intel/engine/) and the queue and item tools over a fake GitHub: ranking rules, the change profile,
CODEOWNERS parity with the contributor plugin, and each layer's character budget."""
import base64
import json
import os
import pathlib
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _load import FIXTURES, contributor, repo_intel
from test_fetch import REPO, Base, FakeGitHub, issue_ref, pr_node

repo_intel()
contributor()
from repo_intel import fetch, pack, queue_tools  # noqa: E402
from repo_intel.engine import layers, owners, profile, rank  # noqa: E402
from contributor import rules as contributor_rules  # noqa: E402

RULES = pack.load(str(FIXTURES / "other-org.yaml"))
URGENT = pack.parse({"maintainer": {"milestones": {"issue_urgency_labels": ["release-critical", "release-high",
                                                                              "release-low"]}}})
NOW = datetime(2026, 11, 17, tzinfo=timezone.utc)


def iso(days):
    return (NOW + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def pr(number, **kw):
    base = {"number": number, "title": f"PR {number}", "created_at": iso(-10), "updated_at": iso(-1),
            "ci_state": "SUCCESS", "merge_state": "CLEAN", "labels": [], "closing_issues": [], "files": []}
    return {**base, **kw}


def issue(number, labels=(), **kw):
    return {"number": number, "state": "OPEN", "labels": list(labels), "other_prs": [], **kw}


def order(result):
    return [e["number"] for e in result["ranked"]]


class Severity(unittest.TestCase):
    def test_closing_issue_labels_win_over_the_prs_own(self):
        s = rank.pr_severity(pr(1, labels=["enhancement"], closing_issues=[issue(9, ["security"])]), RULES)
        self.assertEqual((s["name"], s["inferred"]), ("critical", False))
        self.assertIn("#9", s["why"])

    def test_wording_only_when_no_label_says_anything(self):
        s = rank.pr_severity(pr(1, title="fix panic in loader"), RULES)
        self.assertEqual((s["name"], s["inferred"]), ("critical", True))
        s = rank.pr_severity(pr(1, title="fix panic in loader", labels=["enhancement"]), RULES)
        self.assertEqual(s["name"], "low")

    def test_pack_mapping_replaces_a_tier(self):
        rules = pack.parse({"maintainer": {"severity_labels": {"critical": ["sev/1"]}}})
        self.assertEqual(rank.pr_severity(pr(1, labels=["sev/1"]), rules)["name"], "critical")
        self.assertEqual(rank.pr_severity(pr(1, labels=["security"]), rules)["name"], "unrated")
        self.assertEqual(rank.pr_severity(pr(1, labels=["regression"]), rules)["name"], "high")

    def test_unknown_severity_tier_is_an_error(self):
        with self.assertRaises(pack.PackError):
            pack.parse({"maintainer": {"severity_labels": {"urgent": ["x"]}}})


class Ranking(unittest.TestCase):
    def rank(self, prs, rules=RULES, **kw):
        return rank.rank_prs(prs, rules, now=NOW, **kw)

    def test_severity_beats_a_nearer_milestone(self):
        prs = [pr(1, closing_issues=[issue(10, ["regression"])], milestone="1.0", milestone_due_on=iso(2)),
               pr(2, closing_issues=[issue(11, ["security"])])]
        self.assertEqual(order(self.rank(prs)), [2, 1])

    def test_release_label_and_due_date_make_it_urgent(self):
        prs = [pr(1, closing_issues=[issue(10, ["bug"])]),
               pr(2, closing_issues=[issue(11, ["bug", "release-high"])]),
               pr(3, closing_issues=[issue(12, ["bug"])], milestone="1.0", milestone_due_on=iso(5))]
        result = self.rank(prs, URGENT)
        self.assertEqual(order(result), [2, 3, 1])
        self.assertIn("due in", rank.explain(result["ranked"][1]))

    def test_a_blocker_never_raises_priority_but_actionable_breaks_ties(self):
        prs = [pr(1, ci_state="FAILURE", closing_issues=[issue(10, ["bug"])], created_at=iso(-30)),
               pr(2, closing_issues=[issue(11, ["bug"])]),
               pr(3, ci_state="FAILURE", closing_issues=[issue(12, ["security"])])]
        result = self.rank(prs)
        self.assertEqual(order(result), [3, 2, 1])
        self.assertEqual(result["ranked"][0]["readiness"]["who"], "author")

    def test_older_then_smaller(self):
        prs = [pr(1, created_at=iso(-2)), pr(2, created_at=iso(-20)),
               pr(3, created_at=iso(-20), files=[{"path": "a.go", "additions": 500, "deletions": 0}])]
        self.assertEqual(order(self.rank(prs)), [2, 3, 1])

    def test_same_input_same_order(self):
        prs = [pr(n, created_at=iso(-5)) for n in (5, 3, 9, 1)]
        self.assertEqual(order(self.rank(prs)), order(self.rank(list(reversed(prs)))))
        self.assertEqual(order(self.rank(prs)), [1, 3, 5, 9])

    def test_prefer_goes_first_and_avoid_is_left_out(self):
        prs = [pr(1, closing_issues=[issue(10, ["security"])]),
               pr(2, files=[{"path": "core/engine/run.go", "additions": 3, "deletions": 1}]),
               pr(3, author="noisy")]
        result = self.rank(prs, prefer=["core/engine"], avoid=["@noisy"])
        self.assertEqual(order(result), [2, 1])
        self.assertEqual(result["avoided"], [{"number": 3, "why": "you avoid @noisy"}])
        self.assertIn("you prefer core/engine", rank.explain(result["ranked"][0]))

    def test_a_pr_that_must_merge_first_inherits_priority_and_comes_first(self):
        prs = [pr(1), pr(2, closing_issues=[issue(10, ["security"])])]
        result = self.rank(prs, edges=[(1, 2, "stacked")])
        self.assertEqual(order(result), [1, 2])
        self.assertEqual(result["ranked"][0]["inherits"], 2)

    def test_close_candidates_are_a_separate_list(self):
        prs = [pr(1, closing_issues=[issue(10, other_prs=[{"number": 7, "state": "MERGED"}])]),
               pr(2, closing_issues=[issue(11, state="CLOSED")]),
               pr(3, labels=["stale"], updated_at=iso(-45)),
               pr(4)]
        result = self.rank(prs)
        self.assertEqual(order(result), [4])
        self.assertEqual([c["number"] for c in result["close_candidates"]], [1, 2, 3])
        self.assertIn("#7 already merged", result["close_candidates"][0]["why"])


class Readiness(unittest.TestCase):
    def r(self, **kw):
        return rank.pr_readiness(pr(1, **kw), RULES, "me")

    def test_next_step_and_who(self):
        self.assertEqual(self.r()["next"], "review")
        self.assertEqual(self.r(review_decision="APPROVED")["next"], "ready to merge")
        self.assertEqual(self.r(review_decision="APPROVED", gate_blocked=True)["who"], "gate")
        self.assertEqual(self.r(merge_state="DIRTY")["who"], "author")
        self.assertEqual(self.r(ci_state="PENDING")["who"], "ci")
        self.assertEqual(self.r(labels=["workflow-approval-required"])["next"], "approve the workflow run")
        self.assertFalse(self.r(author="me")["actionable"])

    def test_a_failing_infrastructure_check_is_not_the_authors(self):
        rules = pack.parse({"readiness": {"infra_checks": ["Codecov"]}})
        r = rank.pr_readiness(pr(1, ci_state="FAILURE", failing_checks=["Codecov"]), rules, "me")
        self.assertEqual(r["next"], "review")
        self.assertIn("infrastructure check failing (Codecov), not the author's to fix", r["blockers"])
        r = rank.pr_readiness(pr(1, ci_state="FAILURE", failing_checks=["Codecov", "unit"]), rules, "me")
        self.assertEqual(r["next"], "waiting on author")
        self.assertIn("CI failing (unit)", r["blockers"])

    def test_the_gate_blocks_merging_not_reviewing(self):
        r = self.r(gate_blocked=True)
        self.assertEqual(r["next"], "review")
        self.assertIn("merging blocked by the gate", r["blockers"])


class Issues(unittest.TestCase):
    def test_lifecycle_labels_and_linked_prs(self):
        state = lambda **kw: rank.issue_state({"number": 1, "labels": [], **kw}, "me", NOW)
        self.assertEqual(state(labels=["needs-info"])["who"], "reporter")
        self.assertTrue(state(labels=["possibly-resolved"])["close"])
        self.assertIn("competing", state(closing_prs=[{"number": 2, "state": "OPEN"}, {"number": 3, "state": "OPEN"}])["next"])
        self.assertEqual(state()["next"], "needs an owner")
        self.assertEqual(state(assignees=["me"])["next"], "yours, no PR yet")

    def test_ranked_by_severity_then_reactions(self):
        issues = [{"number": 1, "labels": ["bug"], "reactions": 0, "created_at": iso(-3)},
                  {"number": 2, "labels": ["bug"], "reactions": 12, "created_at": iso(-1)},
                  {"number": 3, "labels": ["security"], "created_at": iso(-1)}]
        self.assertEqual(order(rank.rank_issues(issues, RULES, now=NOW)), [3, 2, 1])


GO_PATCH = """@@ -10,6 +10,9 @@ func (e *Engine) Validate(ctx context.Context) error {
 	x := 1
+	y := 2
@@ -40,3 +43,8 @@ import (
+func newHelper[T any](v T) T {
+	return v
+}
-func Removed() {}
"""


class Profile(unittest.TestCase):
    def test_functions_from_hunk_headers_and_changed_lines(self):
        self.assertEqual(profile.functions_in_patch(GO_PATCH), ["Engine.Validate", "newHelper", "Removed"])
        self.assertEqual(profile.functions_in_patch("@@ -1 +1 @@ def load(path):\n+    pass\n"), ["load"])
        self.assertEqual(profile.functions_in_patch("@@ -1 +1 @@\n+just text\n"), [])

    def test_profile_from_line_counts(self):
        rec = pr(1, files=[{"path": "core/run.go", "additions": 80, "deletions": 4},
                           {"path": "api/v1/types.go", "additions": 10, "deletions": 0},
                           {"path": "schema/a/b.proto", "additions": 2, "deletions": 0},
                           {"path": "README.md", "additions": 1, "deletions": 0}])
        p = profile.change_profile(rec, RULES)
        self.assertEqual(p["lines"], "+93/-4")
        self.assertEqual(p["by_class"]["CRITICAL"], {"files": 1, "lines": 84})
        self.assertTrue(p["api_changed"] and p["code_without_tests"] and p["docs"])
        self.assertEqual(p["generated"], "inputs changed, generated output not updated")
        self.assertIn("no tests", p["summary"])
        self.assertEqual(rank.pr_risk(p)[:2], ["10 lines in API_SURFACE code", "84 lines in CRITICAL code"])

    def test_tests_and_generated_in_sync(self):
        rec = pr(1, files=[{"path": "schema/a.proto", "additions": 1, "deletions": 0},
                           {"path": "gen/a.pb.go", "additions": 50, "deletions": 0},
                           {"path": "core/run_test.go", "additions": 9, "deletions": 0}])
        p = profile.change_profile(rec, RULES)
        self.assertEqual((p["tests"], p["generated"]), ("added or changed", "in sync"))

    def test_same_function_overlap(self):
        a = profile.change_profile(pr(1, files=[{"path": "core/e.go", "additions": 3, "deletions": 0}]), RULES,
                                   {"core/e.go": GO_PATCH})
        b = profile.change_profile(pr(2, files=[{"path": "core/e.go", "additions": 1, "deletions": 0}]), RULES,
                                   {"core/e.go": "@@ -10 +10 @@ func (e *Engine) Validate() error {\n+x\n"})
        c = profile.change_profile(pr(3, files=[{"path": "core/f.go", "additions": 1, "deletions": 0}]), RULES,
                                   {"core/f.go": "@@ -10 +10 @@ func (e *Engine) Validate() error {\n+x\n"})
        self.assertEqual(profile.same_function_overlaps({1: a, 2: b, 3: c}),
                         [{"prs": [1, 2], "functions": ["Engine.Validate in core/e.go"]}])


CODEOWNERS = """# owners
*                @org/maintainers
/core/           @alice @org/core
docs/            @bob
*.md             @writer
/api/**/types.go @carol
"""


class Owners(unittest.TestCase):
    def test_same_owners_as_the_contributor_parser(self):
        mine, theirs = owners.parse_codeowners(CODEOWNERS), contributor_rules.parse_codeowners(CODEOWNERS)
        self.assertEqual(mine, theirs)
        for path in ("core/x/y.go", "README.md", "docs/a/b.txt", "api/v1/types.go", "x/docs/a.go", "other.go"):
            self.assertEqual(owners.owners_for_path(mine, path), contributor_rules.owners_for_path(theirs, path), path)

    def test_owners_of_and_owns(self):
        counts = owners.owners_of(owners.parse_codeowners(CODEOWNERS), ["core/a.go", "core/b.go", "x.go"])
        self.assertEqual(list(counts)[:2], ["@alice", "@org/core"])
        self.assertTrue(owners.owns(counts, "Alice"))
        self.assertFalse(owners.owns(counts, "core"))


class Budgets(unittest.TestCase):
    def test_queue_view_fits(self):
        e = rank.rank_prs([pr(n, title="x" * 200) for n in range(1, 21)], RULES, now=NOW)["ranked"]
        lines = [dict(layers.queue_line(x, pr(x["number"], title="t" * 200, author="a"),
                                        {"summary": "s" * 400}, {x["number"]: ["r" * 300] * 3}), why="w" * 300)
                 for x in e]
        view = layers.queue_view({"kind": "pr", "now": {"x": "y" * 3000}}, lines, {"synced_at": "now"})
        self.assertLessEqual(layers.size(view), layers.QUEUE_BUDGET)
        self.assertTrue(view["not_shown"])

    def test_queue_view_stops_when_the_briefs_are_already_cut(self):
        # Three items (the floor) and two briefs still over budget once replaced: fit must move on, not loop.
        lines = [{"n": n, "why": "w" * 5000} for n in range(1, 4)]
        view = layers.queue_view({"kind": "pr", "pack": {"x": "p" * 9000}, "now": {"x": "y" * 9000}}, lines, {})
        self.assertLessEqual(layers.size(view), layers.QUEUE_BUDGET)
        self.assertEqual(view["pack"], "left out to fit; call queue again for it")

    def test_the_last_resort_also_trims_text_and_items_in_lists(self):
        view = layers.fit({"relations": ["r" * 400] * 200}, 3_000, [])
        self.assertLessEqual(layers.size(view), 3_000)
        self.assertTrue(view["relations"][-1].endswith("more left out to fit"))

    def test_card_fits(self):
        rec = pr(1, body="b" * 5000, labels=[f"l{i}" for i in range(30)],
                 closing_issues=[issue(i, [f"x{j}" for j in range(10)], title="t" * 100, body_excerpt="e" * 600,
                                       other_prs=[{"number": 5, "state": "OPEN"}]) for i in range(6)])
        p = profile.change_profile(rec, RULES, {})
        p["functions"] = [f"f{i}" * 10 for i in range(12)]
        card = layers.pr_card(rec, None, p, {f"@o{i}": i for i in range(9)}, False, ["rel" * 40] * 10)
        self.assertLessEqual(layers.size(card), layers.CARD_BUDGET)
        big = {"number": 1, "body": "b" * 9000, "recent_comments": [{"author": "a", "body": "c" * 2000}] * 20}
        self.assertLessEqual(layers.size(layers.issue_card(big, None)), layers.CARD_BUDGET)

    def test_code_view_fits_with_many_files_left_out(self):
        files = [{"path": f"gen/{'d' * 150}/f{i}.pb.go", "additions": 1, "deletions": 0} for i in range(100)]
        view = layers.code_view(pr(1, files=files), {}, RULES)
        self.assertLessEqual(layers.size(view), layers.CODE_BUDGET)
        self.assertEqual(len(view["not_shown"]) + view["not_shown_more"], 100)

    def test_code_view_fits_and_leaves_generated_out(self):
        files = [{"path": f"core/f{i}.go", "additions": 300, "deletions": 0} for i in range(6)]
        files.append({"path": "gen/a.pb.go", "additions": 900, "deletions": 0})
        patch = "\n".join(f"+line {i} " + "x" * 60 for i in range(300))
        view = layers.code_view(pr(1, files=files), {f["path"]: patch for f in files}, RULES,
                                {"path": "AGENTS.md", "untrusted_excerpt": "d" * 1500})
        self.assertLessEqual(layers.size(view), layers.CODE_BUDGET)
        self.assertIn("gen/a.pb.go (generated)", view["not_shown"])
        self.assertLessEqual(sum(len(f["untrusted_patch"].splitlines()) for f in view["files"]), layers.LINES_PER_PR)


class Focus(unittest.TestCase):
    def parse(self, focus, kind="pr", rules=RULES):
        return queue_tools.parse_focus(focus, kind, rules, "me")

    def test_default_is_the_ready_set(self):
        search, filters, said = self.parse("")
        self.assertEqual((search, filters), ('label:"ready"', []))

    def test_area_path_is_a_filter_and_area_label_a_search(self):
        self.assertEqual(self.parse("area:core/engine")[:2], ('label:"ready"', [("area", "core/engine")]))
        self.assertEqual(self.parse("area:area/cli")[:2], ('label:"area/cli"', []))
        self.assertEqual(self.parse('milestone:"1.0" area:core')[:2], ('milestone:"1.0"', [("area", "core")]))

    def test_all_is_not_narrowed_to_the_ready_set(self):
        self.assertEqual(self.parse("all area:core")[:2], ("", [("area", "core")]))
        self.assertEqual(self.parse("all mine")[:2], ("", [("mine", "")]))

    def test_mine_and_release(self):
        self.assertEqual(self.parse("mine")[1], [("mine", "")])
        self.assertEqual(self.parse("mine", "issue")[0], "is:open assignee:me")
        self.assertEqual(self.parse("release", "issue", URGENT)[0],
                         'is:open label:"release-critical","release-high","release-low"')


class Tools(Base):
    """queue and item end to end against the fake GitHub."""

    def setUp(self):
        super().setUp()
        for cache in (fetch._ISSUES, fetch._BY_COMMIT, fetch._PR_FETCHED_AT):
            cache.clear()
        queue_tools._NOW_SHOWN.clear()
        self.rest = {}
        patcher = mock.patch.object(fetch, "_rest_get", self.fake_rest)
        patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ, {"MAINTAINER_GITHUB_LOGIN": "alice"})
        env.start()
        self.addCleanup(env.stop)

    def fake_rest(self, path):
        self.rest[path] = self.rest.get(path, 0) + 1
        if "/pulls/" in path:
            n = int(path.split("/pulls/")[1].split("/")[0])
            return [{"filename": "core/e.go", "patch": "@@ -1 +1 @@ func Validate() error {\n+x\n" if n != 3 else None}]
        if "CODEOWNERS" in path and path.split("contents/")[1].startswith(".github"):
            return {"encoding": "base64", "content": base64.b64encode(b"/core/ @alice\n").decode()}
        if "git/trees" in path:
            return {"tree": [{"type": "blob", "path": "core/AGENTS.md"}]}
        if "AGENTS.md" in path:
            return {"encoding": "base64", "content": base64.b64encode(b"# Core\nThe engine.").decode()}
        raise fetch.NotFound("404")

    def github(self, *args, **kw):
        gh = super().github(*args, **kw)
        orig = gh.__call__

        def call(query, variables):
            if "milestones(" in query:
                return {"repository": {"milestones": {"nodes": [
                    {"title": "1.0", "dueOn": iso(6), "url": "u", "issues": {"totalCount": 3},
                     "pullRequests": {"totalCount": 2}}]}}}
            return orig(query, variables)

        patcher = mock.patch.object(fetch, "_graphql", call)
        patcher.start()
        self.addCleanup(patcher.stop)
        return gh

    def prs(self):
        sec = issue_ref(50)
        sec["labels"] = {"nodes": [{"name": "security"}]}
        return [pr_node(1, files=["core/e.go"]), pr_node(2, files=["core/e.go"], closing=[sec]),
                pr_node(3, files=["docs/x.md"])]

    def test_a_brief_cut_to_fit_comes_again_on_the_next_call(self):
        self.github(self.prs())
        with mock.patch.object(layers, "QUEUE_BUDGET", 50):
            cut = json.loads(queue_tools.handle_queue({"repo": REPO}))
        self.assertIsInstance(cut.get("now"), str)
        again = json.loads(queue_tools.handle_queue({"repo": REPO}))
        self.assertIsInstance(again["now"], dict)
        self.assertNotIn("now", json.loads(queue_tools.handle_queue({"repo": REPO})))

    def test_queue_ranks_with_reasons_relations_and_the_now_brief(self):
        self.github(self.prs())
        out = json.loads(queue_tools.handle_queue({"repo": REPO}))
        self.assertTrue(out["success"], out)
        self.assertEqual([i["n"] for i in out["items"]], [2, 1, 3])
        top = out["items"][0]
        self.assertIn("security on #50", top["why"])
        self.assertIn("same function as #1: Validate in core/e.go", top["relations"])
        self.assertEqual(top["yours"], "CODEOWNERS")
        self.assertEqual(out["now"]["milestones"][0]["title"], "1.0")
        self.assertNotIn("title", top)
        self.assertEqual(top["untrusted_title"], "PR 2")
        self.assertLessEqual(len(json.dumps(out)), layers.QUEUE_BUDGET + 200)
        again = json.loads(queue_tools.handle_queue({"repo": REPO}))
        self.assertNotIn("now", again)
        self.assertEqual([i["n"] for i in again["items"]], [2, 1, 3])

    def test_mine_keeps_only_what_the_maintainer_owns(self):
        self.github(self.prs())
        out = json.loads(queue_tools.handle_queue({"repo": REPO, "focus": "mine"}))
        self.assertEqual([i["n"] for i in out["items"]], [2, 1])
        self.assertEqual(out["filtered_out"], 1)

    def test_item_card_reuses_the_cache_and_code_fetches_patches_once(self):
        gh = self.github(self.prs())
        queue_tools.handle_queue({"repo": REPO})
        calls = len(gh.calls)
        card = json.loads(queue_tools.handle_item({"repo": REPO, "number": 2}))
        self.assertTrue(card["success"], card)
        # Only the gate is read again (it can change between calls); the PR comes from the session cache.
        self.assertFalse(any("pullRequest(number" in q for q in gh.calls[calls:]), "the card should come from the cache")
        self.assertEqual(card["closing_issues"][0]["n"], 50)
        self.assertIn("same function as #1: Validate in core/e.go", card["relations"])
        self.assertLessEqual(len(json.dumps(card)), layers.CARD_BUDGET + 100)
        code = json.loads(queue_tools.handle_item({"repo": REPO, "number": 2, "level": "code"}))
        self.assertEqual(code["files"][0]["path"], "core/e.go")
        self.assertEqual(code["docs"]["path"], "core/AGENTS.md")
        self.assertEqual(sum(v for k, v in self.rest.items() if "/pulls/2/" in k), 1)

    def test_fetch_tools_do_not_show_engine_fields(self):
        self.github(self.prs())
        out = fetch.fetch_pr_candidates(REPO, "label:ready")
        self.assertNotIn("files", out["prs"][0])
        self.assertNotIn("body_excerpt", (out["prs"][1]["closing_issues"] or [{}])[0])


if __name__ == "__main__":
    unittest.main()
