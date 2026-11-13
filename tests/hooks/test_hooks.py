"""The safety hooks, run as Hermes runs them: the event as JSON on stdin, the decision as JSON on stdout."""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
M_HOOKS = ROOT / "profiles/maintainer/hooks"
C_HOOKS = ROOT / "profiles/contributor/hooks"
SHARED = ROOT / "container/hooks"


def run(cmd, event, env=None):
    full = {"PATH": os.environ["PATH"], **(env or {})}
    out = subprocess.run(cmd, input=json.dumps(event), capture_output=True, text=True, env=full, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout or "{}")


def blocked(decision):
    return decision.get("action") == "block"


class DangerousTools(unittest.TestCase):
    def call(self, tool):
        return run(["bash", str(M_HOOKS / "block-dangerous-tools.sh")], {"tool_name": tool, "tool_input": {}})

    def test_merge_delete_and_force_push_are_blocked(self):
        for tool in ("merge_pull_request", "github_merge", "delete_repo", "force_push_branch"):
            self.assertTrue(blocked(self.call(tool)), tool)

    def test_reading_tools_pass(self):
        for tool in ("get_pull_request", "list_issues", "search_pull_requests"):
            self.assertEqual(self.call(tool), {}, tool)


class MnemosyneTriples(unittest.TestCase):
    def call(self, predicate):
        return run(["bash", str(M_HOOKS / "block-mnemosyne-triples.sh")],
                   {"tool_name": "mnemosyne_triple_add", "tool_input": {"predicate": predicate}})

    def test_the_predicates_the_skills_write_pass(self):
        for p in ("caused_gate_failure", "gate_failure_opened", "gate_failure_closed", "excludes", "prioritizes"):
            self.assertEqual(self.call(p), {}, p)

    def test_anything_else_is_blocked(self):
        for p in ("", "likes", "caused_e2e_failure"):
            self.assertTrue(blocked(self.call(p)), p)

    def test_other_tools_are_not_this_hooks_business(self):
        out = run(["bash", str(M_HOOKS / "block-mnemosyne-triples.sh")], {"tool_name": "mnemosyne_recall", "tool_input": {}})
        self.assertEqual(out, {})


class SandboxTerminal(unittest.TestCase):
    def call(self, env):
        return run(["bash", str(SHARED / "require-sandbox-terminal.sh")], {"tool_name": "terminal", "tool_input": {}}, env)

    def test_blocked_outside_a_sandbox(self):
        self.assertTrue(blocked(self.call({})))

    def test_docker_needs_both_settings(self):
        self.assertTrue(blocked(self.call({"ASSISTANT_RUNTIME": "docker"})))
        self.assertTrue(blocked(self.call({"ASSISTANT_ALLOW_TERMINAL": "1"})))
        self.assertEqual(self.call({"ASSISTANT_RUNTIME": "docker", "ASSISTANT_ALLOW_TERMINAL": "1"}), {})

    def test_the_old_names_no_longer_open_it(self):
        self.assertTrue(blocked(self.call({"KYVERNO_RUNTIME": "docker", "KYVERNO_ALLOW_TERMINAL": "1"})))


class PushGuard(unittest.TestCase):
    def call(self, command, profile="maintainer"):
        home = os.path.join(tempfile.mkdtemp(), profile)
        os.makedirs(home)
        return run([sys.executable, "-I", str(SHARED / "git-push-guard.py")],
                   {"tool_name": "terminal", "tool_input": {"command": command}}, {"HERMES_HOME": home})

    def test_force_push_and_pushes_to_main_are_blocked(self):
        for c in ("git push --force fork x", "git push -f fork x", "git push fork main", "git push --delete fork x", "git push fork master"):
            self.assertTrue(blocked(self.call(c)), c)

    def test_a_plain_clone_and_an_unknown_remote_are_blocked(self):
        self.assertTrue(blocked(self.call("git clone https://github.com/example/repo.git")))
        self.assertTrue(blocked(self.call("git push https://example.com/x/y.git x")))

    def test_the_workspace_script_and_a_normal_push_to_the_fork_pass(self):
        self.assertEqual(self.call("assistant-workspace example/repo"), {})
        self.assertEqual(self.call("git push -u fork fix/x"), {})

    def test_only_a_maintainer_may_push_to_origin(self):
        self.assertEqual(self.call("git push origin fix/x", "maintainer"), {})
        self.assertTrue(blocked(self.call("git push origin fix/x", "contributor")))


class ContributorGuard(unittest.TestCase):
    def call(self, tool, tool_input=None):
        return run([sys.executable, "-I", str(C_HOOKS / "contributor-guard.py")], {"tool_name": tool, "tool_input": tool_input or {}})

    def test_merge_approve_review_and_delete_are_blocked(self):
        for tool in ("merge_pull_request", "approve_pull_request", "pull_request_review_write", "delete_branch", "force_push"):
            self.assertTrue(blocked(self.call(tool)), tool)


if __name__ == "__main__":
    unittest.main()
