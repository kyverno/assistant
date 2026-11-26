"""How close `assistant init` gets to the pack we wrote by hand for Kyverno, on a saved copy of the real repository.

The snapshot (tests/fixtures/kyverno-snapshot.json.gz) is the file list, labels, CODEOWNERS and workflow headers of
kyverno/kyverno on 2026-11-15, saved with `container/assistant-init kyverno/kyverno --dump`. The score is computed over every
path in the repository: a path counts as right when the proposed pack classifies it as the hand-written pack does. The floors
are the measured result minus a small margin, so a change that makes the proposal worse fails here.
"""
import gzip
import importlib.machinery
import importlib.util
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _load import FIXTURES, ROOT, engine

engine()
import yaml  # noqa: E402
from repo_engine import pack  # noqa: E402

loader = importlib.machinery.SourceFileLoader("assistant_init_k", str(ROOT / "container" / "assistant-init"))
init = importlib.util.module_from_spec(importlib.util.spec_from_loader("assistant_init_k", loader))
sys.modules["assistant_init_k"] = init
loader.exec_module(init)

with gzip.open(FIXTURES / "kyverno-snapshot.json.gz", "rt") as f:
    SNAP = json.load(f)
PROPOSAL = init.from_snapshot(SNAP)
MINE = pack.parse(yaml.safe_load(init.render("kyverno", init.inferred(PROPOSAL))), "proposed")
REAL = pack.load(str(ROOT / "container/org/kyverno/org.yaml"))


def score(cls):
    real = {p for p in SNAP["paths"] if REAL.classify(p) == cls}
    mine = {p for p in SNAP["paths"] if MINE.classify(p) == cls}
    both = real & mine
    return len(both) / max(1, len(real)), len(both) / max(1, len(mine)), len(real)


class AgainstTheHandWrittenPack(unittest.TestCase):
    def test_generated_files(self):
        recall, precision, n = score("GENERATED")
        print(f"\ngenerated: recall {recall:.2f} precision {precision:.2f} over {n} files")
        self.assertGreater(n, 400)
        self.assertGreaterEqual(recall, 0.93)
        self.assertGreaterEqual(precision, 0.92)

    def test_api_surface(self):
        recall, precision, n = score("API_SURFACE")
        print(f"api surface: recall {recall:.2f} precision {precision:.2f} over {n} files")
        self.assertGreaterEqual(recall, 0.95)
        self.assertGreaterEqual(precision, 0.85)

    def test_test_files(self):
        recall, precision, n = score("TEST_ONLY")
        print(f"test files: recall {recall:.2f} precision {precision:.2f} over {n} files")
        self.assertGreaterEqual(recall, 0.95)
        self.assertGreaterEqual(precision, 0.90)

    def test_a_generated_folder_is_found_from_its_headers_alone(self):
        found = {c.glob: c.detail for c in PROPOSAL.generated}
        self.assertIn("pkg/client/**", found)
        self.assertIn("131 of 131", found["pkg/client/**"])

    def test_the_guides_labels_owners_and_bots_match(self):
        self.assertEqual(MINE.review_bots, REAL.review_bots)
        self.assertEqual(MINE.codeowners, REAL.codeowners)
        self.assertTrue({"AGENTS.md", "ARCHITECTURE.md", "CONTRIBUTING.md"} <= set(MINE.agent_docs))

    def test_the_draft_of_the_ci_facts_names_the_real_workflows(self):
        facts = PROPOSAL.ci_facts
        before, after = facts.split("Runs on a pull request")[1].split("Runs only after merge")[0], facts.split("Runs only after merge")[1]
        self.assertIn("check-codegen.yaml", before)
        self.assertIn("images-publish.yaml", after)
        self.assertNotIn("check-codegen.yaml", after)
        self.assertIn("check-tests.yaml", facts.split("Reusable workflows")[1])      # the post-merge suites hide behind it

    def test_what_it_does_not_claim(self):
        # The hand-written pack's gate, critical code and milestones are not inferred, and the proposal does not pretend to.
        self.assertEqual(MINE.gates, ())
        self.assertEqual(MINE.critical, ())
        self.assertEqual(MINE.milestone_title_format, None)


if __name__ == "__main__":
    unittest.main()
