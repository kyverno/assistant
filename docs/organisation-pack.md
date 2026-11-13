# The organisation pack

Everything the assistant knows about **your** organisation comes from one directory, `container/org/<name>/`, and
nothing about any organisation is written in code or in a skill. This page is the reference for what goes in it.
The [README](../README.md#2-write-your-organisation-pack) has the short version.

```
container/org/acme/
├── org.yaml     the rules: which files are generated or critical, your merge gate, which labels mean "ready"
└── facts.md     optional prose the assistant should know (codegen, CI, label meanings), in your own words
```

`container/org/kyverno/` is the worked example. Build your own image with it: `container/assistant --build --org acme`.
The pack is copied into the image, read-only, so a manipulated agent cannot rewrite its own rules.

## Where the assistant reads it from

| How you run it | Where the pack comes from |
|---|---|
| The sandbox or container image | The copy baked into the image at `/opt/kyverno/.../org/`, which the sandbox policy keeps read-only. It is read first; the copy in Hermes' writable home is never trusted over it. Choose it with `--org` when you build |
| `scripts/install.sh` on your host | Copied into the profile on every run, from `container/org/$ASSISTANT_ORG`, or else `container/org/<owner of ASSISTANT_REPO>/`. Edit it there: the copy in the profile is overwritten. With neither, it installs with no pack and says so, and removes a pack left in the profile by an earlier install for another repository |

## What happens without one, or with a mistake

| Situation | Result |
|---|---|
| No pack | The tools still run. Every file reads `STANDARD`, no PR is gate-blocked, no label means "ready". `repo_pack` says `pack: none`, and the assistant tells you which signals it is therefore missing. |
| A key it does not know (a typo), a wrong type, or a file that is not a mapping | An error that names the key, from `repo_pack` and the tools that need it. It never ignores it silently and never crashes. |
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
