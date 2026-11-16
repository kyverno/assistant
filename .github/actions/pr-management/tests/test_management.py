import copy
import datetime
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
import uuid


ACTION = Path(__file__).resolve().parents[1]
ROOT = ACTION.parents[2]
CONFIG = ROOT / "adopt-ci-templates"
spec = importlib.util.spec_from_file_location("management", ACTION / "run.py")
management = importlib.util.module_from_spec(spec)
spec.loader.exec_module(management)
SHA = "a" * 40
NOW = datetime.datetime.now(datetime.timezone.utc)


def at(days):
    return (NOW - datetime.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def connection(nodes, more=False):
    return {"nodes": nodes, "pageInfo": {
        "hasNextPage": more, "endCursor": "next", "hasPreviousPage": more, "startCursor": "previous",
    }}


def labels(*names):
    return [{"name": name} for name in names]


def readiness():
    return {
        "number": 1, "state": "OPEN", "isDraft": False, "author": {"login": "contributor"},
        "labels": connection([]),
        "commits": {"nodes": [{"commit": {"oid": SHA, "statusCheckRollup": {
            "contexts": connection([{"name": "unit", "conclusion": "SUCCESS", "status": "COMPLETED"}]),
        }}}]},
        "reviewThreads": connection([]),
    }


def stale(stale_days=8, label_actor="Bot", warning_days=8):
    return {
        "state": "OPEN", "isDraft": False, "headRefOid": SHA,
        "author": {"login": "contributor"}, "labels": connection(labels("needs-author-action", "stale")),
        "commits": {"nodes": [{"commit": {"oid": SHA, "committedDate": at(40)}}]},
        "assignees": connection([]), "reviewRequests": connection([]),
        "reviews": connection([]), "comments": connection([{
            "author": {"login": "github-actions[bot]", "__typename": "Bot"},
            "createdAt": at(warning_days),
            "body": f"<!-- pr-stale-author-action: stale {int(datetime.datetime.strptime(at(40), '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=datetime.timezone.utc).timestamp())} {SHA} -->",
        }]),
        "timelineItems": connection([
            {"__typename": "PullRequestCommit", "commit": {"oid": SHA, "committedDate": at(40)}},
            {"__typename": "LabeledEvent", "label": {"name": "needs-author-action"}, "createdAt": at(35)},
            {"__typename": "LabeledEvent", "label": {"name": "stale"}, "createdAt": at(stale_days),
             "actor": {"__typename": label_actor}},
        ]),
    }


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.work = ROOT / f".pr-management-test-{uuid.uuid4().hex}"
        (self.work / ".github").mkdir(parents=True)
        (self.work / "bin").mkdir()
        shutil.copyfile(ACTION / "tests/gh", self.work / "bin/gh")
        (self.work / "bin/gh").chmod(0o755)
        (self.work / "bin/sleep").write_text("#!/bin/sh\nexit 0\n")
        (self.work / "bin/sleep").chmod(0o755)
        for file in ("pr-management.json", "pr-readiness-checks.yml", "labels.yml"):
            shutil.copyfile(CONFIG / file, self.work / ".github" / file)
        self.fixture = {
            "readiness_pr": readiness(),
            "live_pr": {"state": "open", "draft": False, "head": {"sha": SHA},
                        "labels": labels("needs-author-action", "stale")},
        }
        self.env = dict(os.environ, PATH=f"{self.work / 'bin'}:{os.environ['PATH']}",
                        GH_FIXTURE=str(self.work / "fixture.json"), GH_CALLS=str(self.work / "calls.jsonl"),
                        GITHUB_REPOSITORY="kyverno/example", GITHUB_WORKSPACE=str(self.work),
                        GITHUB_EVENT_PATH=str(self.work / "event.json"), GITHUB_EVENT_NAME="pull_request_target",
                        GITHUB_OUTPUT=str(self.work / "output"), PR_MODE="readiness", DRY_RUN="false",
                        PYTHONDONTWRITEBYTECODE="1", STALE_DAYS="7", NUDGE_DAYS="7",
                        CLOSE_DAYS="14", WARNING_DAYS="7", BACKLOG_DAYS="30",
                        FALLBACK_REVIEWERS="@kyverno/reviewers", EXEMPT_LABELS='["no-stale","hold","hold-merge"]')
        self.event = {"pull_request": {"number": 1, "draft": False, "user": {"login": "contributor"}}}

    def tearDown(self):
        shutil.rmtree(self.work)

    def run_action(self, mode="readiness", dry=False, success=True, shell=None):
        (self.work / "fixture.json").write_text(json.dumps(self.fixture))
        (self.work / "event.json").write_text(json.dumps(self.event))
        env = dict(self.env, PR_MODE=mode, DRY_RUN=str(dry).lower())
        command = ["python3", str(ACTION / "run.py")]
        if shell:
            env["PR_MANAGEMENT_LIBRARY_ONLY"] = "true"
            command = ["bash", "-c", f'source "{ACTION}/stale.sh"; {shell}']
        result = subprocess.run(command, env=env, cwd=self.work, capture_output=True, text=True, timeout=90)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.work / "calls.jsonl"
        self.calls = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
        self.mutations = [
            call for call in self.calls if call[:1] == ["pr"] and call[1] in {"edit", "comment", "close"}
            or "--method" in call
        ]
        return result

    def assert_not_ready(self):
        self.assertFalse(any("--add-label" in call and "ready-for-review" in call for call in self.mutations))
        self.assertFalse(any("--remove-label" in call and "needs-author-action" in call for call in self.mutations))

    def test_clean_ready_and_production_mutation(self):
        self.run_action()
        self.assertTrue(any("--add-label" in call and "ready-for-review" in call for call in self.mutations))

    def test_readiness_leaves_exempt_labelled_pr_alone(self):
        for exempt in json.loads((self.work / ".github/pr-management.json").read_text())["exempt_labels"]:
            with self.subTest(label=exempt):
                self.fixture["readiness_pr"]["labels"] = connection(labels("ready-for-review", exempt))
                self.fixture["readiness_pr"]["reviewThreads"] = connection([{
                    "isResolved": False, "comments": connection([{
                        "author": {"login": "reviewer"}, "createdAt": at(1)}]),
                }])
                result = self.run_action()
                self.assertEqual(self.mutations, [])
                self.assertIn("exempt label present", result.stdout)
                (self.work / "calls.jsonl").unlink(missing_ok=True)

    def test_requested_changes_block_readiness_until_answered(self):
        def review(state, days, body="please change this", assoc="MEMBER", login="maintainer", kind="User",
                   oid=SHA):
            return {"author": {"login": login, "__typename": kind}, "authorAssociation": assoc,
                    "state": state, "submittedAt": at(days), "bodyText": body, "commit": {"oid": oid} if oid else None}

        def comment(days, assoc="MEMBER", login="maintainer", kind="User"):
            return {"author": {"login": login, "__typename": kind}, "authorAssociation": assoc, "createdAt": at(days)}

        author_reply = comment(1, assoc="CONTRIBUTOR", login="contributor")
        requested = review("CHANGES_REQUESTED", 3)
        # (name, reviews, comments, blocked)
        cases = (
            ("changes requested, no answer", [requested], [], True),
            ("author replied afterwards", [requested], [author_reply], False),
            ("author pushed afterwards", [review("CHANGES_REQUESTED", 3, oid="b" * 40)], [], False),
            ("reviewed commit no longer exists", [review("CHANGES_REQUESTED", 3, oid=None)], [], False),
            ("same maintainer approved later", [requested, review("APPROVED", 2, body="")], [], False),
            ("review dismissed", [requested, review("DISMISSED", 2, body="")], [], False),
            ("another maintainer approved", [requested, review("APPROVED", 2, body="", login="other")], [], True),
            ("new request after the author replied", [requested, review("CHANGES_REQUESTED", 0.5)], [author_reply], True),
            ("two maintainers, one approves", [requested, review("CHANGES_REQUESTED", 3, login="other"),
                                               review("APPROVED", 2, body="")], [], True),
            ("author's own review does not answer for a bot", [review("CHANGES_REQUESTED", 3, assoc="OWNER")],
             [comment(1, assoc="OWNER", login="contributor")], False),
            ("plain maintainer comment", [], [comment(2)], False),
            ("comment-only review with a body", [review("COMMENTED", 2)], [], False),
            ("bot request", [review("CHANGES_REQUESTED", 2, login="coderabbitai[bot]", kind="Bot")], [], False),
            ("non-maintainer request", [review("CHANGES_REQUESTED", 2, assoc="CONTRIBUTOR")], [], False),
            ("pending review", [review("PENDING", 2)], [], False),
        )
        for name, reviews, comments, blocked in cases:
            with self.subTest(case=name):
                pr = self.fixture["readiness_pr"]
                pr["reviews"], pr["comments"] = connection(reviews), connection(comments)
                self.run_action()
                added = lambda label: any("--add-label" in call and label in call for call in self.mutations)
                self.assertEqual(added("needs-author-action"), blocked)
                self.assertEqual(added("ready-for-review"), not blocked)
                (self.work / "calls.jsonl").unlink(missing_ok=True)

    def contexts(self, *nodes):
        self.fixture["readiness_pr"]["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["contexts"]["nodes"] = list(nodes)

    def added(self, label):
        return any("--add-label" in call and label in call for call in self.mutations)

    def test_builtin_checks_apply_without_a_repo_file(self):
        (self.work / ".github/pr-readiness-checks.yml").unlink()
        self.contexts({"name": "build", "conclusion": "FAILURE", "status": "COMPLETED"})
        self.run_action()
        self.assertTrue(self.added("needs-author-action"))
        (self.work / "calls.jsonl").unlink()
        self.contexts({"name": "build", "conclusion": "SUCCESS", "status": "COMPLETED"})
        self.fixture["readiness_pr"]["labels"] = connection(labels("merge-conflicts"))
        self.run_action()
        self.assertTrue(self.added("needs-author-action"))
        (self.work / "calls.jsonl").unlink()
        self.fixture["readiness_pr"]["labels"] = connection([])
        self.run_action()
        self.assertTrue(self.added("ready-for-review"))

    def test_repo_file_can_disable_replace_and_add_checks(self):
        checks = self.work / ".github/pr-readiness-checks.yml"
        conflicting = connection(labels("merge-conflicts"))
        unresolved = connection([{"isResolved": False, "comments": connection([
            {"author": {"login": "reviewer"}, "createdAt": at(1)}])}])
        cases = (
            ("disable", "disable: [conflicts]\n", {"labels": conflicting}, False),
            ("not disabled", "", {"labels": conflicting}, True),
            ("replace", "threads:\n  test: 'false'\n  message: '\"unused\"'\n", {"reviewThreads": unresolved}, False),
            ("built-in kept", "", {"reviewThreads": unresolved}, True),
            ("add", "extra:\n  test: 'true'\n  message: '\"extra\"'\n", {}, True),
        )
        for name, content, pr_fields, blocked in cases:
            with self.subTest(case=name):
                checks.write_text(content)
                self.fixture["readiness_pr"] = {**readiness(), **pr_fields}
                self.run_action()
                self.assertEqual(self.added("needs-author-action"), blocked)
                self.assertEqual(self.added("ready-for-review"), not blocked)
                (self.work / "calls.jsonl").unlink(missing_ok=True)

    def test_infra_checks_come_from_the_config(self):
        self.contexts({"name": "E2E Gate", "conclusion": "FAILURE", "status": "COMPLETED"})
        self.run_action()
        self.assertTrue(self.added("needs-author-action"))
        (self.work / "calls.jsonl").unlink()
        config = self.work / ".github/pr-management.json"
        data = json.loads(config.read_text())
        data["infra_checks"] = {"E2E Gate": "base-branch or CI-infrastructure issue"}
        config.write_text(json.dumps(data))
        self.run_action()
        self.assertTrue(self.added("ready-for-review"))
        self.assertTrue(any("E2E Gate" in " ".join(call) and "CI-infrastructure issue" in " ".join(call)
                            for call in self.mutations))

    def test_invalid_check_selection_fails_before_api(self):
        checks = self.work / ".github/pr-readiness-checks.yml"
        config = self.work / ".github/pr-management.json"
        data = json.loads(config.read_text())
        bad_files = (
            "disable: [no_such_check]\n", "disable: conflicts\n", "disable: [conflicts, conflicts]\n",
            "disable: [conflicts]\nconflicts:\n  test: 'true'\n  message: '\"x\"'\n",
            "disable: [dco, ci, infra_checks, conflicts, threads, unanswered]\n",
        )
        for content in bad_files:
            with self.subTest(file=content):
                checks.write_text(content)
                self.run_action(mode="validate", success=False)
                self.assertEqual(self.calls, [])
        checks.write_text("# nothing to change\n")
        self.run_action(mode="validate")
        checks.unlink()
        self.run_action(mode="validate")
        self.env["READINESS_CHECKS"] = ".github/missing.yml"
        self.run_action(mode="validate", success=False)
        del self.env["READINESS_CHECKS"]
        for infra in ({"": "x"}, {"E2E Gate": ""}, ["E2E Gate"]):
            with self.subTest(infra=infra):
                config.write_text(json.dumps({**data, "infra_checks": infra}))
                self.run_action(mode="validate", success=False)

    def test_only_the_latest_run_of_each_check_decides(self):
        run = lambda name, conclusion, started, status="COMPLETED": {
            "name": name, "conclusion": conclusion, "status": status, "startedAt": at(started)}
        # (name, contexts, blocked, ready)
        cases = (
            ("later run passed after an earlier failure",
             [run("markers", "FAILURE", 3), run("markers", "SUCCESS", 2), run("markers", "SUCCESS", 1)], False, True),
            ("order in the rollup does not matter",
             [run("markers", "SUCCESS", 1), run("markers", "FAILURE", 3)], False, True),
            ("later run failed after an earlier pass",
             [run("markers", "SUCCESS", 3), run("markers", "FAILURE", 1)], True, False),
            ("another check still fails", [run("markers", "FAILURE", 3), run("markers", "SUCCESS", 1),
                                           run("build", "FAILURE", 2)], True, False),
            ("queued re-run is not hidden by the old result",
             [run("markers", "FAILURE", 3), {"name": "markers", "status": "QUEUED", "conclusion": None}], False, False),
            ("DCO is deduplicated too", [run("DCO", "FAILURE", 3), run("DCO", "SUCCESS", 1)], False, True),
            ("commit statuses are deduplicated by context",
             [{"context": "legacy", "state": "FAILURE", "createdAt": at(3)},
              {"context": "legacy", "state": "SUCCESS", "createdAt": at(1)}], False, True),
            ("an entry with no name still counts as pending", [run("markers", "SUCCESS", 1), {}], False, False),
        )
        for name, contexts, blocked, ready in cases:
            with self.subTest(case=name):
                self.contexts(*contexts)
                self.run_action()
                self.assertEqual(self.added("needs-author-action"), blocked)
                self.assertEqual(self.added("ready-for-review"), ready)
                (self.work / "calls.jsonl").unlink(missing_ok=True)

    def enable_nudge(self, value=True):
        config = self.work / ".github/pr-management.json"
        data = json.loads(config.read_text())
        data["nudge_plain_comments"] = value
        config.write_text(json.dumps(data))

    def nudges(self):
        return [call for call in self.mutations if call[:2] == ["pr", "comment"] and "pr-readiness-check-nudge" in " ".join(call)]

    def test_plain_maintainer_comment_is_nudged_once_without_changing_labels(self):
        def comment(days, id="IC_1", assoc="MEMBER", login="maintainer", kind="User", text="please check this"):
            return {"id": id, "url": f"https://github.com/kyverno/example/pull/1#issuecomment-{id}", "bodyText": text,
                    "author": {"login": login, "__typename": kind}, "authorAssociation": assoc, "createdAt": at(days)}

        def review(days, oid=SHA, body="looks off", state="COMMENTED", id="PRR_1"):
            return {"id": id, "url": f"https://github.com/kyverno/example/pull/1#pullrequestreview-{id}",
                    "author": {"login": "maintainer", "__typename": "User"}, "authorAssociation": "MEMBER",
                    "state": state, "submittedAt": at(days), "bodyText": body, "commit": {"oid": oid} if oid else None}

        reply = comment(1, id="IC_reply", assoc="CONTRIBUTOR", login="contributor")
        inline_reply = connection([{"isResolved": True, "comments": connection([
            {"author": {"login": "contributor"}, "createdAt": at(1)}])}])
        inline_by_other = connection([{"isResolved": True, "comments": connection([
            {"author": {"login": "someone-else"}, "createdAt": at(1)}])}])
        # (name, reviews, comments, committed days ago, nudged, extra PR fields)
        cases = (
            ("unanswered plain comment", [], [comment(2)], 9, True, {}),
            ("author replied afterwards", [], [comment(2), reply], 9, False, {}),
            ("author pushed afterwards", [], [comment(2)], 1, False, {}),
            ("review that only comments", [review(2)], [], 9, True, {}),
            ("author pushed after the reviewed commit", [review(2, oid="b" * 40)], [], 9, False, {}),
            ("reviewed commit no longer exists", [review(2, oid=None)], [], 9, False, {}),
            ("review without a body", [review(2, body="")], [], 9, False, {}),
            ("approval is not a comment", [review(2, state="APPROVED")], [], 9, False, {}),
            ("not a maintainer", [], [comment(2, assoc="CONTRIBUTOR")], 9, False, {}),
            ("bot", [], [comment(2, login="coderabbitai[bot]", kind="Bot")], 9, False, {}),
            ("the author's own comment", [], [comment(2, login="contributor", assoc="OWNER")], 9, False, {}),
            # Gaps found by going through the cases.
            ("review body answered only by inline replies", [review(2)], [], 9, False, {"reviewThreads": inline_reply}),
            ("an inline reply by someone else is not the author's", [review(2)], [], 9, True,
             {"reviewThreads": inline_by_other}),
            ("same reviewer approved afterwards", [review(1, state="APPROVED", body="")], [comment(2)], 9, False, {}),
            ("another reviewer approved afterwards",
             [{**review(1, state="APPROVED", body=""), "author": {"login": "other", "__typename": "User"}}],
             [comment(2)], 9, True, {}),
            ("Prow command", [], [comment(2, text="/retest")], 9, False, {}),
            ("Prow command after whitespace", [], [comment(2, text="  \n/lgtm")], 9, False, {}),
            ("bare LGTM", [], [comment(2, text="LGTM!")], 9, False, {}),
            ("bare thanks", [], [comment(2, text="Thanks.")], 9, False, {}),
            ("LGTM with a request after it", [], [comment(2, text="LGTM but please rename the flag")], 9, True, {}),
            ("comment older than the window", [], [comment(9)], 20, False, {}),
            ("comment inside the window", [], [comment(6)], 20, True, {}),
        )
        self.enable_nudge()
        for name, reviews, comments, committed, nudged, extra in cases:
            with self.subTest(case=name):
                (self.work / "calls.jsonl").unlink(missing_ok=True)
                pr = self.fixture["readiness_pr"]
                pr["reviews"], pr["comments"] = connection(reviews), connection(comments)
                pr["reviewThreads"] = connection([])
                pr.update(extra)
                pr["commits"]["nodes"][0]["commit"]["committedDate"] = at(committed)
                self.run_action()
                self.assertEqual(len(self.nudges()), 1 if nudged else 0)
                # Never a label change beyond the normal ready path.
                self.assertTrue(self.added("ready-for-review"))
                self.assertFalse(self.added("needs-author-action"))
                self.assertFalse(any("hold" in " ".join(call) and "--add-label" in call for call in self.mutations))
                (self.work / "calls.jsonl").unlink(missing_ok=True)

    def test_nudge_names_the_reviewer_links_the_comment_and_offers_both_options(self):
        self.enable_nudge()
        pr = self.fixture["readiness_pr"]
        pr["comments"] = connection([{
            "id": "IC_1", "url": "https://github.com/kyverno/example/pull/1#issuecomment-1",
            "author": {"login": "maintainer", "__typename": "User"}, "authorAssociation": "MEMBER", "createdAt": at(2)}])
        pr["commits"]["nodes"][0]["commit"]["committedDate"] = at(9)
        self.run_action()
        body = " ".join(self.nudges()[0])
        self.assertIn(f"<!-- pr-readiness-check-nudge: {SHA} -->", body)
        self.assertIn("@maintainer", body)
        self.assertIn("issuecomment-1", body)
        self.assertIn("Request changes", body)
        self.assertIn("`hold`", body)

    def test_nudge_quotes_a_sanitised_excerpt_of_the_comment(self):
        self.enable_nudge()
        pr = self.fixture["readiness_pr"]
        pr["commits"]["nodes"][0]["commit"]["committedDate"] = at(9)

        def nudge_for(text):
            pr["comments"] = connection([{
                "id": "IC_1", "url": "https://github.com/kyverno/example/pull/1#issuecomment-1", "bodyText": text,
                "author": {"login": "maintainer", "__typename": "User"}, "authorAssociation": "MEMBER",
                "createdAt": at(2)}])
            self.run_action()
            posted = self.nudges()
            (self.work / "calls.jsonl").unlink(missing_ok=True)
            return posted[0][posted[0].index("--body") + 1]

        body = nudge_for("@alice @org/team please look, see #123 and kyverno/kyverno#9\n\n<!-- pr-readiness-check-nudge: X -->")
        quote = [line for line in body.splitlines() if line.startswith("> ")]
        self.assertEqual(len(quote), 1)
        for forbidden in ("@alice", "@org", "#123", "#9", "<", ">"):
            self.assertNotIn(forbidden, quote[0][2:])
        self.assertIn("alice org/team please look, see 123 and kyverno/kyverno9", quote[0])
        # The quote ends at its own paragraph: the question starts on a new line, after a blank one.
        self.assertNotIn("This PR is ready", quote[0])
        self.assertIn("\n\nThis PR is ready for review", body)
        # Only the bot's own marker line carries a comment marker, and the mention in the greeting is the reviewer's.
        self.assertEqual(body.count("<!--"), 1)
        self.assertTrue(body.splitlines()[1].startswith("@maintainer,"))

        long_quote = [line for line in nudge_for("word " * 100).splitlines() if line.startswith("> ")][0]
        self.assertTrue(long_quote.endswith("…"))
        self.assertEqual(len(long_quote[2:]), 101)
        self.assertNotIn("…", [line for line in nudge_for("x" * 100).splitlines() if line.startswith("> ")][0])

        self.assertNotIn("\n> ", nudge_for("   "))

    def test_nudge_is_posted_once_per_head_commit_whatever_the_comment(self):
        self.enable_nudge()
        pr = self.fixture["readiness_pr"]
        pr["comments"] = connection([{
            "id": "IC_1", "url": "https://github.com/kyverno/example/pull/1#issuecomment-1",
            "author": {"login": "maintainer", "__typename": "User"}, "authorAssociation": "MEMBER", "createdAt": at(2)}])
        pr["commits"]["nodes"][0]["commit"]["committedDate"] = at(9)
        # Posted earlier for a different comment on this same commit: maintainers talking to each other must not
        # trigger a nudge per message.
        self.fixture["comment_bodies"] = f"<!-- pr-readiness-check-nudge: {SHA} -->"
        self.run_action()
        self.assertEqual(self.nudges(), [])
        (self.work / "calls.jsonl").unlink()
        # A marker for an older head does not suppress it.
        self.fixture["comment_bodies"] = f"<!-- pr-readiness-check-nudge: {'b' * 40} -->"
        self.run_action()
        self.assertEqual(len(self.nudges()), 1)
        self.assertTrue(any("--paginate" in call for call in self.calls))

    def test_a_sweep_nudges_every_pr_that_needs_it(self):
        self.enable_nudge()
        self.env["GITHUB_EVENT_NAME"] = "schedule"
        self.fixture["discovery"] = {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [{"number": n, "isDraft": False, "closingIssuesReferences": connection([])} for n in range(1, 13)],
        }
        pr = self.fixture["readiness_pr"]
        pr["comments"] = connection([{
            "id": "IC_1", "url": "https://github.com/kyverno/example/pull/1#issuecomment-1", "bodyText": "please check",
            "author": {"login": "maintainer", "__typename": "User"}, "authorAssociation": "MEMBER", "createdAt": at(2)}])
        pr["commits"]["nodes"][0]["commit"]["committedDate"] = at(9)
        self.run_action()
        # No cap: every one of the 12 PRs is nudged, each once, and none is held back for a later sweep.
        self.assertEqual(len(self.nudges()), 12)
        self.assertEqual(len([c for c in self.mutations if "--add-label" in c and "ready-for-review" in c]), 12)

    def test_nudge_is_off_by_default_and_never_on_a_blocked_pr(self):
        pr = self.fixture["readiness_pr"]
        pr["comments"] = connection([{
            "id": "IC_1", "url": "https://github.com/kyverno/example/pull/1#issuecomment-1",
            "author": {"login": "maintainer", "__typename": "User"}, "authorAssociation": "MEMBER", "createdAt": at(2)}])
        pr["commits"]["nodes"][0]["commit"]["committedDate"] = at(9)
        self.run_action()
        self.assertEqual(self.nudges(), [])
        (self.work / "calls.jsonl").unlink()
        self.enable_nudge()
        self.contexts({"name": "build", "conclusion": "FAILURE", "status": "COMPLETED"})
        self.run_action()
        self.assertEqual(self.nudges(), [])
        self.assertTrue(self.added("needs-author-action"))

    def test_nudge_setting_must_be_a_boolean(self):
        for value in ("yes", 1, None):
            with self.subTest(value=value):
                self.enable_nudge(value)
                self.run_action(mode="validate", success=False)
                self.assertEqual(self.calls, [])
        self.enable_nudge(True)
        self.run_action(mode="validate")

    def test_pending_and_empty_ci_never_ready(self):
        for contexts in ([], [{"name": "unit", "status": "IN_PROGRESS", "conclusion": None}],
                         [{"context": "legacy", "state": "PENDING"}], [{}],
                         [{"name": "unit", "status": "COMPLETED", "conclusion": None}]):
            with self.subTest(contexts=contexts):
                self.fixture["readiness_pr"]["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["contexts"]["nodes"] = contexts
                self.run_action()
                self.assert_not_ready()

    def test_missing_rollup_is_pending(self):
        self.fixture["readiness_pr"]["commits"]["nodes"][0]["commit"]["statusCheckRollup"] = None
        self.run_action()
        self.assert_not_ready()

    def test_gated_and_failed_approval_lookup(self):
        self.fixture["gated_count"] = 1
        self.run_action()
        self.assertFalse(any("ready-for-review" in c and "--add-label" in c for c in self.mutations))
        self.assertTrue(any("workflow-approval-required" in c for c in self.mutations))
        (self.work / "calls.jsonl").unlink()
        self.fixture["gated_failure"] = True
        self.run_action()
        self.assertEqual(self.mutations, [])

    def test_review_thread_pagination_blocks_and_marker_deduplicates(self):
        self.fixture["readiness_pr"]["reviewThreads"] = connection([], more=True)
        self.fixture["thread_page"] = connection([{
            "isResolved": False, "comments": {"nodes": [{"author": {"login": "reviewer"}, "createdAt": at(1)}]},
        }])
        self.fixture["comment_bodies"] = "<!-- pr-readiness-check: threads -->"
        self.run_action()
        self.assert_not_ready()
        self.assertTrue(any("needs-author-action" in call for call in self.mutations))
        self.assertFalse(any(call[:2] == ["pr", "comment"] for call in self.mutations))
        self.assertTrue(any("--paginate" in call for call in self.calls))

    def test_truncated_pagination_never_clears_author_or_marks_ready(self):
        self.fixture["readiness_pr"]["labels"] = connection(
            labels("needs-author-action", "workflow-approval-required"), more=True)
        self.run_action()
        self.assertEqual(self.mutations, [])

    def test_milestone_stamp_is_configured_and_disabled_by_empty_prefix(self):
        self.env["GITHUB_EVENT_NAME"] = "schedule"
        self.fixture["discovery"] = {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [{"number": 1, "isDraft": False, "closingIssuesReferences": connection([{
                "repository": {"nameWithOwner": "kyverno/example"},
                "milestone": {"state": "OPEN", "title": "Kyverno Release 1.18"},
            }])}],
        }
        path = self.work / ".github/pr-management.json"
        config = json.loads(path.read_text())
        config["milestone_prefix"] = "Kyverno Release "
        path.write_text(json.dumps(config))
        self.run_action()
        self.assertTrue(any("--add-label" in call and "milestone-pr" in call for call in self.mutations))
        (self.work / "calls.jsonl").unlink()
        config["milestone_prefix"] = ""
        path.write_text(json.dumps(config))
        self.run_action()
        self.assertFalse(any("milestone-pr" in call for call in self.mutations))

    def test_partial_and_error_data_fail_closed(self):
        for field in ("labels", "reviewThreads"):
            with self.subTest(field=field):
                self.fixture["readiness_pr"][field] = None
                self.run_action(success=False)
                self.assertEqual(self.mutations, [])
                self.fixture["readiness_pr"] = readiness()
        self.fixture["graphql_errors"] = True
        self.run_action(success=False)
        self.assertEqual(self.mutations, [])

    def test_sweep_isolates_per_pr_fetch_failures(self):
        self.env["GITHUB_EVENT_NAME"] = "schedule"
        self.fixture["discovery"] = {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [{"number": n, "isDraft": False, "closingIssuesReferences": connection([])} for n in (1, 2)],
        }
        self.fixture["fail_detail_numbers"] = [1]
        result = self.run_action(success=False)
        self.assertIn("1 PR detail fetch(es) failed", result.stdout + result.stderr)
        self.assertFalse(any(call[:3] == ["pr", "edit", "1"] for call in self.mutations))
        self.assertTrue(any(call[:3] == ["pr", "edit", "2"] and "ready-for-review" in call
                            for call in self.mutations))

    def test_readiness_head_and_draft_races(self):
        for live in ({"head": {"sha": "b" * 40}}, {"draft": True}, {"state": "closed"}):
            with self.subTest(live=live):
                saved = copy.deepcopy(self.fixture["live_pr"])
                self.fixture["live_pr"].update(live)
                self.run_action()
                self.assertEqual(self.mutations, [])
                self.fixture["live_pr"] = saved

    def test_readiness_dry_run(self):
        result = self.run_action(dry=True)
        self.assertEqual(self.mutations, [])
        self.assertIn("[dry-run]", result.stdout)

    def test_draft_transition_clears_three_labels_and_preview(self):
        self.event["pull_request"]["draft"] = True
        self.fixture["live_pr"].update(draft=True, labels=labels(
            "ready-for-review", "needs-author-action", "workflow-approval-required"))
        self.run_action()
        self.assertEqual(len(self.mutations), 3)
        (self.work / "calls.jsonl").unlink()
        self.run_action(mode="stale", dry=True)
        self.assertEqual(self.mutations, [])

    def test_label_rendering_and_preview_no_api(self):
        result = self.run_action(mode="labels", dry=True)
        self.assertEqual(self.calls, [])
        self.assertIn("no labeler or merge-conflict mutations", result.stdout)
        output = (self.work / "output").read_text().split("=", 1)[1].strip()
        generated = json.loads(Path(output).read_text())
        self.assertEqual(generated["area/docs"][0]["changed-files"][0]["any-glob-to-any-file"], ["docs/**", "**/*.md"])
        self.assertNotIn("needs-author-action", generated)

    def test_sync_idempotency_preview_and_failure(self):
        _, metadata = management.render_labels(management.yaml_file(self.work / ".github/labels.yml"))
        self.fixture["label_pages"] = [metadata]
        self.run_action(mode="sync-labels")
        self.assertEqual(self.mutations, [])
        self.fixture["label_pages"] = [[]]
        self.run_action(mode="sync-labels", dry=True)
        self.assertEqual(self.mutations, [])
        self.fixture["labels_failure"] = True
        self.run_action(mode="sync-labels", success=False)

    def test_sync_write_errors_fail(self):
        self.fixture["write_failure"] = True
        self.run_action(mode="sync-labels", success=False)

    def test_validate_offline_does_not_evaluate_checks(self):
        for key in ("GH_TOKEN", "GITHUB_TOKEN", "GITHUB_EVENT_PATH", "GITHUB_EVENT_NAME", "GITHUB_OUTPUT"):
            self.env.pop(key, None)
        (self.work / ".github/pr-readiness-checks.yml").write_text(
            'never:\n  test: \'error("must not execute")\'\n  message: \'"unused"\'\n')
        self.run_action(mode="validate")
        self.assertEqual(self.calls, [])
        (self.work / ".github/pr-readiness-checks.yml").write_text("bad:\n  test: 'invalid('\n  message: 'null'\n")
        self.run_action(mode="validate", success=False)
        self.assertEqual(self.calls, [])

    def test_validate_module_rejects_top_level_expression_injection(self):
        # Escape the check's generated delimiters: an imported module must reject a program.
        program = '.author.login as $author | []; error("must not execute")'
        folder = self.work / "module"
        folder.mkdir()
        (folder / "checks.jq").write_text(f'def readiness: ({program}); error("must not execute");')
        result = subprocess.run(
            ["jq", "-L", str(folder), "-n", 'import "checks" as checks; null'],
            capture_output=True, text=True, timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("compile error", result.stderr)

    def test_action_and_workflow_syntax_and_preview_guards(self):
        action = management.yaml_file(ACTION / "action.yml")
        self.assertEqual(action["runs"]["using"], "composite")
        for step in action["runs"]["steps"]:
            if step.get("uses", "").startswith(("actions/labeler@", "eps1lon/")):
                self.assertIn("inputs.dry-run == 'false'", step["if"])
        for name in ("pr-management", "pr-management-validate"):
            workflow = management.yaml_file(ROOT / ".github/workflows" / f"{name}.yml")
            for job in workflow["jobs"].values():
                self.assertGreater(job["timeout-minutes"], 0)

    def test_invalid_config_fails_before_api(self):
        config = json.loads((self.work / ".github/pr-management.json").read_text())
        for change in ({"warning_days": 0}, {"close_days": True}, {"close_days": 2.5},
                       {"backlog_days": 3651}, {"warning_days": 15}, {"unknown": 1},
                       {"exempt_labels": "hold"}, {"fallback_reviewers": "$(touch unsafe)"}):
            with self.subTest(change=change):
                (self.work / ".github/pr-management.json").write_text(json.dumps(dict(config, **change)))
                self.run_action(mode="validate", success=False)
                self.assertEqual(self.calls, [])
        config.pop("warning_days")
        (self.work / ".github/pr-management.json").write_text(json.dumps(config))
        self.run_action(mode="validate", success=False)
        self.assertEqual(self.calls, [])

    def test_invalid_overrides_and_label_rules_fail_before_api(self):
        for key, value in (("DRY_RUN", "yes"), ("STALE_DAYS", "0"), ("STALE_DAYS", "15"),
                           ("NUDGE_DAYS", "7.5"), ("NUDGE_DAYS", "3651")):
            with self.subTest(key=key, value=value):
                env = dict(self.env)
                self.env[key] = value
                # dry-run is set explicitly by run_action, so test it through the entry point.
                (self.work / "fixture.json").write_text(json.dumps(self.fixture))
                result = subprocess.run(
                    ["python3", str(ACTION / "run.py")], cwd=self.work,
                    env=dict(self.env, PR_MODE="validate"), capture_output=True, text=True, timeout=10,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.work / "calls.jsonl").exists())
                self.env = env
        (self.work / ".github/labels.yml").write_text(
            'example:\n  color: "abcdef"\n  rules:\n    - changed-files:\n        - unsupported-matcher: ["**"]\n')
        self.run_action(mode="validate", success=False)
        self.assertEqual(self.calls, [])

    def test_stale_grace_requires_seven_days_after_actual_bot_warning(self):
        for label_actor, label_days, warning_days, should_close in (
            ("Bot", 0, 0, False), ("Bot", 6, 6, False), ("Bot", 7, 7, True), ("Bot", 8, 8, True),
            ("User", 20, 1, False), ("User", 20, 7, True), ("User", 20, 8, True),
        ):
            with self.subTest(actor=label_actor, label_days=label_days, warning_days=warning_days):
                (self.work / "calls.jsonl").unlink(missing_ok=True)
                self.fixture["stale_pr"] = stale(label_days, label_actor, warning_days)
                self.run_action(shell="handle_close 1")
                self.assertEqual(any(call[:2] == ["pr", "close"] for call in self.mutations), should_close)

    def test_manual_stale_label_without_bot_warning_never_closes(self):
        self.fixture["stale_pr"] = stale(20, "User", 20)
        self.fixture["stale_pr"]["comments"]["nodes"][0]["author"]["__typename"] = "User"
        self.run_action(shell="handle_close 1")
        self.assertEqual(self.mutations, [])

    def test_stale_close_dry_run(self):
        self.fixture["stale_pr"] = stale()
        self.run_action(dry=True, shell="handle_close 1")
        self.assertEqual(self.mutations, [])

    def test_holds_and_state_guards_in_all_handlers(self):
        for change in ({"state": "CLOSED"}, {"isDraft": True},
                       {"labels": connection(labels("needs-author-action", "stale", "ready-for-review", "hold"))},
                       {"labels": connection(labels("needs-author-action", "stale", "ready-for-review", "hold-merge"))}):
            with self.subTest(change=change):
                self.fixture["stale_pr"] = dict(stale(), **change)
                for handler in ("handle_stale_label", "handle_needs_author", "handle_close", "handle_ready"):
                    self.run_action(shell=f"{handler} 1", success="state" not in change and "isDraft" not in change)
                    if handler == "handle_stale_label" and "labels" in change:
                        # Exemption resets the stale cycle instead of preserving the old clock.
                        self.assertEqual(self.mutations, [["pr", "edit", "1", "--repo", "kyverno/example",
                                                           "--remove-label", "stale"]])
                    else:
                        self.assertEqual(self.mutations, [])
                    (self.work / "calls.jsonl").unlink(missing_ok=True)

    def test_force_push_resets_cycle_and_close_rechecks_live_hold(self):
        self.fixture["stale_pr"] = stale()
        self.fixture["stale_pr"]["timelineItems"]["nodes"].append({
            "__typename": "HeadRefForcePushedEvent", "createdAt": at(1)})
        self.run_action(shell="handle_close 1")
        self.assertEqual(self.mutations, [])
        self.fixture["stale_pr"] = stale()
        self.fixture["live_pr"]["labels"].append({"name": "hold"})
        self.run_action(shell="handle_close 1", success=False)
        self.assertEqual(self.mutations, [])

    def test_force_push_clears_stale_and_timeline_history_is_paginated(self):
        self.fixture["stale_pr"] = stale()
        timeline = self.fixture["stale_pr"]["timelineItems"]["nodes"]
        self.fixture["timeline_page"] = connection(timeline)
        self.fixture["stale_pr"]["timelineItems"] = connection([{
            "__typename": "HeadRefForcePushedEvent", "createdAt": at(1),
        }], more=True)
        self.run_action(shell="handle_stale_label 1")
        self.assertTrue(any("--remove-label" in call and "stale" in call for call in self.mutations))
        self.assertTrue(any("cursor=previous" in call for call in self.calls))

    def test_missing_stale_history_never_mutates(self):
        self.fixture["stale_pr"] = stale()
        self.fixture["stale_pr"]["labels"]["pageInfo"]["hasNextPage"] = True
        self.run_action(shell="handle_close 1", success=False)
        self.assertEqual(self.mutations, [])

    def test_old_backlog_marked_and_not_closed_same_sweep(self):
        self.fixture["stale_pr"] = stale()
        self.fixture["stale_pr"]["labels"] = connection(labels("needs-author-action"))
        self.fixture["stale_pr"]["timelineItems"]["nodes"].pop()
        self.fixture["stale_pr"]["comments"] = connection([])
        self.env["GITHUB_EVENT_NAME"] = "schedule"
        self.run_action(mode="stale")
        self.assertTrue(any("--add-label" in call and "stale" in call for call in self.mutations))
        self.assertFalse(any(call[:2] == ["pr", "close"] for call in self.mutations))

    def test_stale_bot_marker_on_older_comment_page_avoids_duplicate(self):
        self.fixture["stale_pr"] = stale()
        pr = self.fixture["stale_pr"]
        pr["labels"] = connection(labels("ready-for-review"))
        pr["timelineItems"]["nodes"].append({
            "__typename": "LabeledEvent", "label": {"name": "ready-for-review"}, "createdAt": at(10)})
        pr["comments"] = connection([], more=True)
        self.fixture["comment_page"] = connection([{
            "author": {"login": "github-actions[bot]", "__typename": "Bot"},
            "createdAt": at(8), "body": f"<!-- pr-review-nudge: {SHA} -->",
        }])
        self.run_action(shell="handle_ready 1")
        self.assertEqual(self.mutations, [])
        self.assertTrue(any("cursor=previous" in call for call in self.calls))

    def test_author_replies_do_not_reset_author_clock(self):
        self.fixture["stale_pr"] = stale()
        self.fixture["stale_pr"]["comments"]["nodes"].append({
            "author": {"login": "contributor", "__typename": "User"}, "createdAt": at(0), "body": "still here",
        })
        self.run_action(shell="handle_close 1")
        self.assertTrue(any(call[:2] == ["pr", "close"] for call in self.mutations))

    def test_unobserved_late_commit_starts_conservative_clock(self):
        self.fixture["stale_pr"] = stale()
        pr = self.fixture["stale_pr"]
        pr["labels"] = connection(labels("needs-author-action"))
        pr["timelineItems"]["nodes"].append({
            "__typename": "PullRequestCommit", "commit": {"oid": SHA, "committedDate": at(40)},
        })
        self.run_action(shell="handle_needs_author 1")
        self.assertTrue(any(call[:2] == ["pr", "comment"] for call in self.mutations))
        self.assertFalse(any("--add-label" in call for call in self.mutations))


    def test_observed_marker_is_visible_text_and_is_minimized(self):
        self.fixture["stale_pr"] = stale()
        pr = self.fixture["stale_pr"]
        pr["labels"] = connection(labels("needs-author-action"))
        pr["timelineItems"]["nodes"].append({
            "__typename": "PullRequestCommit", "commit": {"oid": SHA, "committedDate": at(40)},
        })
        self.run_action(shell="handle_needs_author 1")
        posted = [c for c in self.calls if c[:2] == ["pr", "comment"]]
        self.assertEqual(len(posted), 1)
        persisted = json.loads((self.work / "fixture.json").read_text())
        body = persisted["stale_pr"]["comments"]["nodes"][-1]["body"]
        self.assertIn("<!-- pr-stale-author-action: observed", body)
        self.assertIn("Tracking the latest push for the stale sweep.", body)
        self.assertTrue(any(c[:2] == ["api", "graphql"] and any("minimizeComment" in a for a in c) and "id=IC_node4242" in c
                            for c in self.calls))

    def test_failing_to_minimize_the_marker_never_fails_the_sweep(self):
        for failure in ("minimize_failure", "node_lookup_failure"):
            with self.subTest(failure=failure):
                self.fixture["stale_pr"] = stale()
                pr = self.fixture["stale_pr"]
                pr["labels"] = connection(labels("needs-author-action"))
                pr["timelineItems"]["nodes"].append({
                    "__typename": "PullRequestCommit", "commit": {"oid": SHA, "committedDate": at(40)},
                })
                self.fixture[failure] = True
                result = self.run_action(shell="handle_needs_author 1")
                self.assertTrue(any(c[:2] == ["pr", "comment"] for c in self.calls))
                self.assertIn("::warning::could not", result.stdout + result.stderr)
                (self.work / "calls.jsonl").unlink(missing_ok=True)
                self.fixture.pop(failure)

    def test_observed_marker_dry_run_makes_no_calls_to_minimize(self):
        self.fixture["stale_pr"] = stale()
        pr = self.fixture["stale_pr"]
        pr["labels"] = connection(labels("needs-author-action"))
        pr["timelineItems"]["nodes"].append({
            "__typename": "PullRequestCommit", "commit": {"oid": SHA, "committedDate": at(40)},
        })
        self.run_action(dry=True, shell="handle_needs_author 1")
        self.assertEqual(self.mutations, [])
        self.assertFalse(any(any("minimizeComment" in a for a in c) for c in self.calls))


if __name__ == "__main__":
    unittest.main()
