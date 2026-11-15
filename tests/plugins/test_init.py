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

    def test_codeowners_gives_folders_never_everything(self):
        dirs = {"pkg", "pkg/engine"}
        text = "* @org/all\n/pkg/engine/ @org/eng\n/pkg/engine @org/eng2\n*.md @docs\n# comment\n"
        got = init.codeowner_globs(text, dirs)
        self.assertEqual([g for g, _ in got], ["pkg/engine/**", "pkg/engine/**"])

    def test_the_proposal_only_offers_what_the_repository_has(self):
        p = init.propose("o/r", TREE, "", "/pkg/engine/ @org/eng\n", ["ready-for-review", "needs-author-action", "bug", "lgtm"])
        self.assertEqual({c.glob for c in p.generated}, {"**/zz_generated.*", "**/dist/**", "package-lock.json", "**/crds/**"})
        self.assertEqual([c.glob for c in p.api_surface], ["api/**"])
        self.assertEqual({c.glob for c in p.test_only}, {"**/*_test.go", "**/*.test.ts"})   # tests/a.test.ts is already covered
        self.assertEqual([(c.glob, c.picked) for c in p.critical], [("pkg/engine/**", False)])   # owning is not riskiness
        self.assertEqual({c.glob for c in p.ready_labels}, {"ready-for-review", "lgtm"})
        self.assertEqual([c.glob for c in p.attention_labels], ["needs-author-action"])

    def test_an_empty_repository_gives_an_empty_proposal(self):
        p = init.propose("o/r", [], "", "", [])
        self.assertEqual((p.generated, p.api_surface, p.critical, p.test_only, p.ready_labels), ([], [], [], [], []))


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

    def test_nothing_chosen_is_still_a_valid_pack(self):
        pack.parse(__import__("yaml").safe_load(init.render("o", {})), "test")


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

    def test_a_selects_all_and_n_none(self):
        self.assertEqual(drive([b"a", ENTER], MULTI), ["a", "b", "c"])
        self.assertEqual(drive([b"n", ENTER], MULTI), [])

    def test_a_single_choice_follows_the_arrows(self):
        self.assertEqual(drive([DOWN, DOWN, ENTER], SINGLE), "three")
        self.assertEqual(drive([DOWN, DOWN, UP, ENTER], SINGLE), "two")

    def test_j_and_k_also_move(self):
        self.assertEqual(drive([b"j", ENTER], SINGLE), "two")

    def test_the_cursor_stops_at_the_ends(self):
        self.assertEqual(drive([UP, ENTER], SINGLE), "one")
        self.assertEqual(drive([DOWN] * 6 + [ENTER], SINGLE), "three")

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
