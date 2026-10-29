#!/usr/bin/env python3
"""pre_tool_call hook for the contributor profile. Fails closed (config.yaml sets fail_closed: true).

1. Blocks anything that merges, approves, reviews or deletes. The toolset has no such tools; this is the
   backstop for a misconfiguration.
2. Guards opening a pull request (only from your own fork, with a title and description, no bot commands but /kind).
3. Guards the two comment tools: no line starting with "/" in a comment (the project's workflows run those
   as bot commands), except a bare "/assign" on an issue with the allowed label; only your own comments can
   be edited. Commenting on issues and on anyone's PR is otherwise open, as for any contributor. Whatever
   cannot be verified against GitHub is blocked.

Hermes pipes the event as JSON on stdin and expects JSON on stdout.
"""
import json
import os
import re
import ssl
import sys
import urllib.request

# Standard library only: this runs under whichever Python the profile gives it, and a missing import would block
# every comment (the hook fails closed). Its rules sit next to it, in guard-rules.json.
HERE = os.path.dirname(os.path.abspath(__file__))
ALWAYS_BLOCKED = r"merge|delete|force_push|review_write|approve|request_copilot|pull_request_review"
COMMENT_TOOLS = r"add_issue_comment|update_issue_comment"
PR_TOOLS = r"create_pull_request"


def block(message):
    print(json.dumps({"action": "block", "message": message}))
    sys.exit(0)


def profile_env():
    env = dict(os.environ)
    # Hermes runs a profile with HERMES_HOME set to the profile's folder, which holds its .env.
    try:
        for line in open(os.path.join(env.get("HERMES_HOME", ""), ".env")):
            key, _, value = line.strip().partition("=")
            if key and not key.startswith("#"):
                env.setdefault(key, value)
    except OSError:
        pass
    return env


def api(path, token):
    req = urllib.request.Request("https://api.github.com/" + path, headers={
        "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json", "User-Agent": "contributor-guard"})
    ctx = ssl.create_default_context()     # the system store (in the sandbox it holds the proxy's certificate)
    try:
        import certifi
        ctx.load_verify_locations(cafile=certifi.where())
    except ImportError:
        pass
    with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
        return json.load(resp)


def check_pull_request(args):
    """Opening a PR: only from your own fork, with a title and a body, and no bot commands in the body except the
    labelling ones the project's template uses (/kind)."""
    me = (profile_env().get("CONTRIBUTOR_GITHUB_LOGIN") or "").lower()
    head = str(args.get("head") or "")
    if not me:
        block("cannot check this pull request: your GitHub login is not set")
    if head.split(":", 1)[0].lower() != me or ":" not in head:
        block("a pull request must come from your own fork: head must be '%s:<branch>', not '%s'" % (me, head))
    if not (args.get("title") or "").strip() or not (args.get("body") or "").strip():
        block("a pull request needs a title and a description")
    allowed = set(json.load(open(os.path.join(HERE, "guard-rules.json")))["pull_request_body_commands"])
    extra = [c for c in re.findall(r"(?m)^\s*/([A-Za-z][\w-]*)", args.get("body") or "") if c.lower() not in allowed]
    if extra:
        block("the description has bot commands (" + ", ".join("/" + c for c in extra) + "): the project's workflows "
              "execute them. Leave them out, or ask the contributor to add them.")


def main():
    payload = json.load(sys.stdin)
    tool = payload.get("tool_name") or ""
    args = payload.get("tool_input") or {}

    if re.search(ALWAYS_BLOCKED, tool):
        block(f"{tool} is not permitted for the contributor profile: it changes project state")
    if re.search(PR_TOOLS, tool):
        try:
            check_pull_request(args)
        except SystemExit:
            raise
        except Exception as exc:
            block(f"could not check this pull request ({exc}); blocked")
        print("{}")
        return
    if not re.search(COMMENT_TOOLS, tool):
        print("{}")
        return

    body = args.get("body") or ""
    # Any line that starts with "/" is treated as a command: the project's workflows (Prow-style) act on
    # /assign, /lgtm, /cherry-pick, /conformance and more, and a list of names would go stale.
    commands = re.findall(r"(?m)^\s*/([A-Za-z][\w-]*)", body)
    if "update" not in tool and not commands:
        print("{}")
        return

    env = profile_env()
    token, me = env.get("REPO_GITHUB_TOKEN"), (env.get("CONTRIBUTOR_GITHUB_LOGIN") or "").lower()
    owner, repo = args.get("owner"), args.get("repo")
    if not token or not me or not owner or not repo:
        block("cannot verify this comment: the token, the login or the repository is missing")
    rules = json.load(open(os.path.join(HERE, "guard-rules.json")))
    try:
        if "update" in tool:
            comment = api(f"repos/{owner}/{repo}/issues/comments/{args.get('comment_id')}", token)
            if (comment.get("user") or {}).get("login", "").lower() != me:
                block("only your own comments can be edited")
        if commands:
            issue = api(f"repos/{owner}/{repo}/issues/{args.get('issue_number')}", token) if "update" not in tool else None
            labels = [l["name"] for l in (issue or {}).get("labels", [])]
            allowed = issue is not None and "pull_request" not in issue and any(
                [c.lower() for c in commands] == [rule["command"]] and body.strip() == rule["body_must_equal"]
                and rule["only_on_label"] in labels for rule in rules["comment_command_exceptions"])
            if not allowed:
                block("comments with bot commands (" + ", ".join("/" + c for c in commands) + ") are blocked: the "
                      "project's workflows execute them. Ask the contributor to post it themselves.")
    except SystemExit:
        raise
    except Exception as exc:
        block(f"could not verify the comment against GitHub ({exc}); blocked")
    print("{}")


if __name__ == "__main__":
    main()
