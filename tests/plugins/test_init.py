"""`assistant init`: what it infers from a repository, the pack it writes, and the terminal menus."""
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import pty
import re
import select
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _load import ROOT, repo_intel

repo_intel()
from repo_intel import pack  # noqa: E402

SCRIPT = ROOT / "container" / "assistant-init"
loader = importlib.machinery.SourceFileLoader("assistant_init", str(SCRIPT))
init = importlib.util.module_from_spec(importlib.util.spec_from_loader("assistant_init", loader))
sys.modules["assistant_init"] = init           # dataclasses look their module up here
loader.exec_module(init)

TREE = ["api/v1/types.go", "api/v1/zz_generated.deepcopy.go", "pkg/engine/run.go", "pkg/engine/run_test.go",
        "pkg/util/x.go", "dist/cli/index.js", "package-lock.json", "tests/a.test.ts", "docs/a.md", "src/app.ts",
        "config/crds/a.yaml", "README.md"]


class Inference(unittest.TestCase):
    def test_globs_follow_the_pack_language(self):
        self.assertEqual(init.matches("**/zz_generated.*", TREE), 1)
        self.assertEqual(init.matches("**/dist/**", TREE), 1)          # `**/` matches zero directories too
        self.assertEqual(init.matches("pkg/engine/**", TREE), 2)
        self.assertEqual(init.matches("*.md", TREE), 1)                # `*` stays inside one directory

    def test_the_repository_marks_its_own_generated_files(self):
        self.assertEqual(init.attribute_globs("docs/gen/** linguist-generated\n*.pb.go linguist-generated=true\n*.go text\n"),
                         ["docs/gen/**", "*.pb.go"])

    def test_the_proposal_only_offers_what_the_repository_has(self):
        p = init.propose("o/r", TREE, "", "/pkg/engine/ @org/eng\n", ["ready-for-review", "needs-author-action", "bug", "lgtm"])
        self.assertEqual({c.glob for c in p.generated}, {"**/zz_generated.*", "**/dist/**", "package-lock.json", "**/crds/**"})
        self.assertEqual([c.glob for c in p.api_surface], ["api/**"])
        self.assertEqual({c.glob for c in p.test_only}, {"**/*_test.go", "**/*.test.ts"})   # tests/a.test.ts is already covered
        self.assertEqual([(c.glob, c.picked) for c in p.critical], [("pkg/engine/**", False)])   # owning is not riskiness
        self.assertEqual({c.glob for c in p.ready_labels}, {"ready-for-review"})
        self.assertEqual([c.glob for c in p.attention_labels], ["needs-author-action"])


CTX = ["AGENTS.md", "CLAUDE.md", "RELEASING.md", "pkg/a/AGENTS.md", ".github/instructions/go.instructions.md",
       ".github/CODEOWNERS", ".github/labels.yml", ".github/pr-readiness-checks.yml", "src/x.ts", "README.md", "SECURITY.md"]


