# The contributor assistant

A second profile, for people contributing to a repository. It is not part of the current phase (maintainer visibility), and an organisation needs more setup for it than for the maintainer assistant: see the roadmap in the [README](../README.md#roadmap).

## Contributor profile

A second profile for people contributing to Kyverno. It finds work worth doing, explains how the code
and folders fit together, helps prepare evidence-backed PRs, makes review feedback understandable, and
tracks the path from first PR toward reviewer. It reads GitHub with your own token and can write two
things, each after you confirm the exact text: a comment on an issue or a PR, and a pull request from your own fork
(`create_pull_request`, which needs the `gh` sign-in or a token that can write pull requests on the repository). It cannot
merge, approve, submit a formal
review; hooks enforce that. It can push to your own fork only if you start the launcher with `--push-to-fork`.

```bash
container/assistant --profile contributor     # or just container/assistant, which asks which assistant you want
```

It has its own sandbox (`assistant-contrib`) and its own GitHub token, apart from the maintainer's. It needs a GitHub
token of your own account (a fine-grained PAT; "Public repositories (read-only)" is enough to read everything, or sign in
with `gh`), your GitHub username (read from the token), and the repo (default `kyverno/kyverno`). The coding environment
below lets it clone the repo, build and test in the sandbox, which holds no real tokens. It runs only in the
sandbox: the host install (`scripts/install.sh`) sets up the maintainer profile only. Background updates (new reviews,
failing checks, merged PRs) need the messaging gateway, so they are not available in the sandbox yet.

| Tool (plugin `profiles/contributor/plugins/contributor/`) | Answers |
|---|---|
| `knowledge_for` | What the repo's AGENTS.md chain, instructions, labels and CODEOWNERS say about a path |
| `explain_path` | How a file or folder fits in: contents, Go imports and exports, who imports it, tests, history |
| `related_prs` | How the same area was changed before: merged precedents, open work, rejected attempts |
| `find_issues`, `issue_history` | What to work on, and what happened to each issue before |
| `my_pr_state` | Review threads (maintainers, humans, bots), failing checks, blockers on your PR |
| `journey_summary`, `journey_note`, `journey_self_report` | What you have done, areas, ladder progress, private notes |
| `inbox_read` | What the background watch found |

Skills: `find-work`, `understand-code`, `make-the-change`, `review-loop`, `grow`. The coding environment is shared with the maintainer profile (see above).
