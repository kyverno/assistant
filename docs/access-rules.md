# Access rules

An organisation can limit which GitHub repositories the maintainer assistant may reach, for everyone who runs it. It is
enforced by NVIDIA's [Agent Policy Fabric](https://github.com/NVIDIA/agent-policy-fabric) (APF), which checks every GitHub call
the assistant makes. This matters because `gh` sign-in tokens reach every repository their owner can, and OpenShell's own rules
cannot look inside a GitHub MCP call to see which repository it names.

Nothing changes for an organisation that publishes no rules: the launcher says so and runs as before.

## Set them (an admin)

Add an `access:` section to `<owner>/.github/assistant-org.yaml`, the [organisation pack](organisation-pack.md):

```yaml
access:
  repos:
    - acme/*              # every repository of acme
    - partner/shared-lib  # one repository
```

Or let the launcher open the pull request: `container/assistant setup acme/api --access`. Merge it. Rules are read from the
default branch, only from `<owner>/.github` (a repository's own pack cannot loosen its owner's rules).

## What a maintainer sees

On each start the launcher reads the rules before it stores anything. With rules, it starts the stack in Docker (the first time
it downloads three images, a few hundred MB), runs the token and the sandbox on a dedicated OpenShell gateway behind APF, and says so.
A later start with unchanged rules takes a couple of seconds; changed rules apply to the running sandbox in about half a minute.
Your own OpenShell gateway is never touched. A sandbox made earlier on it is left alone and keeps no rules: delete it when you
no longer need it.

`container/assistant access status` shows whether the stack runs, `access stop` stops it (sandboxes made on it are kept).

## What it enforces

| Call | Allowed |
|---|---|
| A GitHub MCP tool naming `owner` and `repo` | Only for a listed repository (`owner/*` covers the owner) |
| Search tools | The query must open with an allowed `org:`, `user:` or `repo:` scope and add no other; `owner`/`repo` arguments must be allowed too |
| Project tools, `get_team_members`, ruleset and property reads | Only for an owner in the rules; enterprise level never |
| `api.github.com` REST | Only `/repos/<allowed>/...` and `/orgs/<allowed owner>/...`, plus `/zen`, `/rate_limit`, `/user` |
| GraphQL | Never: it cannot be limited to a repository |
| A tool the rules do not name | Never |

Names are matched as written, and in lower case. A request that spells an owner another way (`Acme` for `acme`) is denied, which
is the safe side. If APF cannot be reached the call is refused.

## What it does not do

- **It is enforced on the maintainer's machine, which they control.** `--no-access` (or `ASSISTANT_ACCESS_FILE`, to try rules
  before merging them) skips it and says so. For something a maintainer cannot skip, restrict tokens in the organisation's
  GitHub settings (personal access token policies, third-party OAuth app access).
- It covers `api.github.com` and the GitHub MCP server, not `git clone` of a public repository.
- The dedicated gateway listens on `127.0.0.1` without authentication, as APF's own test setup does: anything on the machine can use it.
- The contributor assistant and `--no-openshell` are not covered; Docker mode refuses to run when rules are published.
- Only a missing file means "no rules". A network error, a 403 (rate limit, SAML single sign-on) or a malformed file stops the
  launcher; `--no-access` goes without. A private `<owner>/.github` is only seen with a `gh` sign-in.
- `check_dependency_vulnerabilities` and `semantic_issue_similarity_search` are denied under rules: their arguments could not be checked.

## Versions

The stack is built from one APF commit and one OpenShell version, both pinned (`container/apf/VERSION`, `OPENSHELL_VERSION` in the
launcher). OpenShell is replacing the middleware hooks APF uses and removes the old ones in 0.2.0, so move either pin only after
re-running the repository-scoping checks against the new pair. `.github/workflows/apf-images.yml` builds and publishes the images
for the pinned commit.