class Context(unittest.TestCase):
    def setUp(self):
        self.p = init.propose("o/r", CTX, "", "", ["area/docs", "kind/bug", "ready-for-review"])

    def test_the_repositorys_own_guides_are_offered(self):
        self.assertEqual({c.glob for c in self.p.agent_docs},
                         {"AGENTS.md", "**/AGENTS.md", "CLAUDE.md", "RELEASING.md", ".github/instructions/*.instructions.md"})

    def test_where_it_keeps_its_owners_labels_and_checks(self):
        self.assertEqual((self.p.codeowners_file, self.p.labels_source, self.p.checks_file, self.p.instructions_dir),
                         (".github/CODEOWNERS", ".github/labels.yml", ".github/pr-readiness-checks.yml", ".github/instructions"))
        self.assertEqual((self.p.area_prefixes, self.p.kind_prefixes), (["area/"], ["kind/"]))

    def test_the_full_pack_is_valid(self):
        chosen = {**init.inferred(self.p), "review_bots": ["copilot-pull-request-reviewer"]}
        rules = pack.parse(__import__("yaml").safe_load(init.render("o", chosen)), "test")
        self.assertEqual(rules.codeowners, ".github/CODEOWNERS")
        self.assertIn("RELEASING.md", rules.agent_docs)
        self.assertEqual(rules.review_bots, ("copilot-pull-request-reviewer",))

    def test_a_shared_pack_keeps_a_single_value_only_where_repositories_agree(self):
        merged = init.merge([{"codeowners": ".github/CODEOWNERS", "agent_docs": ["A"]}, {"codeowners": "CODEOWNERS", "agent_docs": ["B"]}])
        self.assertEqual((merged["codeowners"], merged["agent_docs"]), (None, ["A", "B"]))

    def test_what_it_could_not_infer_is_always_said(self):
        said = " ".join(init.not_inferred(self.p, "repo"))
        for word in ("gates", "Critical", "release process"):
            self.assertIn(word, said)


class Flow(unittest.TestCase):
    def test_an_existing_shared_pack_is_reported_before_any_question(self):
        calls = []
        saved = (init.load, init.api, init.publish)
        init.load = lambda repo: init.propose(repo, ["src/x.ts"], "", "", [])
        init.api = lambda path, raw=False: {"name": init.PACK} if path.endswith("/.github/contents/" + init.PACK) else None
        init.publish = lambda *a, **k: calls.append(a)
        try:
            self.assertEqual(init.main(["o/r", "--location", "org", "--yes"]), 1)
        finally:
            init.load, init.api, init.publish = saved
        self.assertEqual(calls, [])

    def _run(self, argv):
        calls = []
        saved = (init.load, init.api, init.publish, init.interactive)
        init.load = lambda repo: init.propose(repo, ["src/x.ts"], "", "", [])
        init.api = lambda path, raw=False: None
        init.publish = lambda *a, **k: calls.append(a) or "https://example/pr/1"
        init.interactive = lambda: False
        try:
            code = init.main(argv)
        finally:
            init.load, init.api, init.publish, init.interactive = saved
        return code, calls

    def test_a_single_repository_is_told_about_the_shared_pack_it_would_override(self):
        calls = []
        saved = (init.load, init.api, init.publish)
        init.load = lambda repo: init.propose(repo, ["src/x.ts"], "", "", [])
        init.api = lambda path, raw=False: {"name": init.PACK} if path.endswith("/.github/contents/" + init.PACK) else None
        init.publish = lambda *a, **k: calls.append(a)
        try:
            self.assertEqual(init.main(["o/r", "--yes"]), 1)
        finally:
            init.load, init.api, init.publish = saved
        self.assertEqual(calls, [])

    def test_without_a_terminal_and_without_yes_nothing_is_published(self):
        self.assertEqual(self._run(["o/r"]), (0, []))

    def test_yes_opens_the_pull_request(self):
        code, calls = self._run(["o/r", "--yes"])
        self.assertEqual((code, len(calls)), (0, 1))

    def test_saving_here_never_overwrites_a_file(self):
        import contextlib, io, os, tempfile
        saved = init.choose
        init.choose = lambda *a, **k: init.Option("", value="file")
        old = os.getcwd()
        with tempfile.TemporaryDirectory() as d:
            os.chdir(d)
            try:
                Path(init.PACK).write_text("mine")
                with contextlib.redirect_stderr(io.StringIO()):
                    code, _ = self._run_interactive(["o/r", "--location", "repo"])
                self.assertEqual((code, Path(init.PACK).read_text()), (1, "mine"))
            finally:
                os.chdir(old)
                init.choose = saved

    def _run_interactive(self, argv):
        saved = (init.load, init.api, init.interactive, init.confirm)
        init.load = lambda repo: init.propose(repo, ["src/x.ts"], "", "", [])
        init.api = lambda path, raw=False: None
        init.interactive = lambda: True
        init.confirm = lambda *a, **k: False
        saved_review = init.review
        init.review = lambda p: init.inferred(p)
        try:
            return init.main(argv), None
        finally:
            init.load, init.api, init.interactive, init.confirm = saved
            init.review = saved_review


