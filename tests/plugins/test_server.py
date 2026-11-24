"""The engine's MCP server: the protocol it speaks, the tools it lists, and how the maintainer profile starts it."""
import io
import json
import os
import re
import subprocess
import sys
import unittest
from unittest import mock

import yaml

from _load import ROOT, engine

engine()
from repo_engine import TOOLS, server  # noqa: E402

CONFIG = yaml.safe_load((ROOT / "profiles/maintainer/config.yaml").read_text())


def rpc(method, params=None, id_=1):
    m = {"jsonrpc": "2.0", "method": method}
    if id_ is not None:
        m["id"] = id_
    if params is not None:
        m["params"] = params
    return m


class Protocol(unittest.TestCase):
    def test_initialize_agrees_a_version(self):
        r = server.handle(rpc("initialize", {"protocolVersion": "2025-06-18"}))["result"]
        self.assertEqual(r["protocolVersion"], "2025-06-18")
        self.assertEqual(r["serverInfo"]["name"], "repo-intel")
        self.assertIn("tools", r["capabilities"])
        r = server.handle(rpc("initialize", {"protocolVersion": "1999-01-01"}))["result"]
        self.assertEqual(r["protocolVersion"], server.PROTOCOLS[0])

    def test_notifications_get_no_reply(self):
        self.assertIsNone(server.handle(rpc("notifications/initialized", id_=None)))

    def test_unknown_method_and_bad_request(self):
        self.assertEqual(server.handle(rpc("resources/list"))["error"]["code"], -32601)
        self.assertEqual(server.handle({"id": 3, "method": "ping"})["error"]["code"], -32600)
        self.assertEqual(server.handle(rpc("ping"))["result"], {})

    def test_params_that_are_not_an_object_are_rejected_and_the_server_goes_on(self):
        self.assertEqual(server.handle(rpc("tools/list", []))["error"]["code"], -32602)
        self.assertEqual(server.handle(rpc("initialize", ["2025-06-18"]))["error"]["code"], -32602)
        out = io.StringIO()
        lines = [rpc("tools/list", [], id_=1), rpc("ping", id_=2)]
        server.serve(io.StringIO("".join(json.dumps(m) + "\n" for m in lines)), out)
        replies = [json.loads(x) for x in out.getvalue().splitlines()]
        self.assertEqual([r["id"] for r in replies], [1, 2])
        self.assertEqual(replies[0]["error"]["code"], -32602)
        self.assertEqual(replies[1]["result"], {})

    def test_an_unexpected_failure_is_an_internal_error_not_a_crash(self):
        out = io.StringIO()
        lines = [rpc("ping", id_=1), rpc("ping", id_=2)]
        real = server.handle
        calls = []

        def flaky(m):
            calls.append(m)
            if len(calls) == 1:
                raise KeyError("x")
            return real(m)
        with mock.patch.object(server, "handle", flaky), mock.patch("sys.stderr", io.StringIO()):
            server.serve(io.StringIO("".join(json.dumps(m) + "\n" for m in lines)), out)
        replies = [json.loads(x) for x in out.getvalue().splitlines()]
        self.assertEqual(replies[0], {"jsonrpc": "2.0", "id": 1, "error": {"code": -32603, "message": "internal error"}})
        self.assertEqual(replies[1]["result"], {})

    def test_the_host_installer_points_the_server_at_the_interpreter_it_picked(self):
        # install.sh shadows python3 with a function that runs $PY, so `command -v python3` would write a bare
        # "python3" into the profile, which may lack PyYAML.
        text = (ROOT / "scripts/install.sh").read_text()
        self.assertIn('python3 - "$PROFILE_DIR/config.yaml" "$PY" "$REPO_ROOT/engine"', text)
        self.assertNotIn("command -v python3", text)

    def test_tools_list_matches_the_engine_and_the_profile_allowlist(self):
        tools = server.handle(rpc("tools/list"))["result"]["tools"]
        names = [t["name"] for t in tools]
        self.assertEqual(names, [s["name"] for s, _ in TOOLS])
        self.assertEqual(sorted(names), sorted(CONFIG["mcp_servers"]["repo-intel"]["tools"]["include"]))
        for t in tools:
            self.assertEqual(t["inputSchema"]["type"], "object")
            self.assertTrue(t["description"])


