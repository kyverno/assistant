"""The organisation's own pack, read from <owner>/.github at run time: the published image needs no build per organisation."""
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _load import FIXTURES, ROOT, contributor, repo_intel

repo_intel()
contributor()
from contributor import github, rules  # noqa: E402
from repo_intel import pack  # noqa: E402

KYVERNO = ROOT / "container/org/kyverno/org.yaml"
OTHER = (FIXTURES / "other-org.yaml").read_bytes()


class Env(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.saved = {k: os.environ.get(k) for k in ("HERMES_HOME", "ASSISTANT_REPO", "ASSISTANT_ORG_FILE")}
        os.environ["HERMES_HOME"] = self.home
        os.environ["ASSISTANT_REPO"] = "acme/api"
        os.environ.pop("ASSISTANT_ORG_FILE", None)
        pack._remote_seen.clear()
        rules._remote_seen.clear()
        self.old_image = (pack.IMAGE_PACK, rules.IMAGE_PACK)
        pack.IMAGE_PACK = rules.IMAGE_PACK = KYVERNO       # the published image: Kyverno's pack is baked in

    def tearDown(self):
        pack.IMAGE_PACK, rules.IMAGE_PACK = self.old_image
        for k, v in self.saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


class Maintainer(Env):
    def serve(self, files):
        calls = []

        def get(url):
            calls.append(url)
            name = url.rsplit("/", 1)[1]
            value = files.get(name)
            if isinstance(value, Exception):
                raise value
            return value
        pack._get = get
        self.addCleanup(lambda: setattr(pack, "_get", self._real))
        return calls

    _real = staticmethod(pack._get)

    def test_the_baked_pack_covers_only_its_own_organisation(self):
        self.serve({})
        self.assertEqual(pack.load(repo="kyverno/kyverno").gate.name, "e2e-gate")
        self.assertEqual(pack.load(repo="acme/api").gates, ())          # no Kyverno rules on someone else's repository

    def test_another_organisation_publishes_its_own(self):
        calls = self.serve({"assistant-org.yaml": OTHER, "assistant-org-facts.md": b"Release branches are named rel-N."})
        rules_ = pack.load(repo="acme/api")
        self.assertEqual(rules_.gate.name, "release-gate")
        self.assertIn("rel-N", rules_.facts)
        self.assertTrue(all("/repos/acme/.github/contents/" in c for c in calls))
        n = len(calls)
        pack.load(repo="acme/api")
        self.assertEqual(len(calls), n)                                  # remembered, not fetched on every call

    def test_an_organisation_with_no_pack_gets_none(self):
        self.serve({})
        self.assertEqual(pack.load(repo="acme/api").source, None)

    def test_an_unreadable_pack_is_an_error_not_no_pack(self):
        self.serve({"assistant-org.yaml": pack.PackError("could not read: HTTP 403")})
        with self.assertRaises(pack.PackError):
            pack.load(repo="acme/api")

    def test_the_last_good_copy_is_used_when_github_cannot_be_reached(self):
        self.serve({"assistant-org.yaml": OTHER})
        pack.load(repo="acme/api")
        pack._remote_seen.clear()
        self.serve({"assistant-org.yaml": pack.PackError("timed out")})
        self.assertEqual(pack.load(repo="acme/api").gate.name, "release-gate")

    def test_a_broken_published_pack_names_the_problem(self):
        self.serve({"assistant-org.yaml": b"maintainer:\n  nope: 1\n"})
        with self.assertRaises(pack.PackError):
            pack.load(repo="acme/api")

    def test_removing_the_pack_removes_the_cache(self):
        self.serve({"assistant-org.yaml": OTHER})
        pack.load(repo="acme/api")
        pack._remote_seen.clear()
        self.serve({})
        self.assertEqual(pack.load(repo="acme/api").source, None)

    def test_an_image_built_for_one_organisation_without_a_list_applies_everywhere(self):
        p = pathlib.Path(self.home) / "org.yaml"
        p.write_bytes(OTHER)
        pack.IMAGE_PACK = p
        self.serve({})
        self.assertEqual(pack.load(repo="whoever/anything").gate.name, "release-gate")


class FakeClient:
    def __init__(self, files):
        self.files = files

    def rest(self, path):
        import base64
        name = path.rsplit("/", 1)[1]
        value = self.files.get(name)
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise github.GitHubError(f"GitHub 404 for {path}: Not Found")
        return {"encoding": "base64", "size": len(value), "content": base64.b64encode(value).decode()}


class Contributor(Env):
    def use(self, files):
        client = FakeClient(files)
        real = github.Client
        github.Client = lambda *a, **k: client
        self.addCleanup(lambda: setattr(github, "Client", real))

    def test_the_baked_pack_covers_only_its_own_organisation(self):
        self.use({})
        self.assertEqual(rules.load_org(repo="kyverno/kyverno")["name"], "kyverno")
        with self.assertRaises(rules.OrgFileError) as ctx:
            rules.load_org(repo="acme/api")
        self.assertIn("acme/.github/assistant-org.yaml", str(ctx.exception))

    def test_another_organisation_publishes_its_own(self):
        text = KYVERNO.read_text().replace("name: kyverno", "name: acme").replace("repositories:", "x_unused:")
        self.use({"assistant-org.yaml": text.encode()})
        # a pack with a different shape is read the same way; this one only has to satisfy the required keys
        try:
            org = rules.load_org(repo="acme/api")
        except rules.OrgFileError as exc:
            self.fail(str(exc))
        self.assertEqual(org["name"], "acme")

    def test_an_unreadable_pack_is_an_error_not_no_pack(self):
        self.use({"assistant-org.yaml": github.GitHubError("GitHub 403 for contents: forbidden")})
        with self.assertRaises(rules.OrgFileError):
            rules.load_org(repo="acme/api")


if __name__ == "__main__":
    unittest.main()