class Setup(unittest.TestCase):
    """`assistant setup`: say what is missing, and run nothing without a yes."""
    def _with(self, files, prs=None, tty=False):
        calls = []
        saved = (init.api, init.open_pr, init.interactive, init.main, init.subprocess.run)
        init.api = lambda path, raw=False: files.get(path)
        init.open_pr = lambda repo, branch: (prs or {}).get(branch, "")
        init.interactive = lambda: tty
        init.main = lambda argv: calls.append(("pack", argv)) or 0
        init.subprocess.run = lambda *a, **k: calls.append(("ci", a)) or type("R", (), {"returncode": 0})()
        return calls, saved

    def _restore(self, saved):
        init.api, init.open_pr, init.interactive, init.main, init.subprocess.run = saved

    def test_it_reports_what_is_missing_and_runs_nothing_without_a_terminal(self):
        calls, saved = self._with({})
        try:
            self.assertEqual(init.setup("o/r"), 0)
            self.assertEqual(init.setup_status("o/r"), {"pack": "", "ci": ""})
        finally:
            self._restore(saved)
        self.assertEqual(calls, [])

    def test_a_repository_with_both_is_left_alone(self):
        calls, saved = self._with({f"repos/o/r/contents/.github/{init.PACK}": {"name": "x"},
                                   "repos/o/r/contents/.github/workflows": [{"name": "pr-management.yaml"}]}, tty=True)
        try:
            self.assertEqual(init.setup("o/r"), 0)
        finally:
            self._restore(saved)
        self.assertEqual(calls, [])

    def test_an_open_pull_request_counts_and_is_not_offered_again(self):
        calls, saved = self._with({}, prs={init.PACK_BRANCH: "https://x/pr/1", init.CI_BRANCH: "https://x/pr/2"}, tty=True)
        try:
            self.assertEqual(init.setup("o/r"), 0)
        finally:
            self._restore(saved)
        self.assertEqual(calls, [])

    def test_the_owners_shared_pack_counts(self):
        calls, saved = self._with({f"repos/o/.github/contents/{init.PACK}": {"name": "x"}})
        try:
            self.assertEqual(init.setup_status("o/r")["pack"], "found")
        finally:
            self._restore(saved)

    def test_the_reviewers_team_comes_from_the_organisations_own_codeowners(self):
        calls, saved = self._with({"repos/o/r/contents/CODEOWNERS": "# @o/commented\n*.md @alice @Other/x @O/docs-team\n"})
        init.api = lambda path, raw=False: "# @o/commented\n*.md @alice @Other/x @O/docs-team\n" if path.endswith("/CODEOWNERS") and "github" not in path else None
        try:
            self.assertEqual(init.codeowner_team("o/r"), "@O/docs-team")
        finally:
            self._restore(saved)


class Rendering(unittest.TestCase):
    def test_what_it_writes_is_a_valid_pack(self):
        p = init.propose("o/r", TREE, "", "", ["ready-for-review", "needs-author-action"])
        text = init.render("o", init.inferred(p))
        rules = pack.parse(__import__("yaml").safe_load(text), "test")
        self.assertEqual(rules.classify("api/v1/zz_generated.deepcopy.go"), "GENERATED")
        self.assertEqual(rules.classify("api/v1/types.go"), "API_SURFACE")
        self.assertEqual(rules.classify("pkg/engine/run_test.go"), "TEST_ONLY")
        self.assertEqual(rules.ready_labels, ("ready-for-review",))

    def test_awkward_characters_survive(self):
        text = init.render('a"b', {"critical": ['x/"y"/**', "with space/**"], "ready_labels": ["ready: now"]})
        data = __import__("yaml").safe_load(text)
        self.assertEqual(data["name"], 'a"b')
        self.assertEqual(data["maintainer"]["file_classes"]["critical"], ['x/"y"/**', "with space/**"])
        pack.parse(data, "test")


