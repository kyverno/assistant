---
name: sandbox-access
description: "When a terminal command is blocked by the sandbox, ask the contributor for narrow network access instead of working around it."
version: 0.1.0
author: Suhaani Agarwal, Hermes Agent
license: MIT
platforms: [linux, macos, windows]
---

# sandbox-access Skill

The terminal runs in an OpenShell sandbox. It can read GitHub (`api.github.com`, `github.com`
clone and fetch, `codeload.github.com`, `raw.githubusercontent.com`) with `curl` and `git`, write
only under `/sandbox` and `/tmp`, and holds no tokens. Everything else is denied.

## When to Use

- A command fails with `Permission denied` on connect, a `403` from the proxy, or a `git` network
  error, and the host is one the task genuinely needs (a package registry, a docs site, an API).
- Not for GitHub writes: use the GitHub tools. The sandbox cannot write to GitHub.

## What to do

1. Do not retry through another program or encoding (`python3`, `nc`, base64, a different port).
   Those are blocked on purpose and each attempt appears in the audit log.
2. Submit the narrowest request that unblocks the task:

   ```bash
   curl -s -X POST http://policy.local/v1/proposals -H "Content-Type: application/json" -d '{
     "intent_summary": "<why, one sentence>",
     "operations": [{"addRule": {"ruleName": "<snake_case_name>", "rule": {
       "endpoints": [{"host": "<exact host>", "port": 443, "protocol": "rest",
                      "enforcement": "enforce",
                      "rules": [{"allow": {"method": "GET", "path": "<narrowest path>"}}]}],
       "binaries": [{"path": "<the one program that needs it>"}]}}}]}'
   ```

   - Exact host, never a wildcard; `protocol` is always `rest`; `enforcement` is always `enforce`.
   - Allow only the methods and path prefix the task needs. A read needs `GET`.
   - List only the program you are using, for example `/usr/bin/curl`.
3. The reply holds a request id. Tell the contributor what you asked for and why, and that they
   approve it with `KYVERNO_PROFILE_KIND=contributor ./scripts/openshell.sh rules approve <id>`. You cannot approve it yourself.
4. Retry the command once the contributor says it is approved. If it was rejected, use the reason
   they gave; do not resubmit the same request.
