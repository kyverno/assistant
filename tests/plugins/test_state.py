"""Where the two profiles keep their state, and that the contributor reads the organisation file."""
import importlib.util
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _load import ROOT, contributor

DASH = ROOT / "profiles/maintainer/plugins/maintainer-dashboard"


def load_store():
    spec = importlib.util.spec_from_file_location("maintainer_dashboard", DASH / "__init__.py", submodule_search_locations=[str(DASH)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["maintainer_dashboard"] = module
    spec.loader.exec_module(module)
    from maintainer_dashboard import store
    return store


class WithHome(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self._saved = {k: os.environ.get(k) for k in ("HERMES_HOME", "ASSISTANT_CONTRIBUTOR_HOME", "ASSISTANT_ORG_FILE")}
        os.environ["HERMES_HOME"] = self.home
        os.environ.pop("ASSISTANT_CONTRIBUTOR_HOME", None)

    def tearDown(self):
        for k, v in self._saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


class DashboardState(WithHome):
    def test_state_lives_under_maintainer_dashboard(self):
        store = load_store()
        self.assertEqual(store.state_path(), pathlib.Path(self.home) / "maintainer-dashboard" / "state.json")

    def test_an_update_is_kept_and_read_back(self):
        store = load_store()
        store.update("items", {"items": [{"key": "pr:1", "kind": "pr", "number": 1, "title": "Fix x", "verdict": "review"}]})
        self.assertIn("pr:1", store.load()["items"])
        self.assertTrue(store.state_path().is_file())

    def test_the_pre_rename_folder_is_not_read(self):
        old = pathlib.Path(self.home) / "kyverno-dashboard"
        old.mkdir()
        (old / "state.json").write_text('{"items": {"pr:9": {}}}')
        self.assertEqual(load_store().load()["items"], {})


class ContributorState(WithHome):
    def test_journey_lives_under_contributor_state(self):
        contributor()
        from contributor import journey
        self.assertEqual(journey.path(), pathlib.Path(self.home) / "contributor-state" / "journey.json")

    def test_the_home_can_be_set_explicitly(self):
        contributor()
        from contributor import journey
        os.environ["ASSISTANT_CONTRIBUTOR_HOME"] = self.home + "/elsewhere"
        self.assertEqual(journey.path(), pathlib.Path(self.home) / "elsewhere" / "journey.json")

    def test_a_note_is_kept(self):
        contributor()
        from contributor import journey
        journey.add_note("struggle", "could not find where the webhook is registered", area="webhooks")
        self.assertEqual(journey.load()["notes"][-1]["area"], "webhooks")


class ContributorOrgFile(WithHome):
    def test_the_shipped_pack_satisfies_the_contributor(self):
        contributor()
        from contributor import rules
        org = rules.load_org(str(ROOT / "container/org/kyverno/org.yaml"))
        for key in rules.REQUIRED:
            self.assertIn(key, org)

    def test_a_pack_missing_a_required_key_is_refused(self):
        contributor()
        from contributor import rules
        p = pathlib.Path(self.home) / "org.yaml"
        p.write_text("name: x\n")
        with self.assertRaises(rules.OrgFileError):
            rules.load_org(str(p))

    def test_the_old_environment_variable_is_not_read(self):
        contributor()
        from contributor import rules
        os.environ.pop("ASSISTANT_ORG_FILE", None)
        os.environ["KYVERNO_ORG_FILE"] = "/nonexistent"
        try:
            self.assertNotIn(pathlib.Path("/nonexistent"), rules.candidate_paths())
        finally:
            os.environ.pop("KYVERNO_ORG_FILE", None)


if __name__ == "__main__":
    unittest.main()