class Calls(unittest.TestCase):
    def call(self, name, args=None):
        return server.handle(rpc("tools/call", {"name": name, "arguments": args or {}}))

    def test_unknown_tool_is_a_protocol_error(self):
        self.assertEqual(self.call("merge_pull_request")["error"]["code"], -32602)

    def test_a_tool_that_reports_failure_is_marked_as_an_error(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            r = self.call("queue")["result"]
        self.assertTrue(r["isError"])
        self.assertFalse(json.loads(r["content"][0]["text"])["success"])

    def test_a_tool_that_raises_gives_a_short_error_and_keeps_the_server_up(self):
        def boom(_args):
            raise RuntimeError("no")
        with mock.patch.dict(server._BY_NAME, {"queue": (TOOLS[0][0], boom)}), \
                mock.patch("sys.stderr", io.StringIO()):
            r = self.call("queue")["result"]
        self.assertTrue(r["isError"])
        self.assertIn("RuntimeError: no", r["content"][0]["text"])

    def test_a_tool_that_prints_does_not_corrupt_the_stream(self):
        def chatty(_args):
            print("noise")
            return json.dumps({"success": True})
        out, err = io.StringIO(), io.StringIO()
        line = json.dumps(rpc("tools/call", {"name": "queue", "arguments": {}})) + "\n"
        with mock.patch.dict(server._BY_NAME, {"queue": (TOOLS[0][0], chatty)}), mock.patch("sys.stderr", err):
            server.serve(io.StringIO(line), out)
        self.assertEqual(len(out.getvalue().splitlines()), 1)
        self.assertFalse(json.loads(out.getvalue())["result"]["isError"])
        self.assertIn("noise", err.getvalue())

    def test_parse_errors_and_batches(self):
        out = io.StringIO()
        batch = json.dumps([rpc("ping", id_=1), rpc("notifications/initialized", id_=None), rpc("ping", id_=2)])
        server.serve(io.StringIO("not json\n\n" + batch + "\n"), out)
        lines = [json.loads(x) for x in out.getvalue().splitlines()]
        self.assertEqual(lines[0]["error"]["code"], -32700)
        self.assertEqual([r["id"] for r in lines[1]], [1, 2])


class Environment(unittest.TestCase):
    def test_an_unset_variable_left_as_written_counts_as_unset(self):
        env = {"ASSISTANT_REPO": "${ASSISTANT_REPO}", "HTTPS_PROXY": "http://10.0.0.1:3128", "X": "a ${B} c"}
        server.scrub_env(env)
        self.assertEqual(env, {"HTTPS_PROXY": "http://10.0.0.1:3128", "X": "a ${B} c"})

    def test_the_profile_passes_what_the_engine_reads(self):
        s = CONFIG["mcp_servers"]["repo-intel"]
        for var in ("REPO_GITHUB_TOKEN", "ASSISTANT_REPO", "ASSISTANT_ORG_FILE", "HERMES_HOME", "HTTPS_PROXY"):
            self.assertEqual(s["env"][var], "${%s}" % var)
        # Hermes passes a stdio server only the variables listed, so every one the engine reads must be there.
        read = set()
        for f in (ROOT / "engine").rglob("*.py"):
            read |= set(re.findall(r"""os\.environ(?:\.get\(|\[)["']([A-Z_]+)["']""", f.read_text()))
        self.assertTrue(read)
        self.assertEqual(read - set(s["env"]), set())
        self.assertEqual((s["command"], s["args"]), ("@HERMES_PYTHON@", ["@ENGINE_DIR@/serve.py"]))
        toolset = CONFIG["custom_toolsets"]["maintainer-tools"]
        self.assertIn("mcp-repo-intel", toolset)
        self.assertNotIn("repo-intel", toolset)
        self.assertNotIn("repo-intel", CONFIG["plugins"]["enabled"])


class Subprocess(unittest.TestCase):
    def test_serve_py_runs_isolated_with_a_bare_environment(self):
        msgs = [rpc("initialize", {"protocolVersion": "2025-11-25"}), rpc("notifications/initialized", id_=None),
                rpc("tools/list", id_=2),
                rpc("tools/call", {"name": "queue", "arguments": {}}, id_=3),
                rpc("tools/call", {"name": "größe", "arguments": {}}, id_=4)]
        env = {"PATH": os.environ.get("PATH", ""), "ASSISTANT_REPO": "${ASSISTANT_REPO}", "LC_ALL": "C"}
        p = subprocess.run([sys.executable, "-I", str(ROOT / "engine/serve.py")], cwd="/",
                           input="".join(json.dumps(m, ensure_ascii=False) + "\n" for m in msgs).encode(),
                           capture_output=True, timeout=60, env=env)
        replies = [json.loads(x) for x in p.stdout.decode("utf-8").splitlines()]
        self.assertEqual([r["id"] for r in replies], [1, 2, 3, 4], p.stderr)
        self.assertIn("größe", replies[3]["error"]["message"])
        self.assertEqual(len(replies[1]["result"]["tools"]), len(TOOLS))
        # ASSISTANT_REPO was left as written, so it is unset: the tool asks for a repo, it does not try "${...}".
        self.assertIn("ASSISTANT_REPO", replies[2]["result"]["content"][0]["text"])
        self.assertTrue(replies[2]["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