def drive(keys, call):
    """Run `call` (Python source using `init`) in a real terminal, send `keys`, return what it printed as its last line."""
    code = ("import importlib.machinery, importlib.util, json, sys\n"
            f"l = importlib.machinery.SourceFileLoader('i', {str(SCRIPT)!r})\n"
            "i = importlib.util.module_from_spec(importlib.util.spec_from_loader('i', l)); sys.modules['i'] = i; l.exec_module(i)\n" + call)
    master, slave = pty.openpty()
    proc = subprocess.Popen([sys.executable, "-c", code], stdin=slave, stdout=slave, stderr=slave, close_fds=True,
                            env={**os.environ, "NO_COLOR": "1"})
    os.close(slave)
    out = b""

    def pump(until=None, quiet=0.15, limit=10.0):
        """Read what the program printed, until `until` appears or it has been quiet for a moment."""
        nonlocal out
        deadline, last = time.time() + limit, time.time()
        while time.time() < deadline:
            if until and until in out:
                return
            if select.select([master], [], [], 0.05)[0]:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    return
                if not chunk:
                    return
                out += chunk
                last = time.time()
                if until and until in out:
                    return
            elif not until and time.time() - last > quiet and out:
                return
            elif proc.poll() is not None:
                return

    try:
        pump(until=b"pick")               # the menu is up and reading keys only after it has drawn itself
        pump(until=b"enter")
        for k in keys:
            os.write(master, k)
            pump()
        pump(until=b"RESULT", limit=5)
    finally:
        proc.kill()
        os.close(master)
    found = re.findall(r"RESULT (.*)", out.decode("utf-8", "replace").replace("\r", ""))
    return json.loads(found[-1]) if found else out.decode("utf-8", "replace")


UP, DOWN, SPACE, ENTER = b"\x1b[A", b"\x1b[B", b" ", b"\r"
MULTI = ("opts = [i.Option('a'), i.Option('b', checked=True), i.Option('c')]\n"
         "r = i.menu('pick', opts, multi=True)\nprint('RESULT ' + json.dumps([o.label for o in r]))\n")
SINGLE = ("opts = [i.Option('one'), i.Option('two'), i.Option('three')]\n"
          "r = i.choose('pick', opts)\nprint('RESULT ' + json.dumps(r.label))\n")


class Menus(unittest.TestCase):
    def test_arrow_keys_and_space_toggle_boxes(self):
        # start on a; space ticks a; down to b; space unticks b; down to c; space ticks c
        self.assertEqual(drive([SPACE, DOWN, SPACE, DOWN, SPACE, ENTER], MULTI), ["a", "c"])

    def test_the_preselection_is_kept_when_nothing_is_touched(self):
        self.assertEqual(drive([ENTER], MULTI), ["b"])

    def test_filtering_narrows_a_long_list(self):
        many = ("opts = [i.Option(f'label-{n}') for n in range(30)]\n"
                "r = i.menu('pick', opts, multi=True)\nprint('RESULT ' + json.dumps([o.label for o in r]))\n")
        self.assertEqual(drive([b"/", b"2", b"7", b"\r", SPACE, ENTER], many), ["label-27"])

    def test_without_a_terminal_it_takes_the_defaults(self):
        opts = [init.Option("a"), init.Option("b", checked=True)]
        self.assertEqual([o.label for o in init.menu("t", opts, multi=True)], ["b"])
        self.assertEqual(init.choose("t", [init.Option("x"), init.Option("y")]).label, "x")


if __name__ == "__main__":
    unittest.main()
