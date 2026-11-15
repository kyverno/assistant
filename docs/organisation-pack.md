# The organisation pack

Everything the assistant knows about **your** organisation comes from one pack, and nothing about any organisation is
written in code or in a skill. You publish it in one of two places, and the published image reads it at run time:

```
acme/api/.github/assistant-org.yaml            this repository only
acme/.github/assistant-org.yaml                every repository of the owner without its own
```

A repository's own file replaces the shared one; they are not merged. Each can have `assistant-org-facts.md` next to it,
optional prose for the maintainer assistant (codegen, CI, label meanings), in your own words.

The file format is the one below; `container/org/kyverno/` is the worked example (its files are `org.yaml` and `facts.md`).
Whoever can push to the default branch of either place can change the rules, so protect it like the rest of your configuration.
Both are read from the default branch, so a pull request cannot change its own rules.
To bake a pack into an image you build instead, put it in `container/org/acme/` and run `container/assistant --build --org acme`;
a baked pack is read-only to the agent.

## Writing it with `assistant init`

```bash
container/assistant init acme/api                 # one repository
container/assistant init acme/api acme/web        # asks: one shared pack, or one per repository
container/assistant init acme/api --yes --dry-run # take the inferred answers and only print the file
```

It runs on your machine with your `gh` login and Python 3, and needs no clone, no Docker and no image:

```bash
curl -fsSL https://raw.githubusercontent.com/kyverno/assistant/main/container/assistant-init | python3 - acme/api
```

(Pin a release tag instead of `main` if you want to read the exact script first; with a checkout, `container/assistant init acme/api` does the same.) It reads the repository's file tree, `.gitattributes` (`linguist-generated`), `CODEOWNERS`, labels and recent reviews, and offers
only what exists:

| Written to the pack | From |
|---|---|
| `maintainer.file_classes`: generated, API surface, test-only (pre-ticked) and critical (you choose; it is never guessed, because owning a folder is not how risky it is) | File names, `.gitattributes`, folder names |
| `agent_docs`, `instructions_dir`: the repository's own guides (`AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md`, `RELEASING.md`, ...) | File names |
| `codeowners`, `labels` (catalogue file, `area/` and `kind/` prefixes), `readiness.checks_file` | Where the repository keeps them |
| `readiness` ready and waiting-on-the-author labels, `review_bots` | Label names, bots that reviewed recent PRs |

