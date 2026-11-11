#!/usr/bin/env python3
"""Offline tests for run.py: one case per rule, exemption and reset, plus config handling."""

from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("run", Path(__file__).resolve().parents[1] / "run.py")
lifecycle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lifecycle)

# A repository that triages with labels, as Kyverno does. The defaults leave the idle rule off.
KYVERNO = {
    "triage_label": "triage",
    "priority_prefix": "release-",
    "needs_info_labels": ["needs-info", "cannot reproduce", "waiting for status update"],
    "exempt_labels": ["no-stale", "hold", "security", "good first issue", "release-critical", "release-high"],
}
CFG = {**lifecycle.DEFAULTS, **KYVERNO}
NOW = datetime(2026, 11, 11, tzinfo=timezone.utc)
USER = {"login": "reporter", "__typename": "User"}
MAINT = {"login": "maint", "__typename": "User"}
BOT = {"login": "github-actions[bot]", "__typename": "Bot"}


def ago(days):
    return (NOW - timedelta(days=days)).isoformat()


def issue(labels=(), created=400, comments=(), events=(), milestone=None):
    """comments: (days_ago, author); events: ("label", name, days_ago) or ("ref", pr, merged_days_ago, closes)."""
    nodes = []
    for e in events:
        if e[0] == "label":
            nodes.append({"__typename": "LabeledEvent", "createdAt": ago(e[2]), "label": {"name": e[1]}})
        else:
            nodes.append({
                "__typename": "CrossReferencedEvent",
                "willCloseTarget": e[3] if len(e) > 3 else False,
                "source": {"number": e[1], "merged": True, "mergedAt": ago(e[2])},
            })
    return {
        "number": 1,
        "title": "t",
        "createdAt": ago(created),
        "author": USER,
        "labels": {"nodes": [{"name": n} for n in labels]},
        "milestone": milestone,
        "comments": {"nodes": [{"createdAt": ago(d), "author": a} for d, a in comments]},
        "timelineItems": {"nodes": nodes},
    }


def decide(i):
    return lifecycle.decide(i, NOW, CFG)


class WaitingOnReporter(unittest.TestCase):
    def test_warns_after_warning_days(self):
        acts = decide(issue(["needs-info"], events=[("label", "needs-info", 15)]))
        self.assertEqual([(a[0], a[1]) for a in acts], [("label", "stale")])

    def test_no_action_before_warning_days(self):
        self.assertEqual(decide(issue(["needs-info"], events=[("label", "needs-info", 5)])), [])

    def test_closes_after_stale_period(self):
        i = issue(["needs-info", "stale"], events=[("label", "needs-info", 40), ("label", "stale", 15)])
        self.assertEqual([a[0] for a in decide(i)], ["close"])

    def test_no_close_before_stale_period(self):
        i = issue(["needs-info", "stale"], events=[("label", "needs-info", 40), ("label", "stale", 5)])
        self.assertEqual(decide(i), [])

    def test_reporter_reply_resets(self):
        i = issue(["needs-info", "stale"], comments=[(3, USER)],
                  events=[("label", "needs-info", 40), ("label", "stale", 15)])
        self.assertEqual(decide(i), [("unlabel", "stale")])

    def test_maintainer_comment_is_not_a_reply(self):
        i = issue(["needs-info"], comments=[(10, MAINT)], events=[("label", "needs-info", 15)])
        self.assertEqual([a[0] for a in decide(i)], ["label"])

    def test_exempt_never_closes(self):
        i = issue(["needs-info", "stale", "release-high"],
                  events=[("label", "needs-info", 40), ("label", "stale", 15)])
        self.assertEqual(decide(i), [])


class PossiblyResolved(unittest.TestCase):
    def test_labels_after_grace(self):
        acts = decide(issue(events=[("ref", 99, 10)], created=30))
        self.assertEqual([(a[0], a[1]) for a in acts], [("label", "possibly-resolved")])

    def test_within_grace_period(self):
        self.assertEqual(decide(issue(events=[("ref", 99, 1)], created=30)), [])

    def test_closing_reference_ignored(self):
        self.assertEqual(decide(issue(events=[("ref", 99, 10, True)], created=30)), [])

    def test_comment_after_merge_blocks(self):
        self.assertEqual(decide(issue(events=[("ref", 99, 10)], created=30, comments=[(5, MAINT)])), [])

    def test_bot_comment_does_not_block(self):
        acts = decide(issue(events=[("ref", 99, 10)], created=30, comments=[(5, BOT)]))
        self.assertEqual(len(acts), 1)

    def test_comment_removes_label(self):
        i = issue(["possibly-resolved"], comments=[(2, USER)], events=[("label", "possibly-resolved", 5)])
        self.assertEqual(decide(i), [("unlabel", "possibly-resolved")])

    def test_never_closes(self):
        acts = decide(issue(events=[("ref", 99, 400)], created=500))
        self.assertTrue(all(a[0] != "close" for a in acts))


class IdleUntriaged(unittest.TestCase):
    def test_labels_idle_untriaged(self):
        acts = decide(issue(["triage"], created=200))
        self.assertEqual([(a[0], a[1]) for a in acts], [("label", "stale")])

    def test_recent_activity(self):
        self.assertEqual(decide(issue(["triage"], created=200, comments=[(10, MAINT)])), [])

    def test_priority_label_skips(self):
        self.assertEqual(decide(issue(["triage", "release-low"], created=400)), [])

    def test_milestone_skips(self):
        self.assertEqual(decide(issue(["triage"], created=400, milestone={"title": "Kyverno Release 1.20"})), [])

    def test_no_stale_exempts(self):
        self.assertEqual(decide(issue(["triage", "no-stale"], created=400)), [])

    def test_never_closes(self):
        acts = decide(issue(["triage", "stale"], created=900, events=[("label", "stale", 400)]))
        self.assertTrue(all(a[0] != "close" for a in acts))


class Config(unittest.TestCase):
    def load(self, content=None):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "issue-management.json"
            if content is not None:
                path.write_text(json.dumps(content))
            return lifecycle.load_config(path)

    def test_missing_file_uses_defaults(self):
        self.assertEqual(self.load(), lifecycle.DEFAULTS)

    def test_overrides_merge_over_defaults(self):
        cfg = self.load({"idle_days": 90})
        self.assertEqual((cfg["idle_days"], cfg["close_days"]), (90, 14))

    def test_rejects_unknown_key(self):
        with self.assertRaisesRegex(ValueError, "unknown config keys: idel_days"):
            self.load({"idel_days": 90})

    def test_rejects_bad_days(self):
        for bad in (0, -1, "14", True, 99999):
            with self.assertRaises(ValueError, msg=bad):
                self.load({"warning_days": bad})

    def test_rejects_unknown_comment_field(self):
        with self.assertRaisesRegex(ValueError, "warning_comment"):
            self.load({"warning_comment": "closes in {when}"})

    def test_rejects_empty_managed_label(self):
        with self.assertRaises(ValueError):
            self.load({"stale_label": ""})


class OptIn(unittest.TestCase):
    def test_idle_rule_off_by_default(self):
        i = issue(["triage"], created=400)
        self.assertEqual(lifecycle.decide(i, NOW, lifecycle.DEFAULTS), [])

    def test_defaults_do_not_exempt_priority_labels(self):
        i = issue(["needs-info", "stale", "release-high"], events=[("label", "needs-info", 40), ("label", "stale", 15)])
        self.assertEqual([a[0] for a in lifecycle.decide(i, NOW, lifecycle.DEFAULTS)], ["close"])


if __name__ == "__main__":
    unittest.main()