Label meanings need no entry: the assistant reads each label's description from GitHub live, so write them there (or in
`.github/labels.yml`). With `--facts` or when asked, it also adds `assistant-org-facts.md`, a short list of questions (how a release is
made, which branches matter, failures that are not the author's fault, what a reviewer looks for first) for you to fill in. It ends by
listing what it **could not** work out: merge gates (`branch_gates` needs a check name, a label and a bypass label), critical code, and
the release process and review norms. Put the last of those in `assistant-org-facts.md`, or in `CONTRIBUTING.md` or `RELEASING.md`
if they belong there too.

Keys: up and down (or `j`, `k`) move, space ticks, `a` all, `n` none, `/` filters a long list, enter confirms. Without a terminal it
takes the suggestions. It adds the files in a pull request (from your fork if you cannot push), signed off, and never overwrites an
existing file. If a shared pack already exists it says so before asking anything, and offers a pack for just that repository that
overrides it. With a shared pack it offers to create `<owner>/.github` if it does not exist.

## Where the assistant reads it from

| How you run it | Where the pack comes from |
|---|---|
| The published image | The repository's own `.github/assistant-org.yaml`, else `<owner>/.github/assistant-org.yaml`, for the repository you point it at, remembered for ten minutes, with the last good copy kept if GitHub cannot be reached. The image also bakes Kyverno's pack, used only for the owners its `repositories:` lists |
| An image you build | The copy baked in at `/opt/kyverno/.../org/`, which the sandbox policy keeps read-only. It is read first, for the owners its `repositories:` lists (every owner when it lists none). Choose it with `--org` when you build |
| `scripts/install.sh` on your host | Copied into the profile on every run, from `container/org/$ASSISTANT_ORG`, or else `container/org/<owner of ASSISTANT_REPO>/`. Edit it there: the copy in the profile is overwritten. With neither, it installs with no pack and says so, and removes a pack left in the profile by an earlier install for another repository |

## What happens without one, or with a mistake

| Situation | Result |
|---|---|
| No pack (nothing published in the repository or at `<owner>/.github`, or a private one the token cannot see: GitHub answers "not found" for both, and `repo_pack` says so) | The tools still run. Every file reads `STANDARD`, no PR is gate-blocked, no label means "ready". `repo_pack` says `pack: none`, and the assistant tells you which signals it is therefore missing. |
| The published pack cannot be read (a private `.github` the token cannot see, a network error with no earlier copy) | An error that names the file and the HTTP status, not "no pack". |
| A key it does not know (a typo), a wrong type, a malformed `repositories:` list, or a file that is not a mapping | An error that names the key, from `repo_pack` and the tools that need it. It never ignores it silently and never crashes. |
| A bad gate (no bypass label, a marker regex without a `(?P<branch>...)` group) | An error that names the gate and the problem. |
| A rule that matches nothing | Nothing happens. Check with `repo_pack`, which returns exactly what was loaded. |

## `org.yaml`

A minimal pack is about ten lines:

```yaml
name: acme
readiness:
  ready_labels: [ready-for-review]          # what your CI marks "waiting on a maintainer"
  attention_labels: [needs-author-action]   # what it marks "waiting on the author"
maintainer:
  file_classes:
    generated: ["**/zz_generated.*.go", "config/crds/**/*.yaml"]
    critical:  ["pkg/engine/**", "pkg/webhooks/**"]
    test_only: ["*_test.go", "test/**"]
```

### Shared keys (top level)

The top-level keys are `name`, `repositories`, `agent_docs`, `instructions_dir`, `codeowners`, `labels`, `readiness`,
`entry_labels`, `reserved_labels`, `review_bots`, `ladder`, `noise_paths`, `test_files` and `maintainer`. Any other key is an
error, so a typo like `maintaner:` is caught instead of silently doing nothing.

| Key | Meaning |
|---|---|
| `name` | Your organisation. |
| `readiness.ready_labels` | Labels that mean "ready for a maintainer". The queue fetches one set of PRs per label. Without them the assistant says so and works from the focus you give it. |
| `readiness.attention_labels` | Labels that mean "waiting on the author". |
| `readiness.infra_checks` | Check names whose failure is not the author's to fix. Keep this in step with `.github/pr-readiness-checks.yml`, which the shared CI reads. |
| `readiness.checks_file` | Path of that readiness-checks file. The contributor assistant reads it. Under `readiness:` only these four keys are accepted. |
| `review_bots` | Logins of review bots (for example `copilot-pull-request-reviewer`). |
| `agent_docs` | Globs for the files that describe your code (`**/AGENTS.md`, `ARCHITECTURE.md`, ...). |
| `codeowners` | Path of your CODEOWNERS file. |

### `maintainer:` keys

| Key | Meaning |
|---|---|
| `approval_bots` | Bots whose approval counts as one review signal in the queue order (`bot_approved` on each PR). A signal, not a substitute for a human. |
| `branch_gates` | Branch-wide merge gates, if you have one: `name`, `status_context` (the commit status, kept out of a PR's own CI state), `failure_issue_label` (an open issue with this label means the gate is red), `branch_marker` (a regex with a `(?P<branch>...)` group that finds the branch in the issue body; leave it out and the gate blocks every branch), `bypass_label`. No gates listed means the assistant never mentions one. Only the first gate is used. |
| `file_classes` | Globs for `generated`, `generated_exceptions` (globs for hand-written files inside a generated directory), `api_surface`, `critical` (elevated risk after merge) and `test_only`. Highest tier wins: generated, then API surface, then critical, then test-only. |
| `generated_inputs` | What feeds the generated files: `paths` (globs) and `dependency_modules` (a bump of one of these also counts). A PR touching an input must merge before one touching the output. |
| `dependency_bots` | `codegen_module_globs` (dependency bumps that need regenerated output in the same PR) and `codegen_kind_label`. |
| `milestones` | `title_format` (how your milestones are named), `open_milestone_label`, `issue_urgency_labels` (set by hand on an issue to mark urgency toward a milestone). |

**Glob syntax:** `**` crosses directories, `*` does not, `?` is one character. A pattern with no `/` names a file and
matches it at any depth (`zz_generated.deepcopy.go`).

### Keys only the contributor assistant reads

`ladder`, `labels`, `entry_labels`, `reserved_labels`, `noise_paths`, `test_files`, `instructions_dir`. The maintainer
assistant ignores them. See [`contributor.md`](contributor.md).

## `facts.md`

Free prose the assistant reads once per session through the `repo_pack` tool, as your own statement of how things
work here: what each label means, how code generation works, which CI jobs run before and after merge, how a gate is
opened and closed. Date it. The assistant treats it as yours and re-checks the live repository when a decision
depends on it. Keep it to what a new maintainer would need to be told; the code and your docs say the rest.

## Checking your pack

Ask the assistant to call `repo_pack`: it returns the rules exactly as loaded, or the error. To see what a pack does
to real PRs, ask for a queue and read the file classification and gate lines in the answer.
