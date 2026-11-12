# CI health

Read-only composite action that turns the Actions history of one workflow into a report: which
failures share a root cause, which jobs are persistently red or flaky, what changed since the last
green run, and which jobs cost the most. It needs Python 3.9+ (standard library only) and `gh`.

Counting and classification are deterministic code. A later step, human or AI, reads the report; it
never has to derive the numbers.

## Interface

| Input | Default | Meaning |
| --- | --- | --- |
| `mode` | required | `collect`, or `validate` (offline configuration check, no token) |
| `config` | `.github/ci-health.json` | Repository-specific JSON, see below |
| `repository` | `github.repository` | `owner/name` whose runs are analysed |
| `output-dir` | `ci-health` | Receives `report.json` and `report.md` |

Output `report-path` is the JSON report. `collect` also appends the markdown to
`$GITHUB_STEP_SUMMARY`. Supply `GH_TOKEN` in the caller step's environment; the job needs
`actions: read` and `contents: read` on a private repository, and no write permission anywhere. The
action makes `GET` requests only; the offline tests assert that.

## Configuration

Only `workflow` is required. Unknown keys are rejected.

| Key | Default | Meaning |
| --- | --- | --- |
| `workflow` | | Workflow file name, for example `check-tests.yaml` |
| `branch`, `event` | `main`, `push` | Which runs to read. Cancelled runs are always ignored |
| `runs` | `30` | How many finished runs to analyse, at most 100 |
| `mass_failure_threshold` | `10` | Failed jobs in one run at or above which the run is a mass failure |
| `persistent_ratio` | `0.8` | Failed-run share at or above which a job is `persistent` (needs 3 runs) |
| `max_log_jobs` | `60` | Failed-job logs to download per report |
| `slow_jobs` | `10` | Rows in the slowest-jobs table |
| `version_pattern` | `v\d+\.\d+(\.\d+)?` | Matrix value that is the version; patch versions are folded into the minor |
| `failed_test_pattern` | `--- FAIL: (\S+)` | One capture group: the name of a failed test in a job log. The default reads `go test`; chainsaw output needs `--- FAIL: chainsaw/(\S+)` |
| `infra_patterns` | `[]` | Regular expressions that mark a failure outside the tests as infrastructure |

Everything specific to one repository lives in this file; the action itself names no workflow or job. A
template to start from is `adopt-ci-templates/ci-health.json`.

## Using it

Most repositories call the shared workflow, which does the trusted checkout, holds the token permissions and
uploads the report. Copy `adopt-ci-templates/workflows/ci-health.yaml` and `check-ci-health.yaml`, replace
`<full-commit-SHA>` with a reviewed commit of this repository, and add `.github/ci-health.json`. A caller that
prefers the action directly needs `contents: read` and `actions: read`, a default-branch checkout with
`persist-credentials: false`, and `GH_TOKEN`:

```yaml
- uses: kyverno/assistant/.github/actions/ci-health@<full-commit-SHA>
  env:
    GH_TOKEN: ${{ github.token }}
  with:
    mode: collect
```

`validate` needs no token and runs the same checks on the configuration, so it is safe on pull requests from
forks. The action has no `dry-run` input because it never writes to GitHub; a step that opens issues will add one,
defaulting to `true`.

## What the report says

- **Root causes.** Failed jobs grouped by what failed: the failing test names, or the normalised
  last error line when no test failed. One regression that fails 40 jobs across four Kubernetes
  versions is one row.
- **Jobs.** Per job and Kubernetes minor version, over the runs where it appeared:
  - `flaky`: failed, then passed on a re-run of the same commit (the strongest signal).
  - `persistent`: failed in most runs. Likely broken, not flaky.
  - `cross-version`: fails together with other versions of the same job, never alone.
  - `intermittent`: fails alone now and then. A suspected flake; a few samples prove little.
  - `fan-out-only`: failed only in mass-failure runs, so it follows another regression.
- **Red streak.** Commits (with PR numbers) between the last green run and the newest run, when a
  green run is in the window. Several commits can share one run, so this is a suspect list, not a
  verdict. Authors are printed in backticks and never `@`-mentioned.
- **Cost.** Mean runner-minutes per run and the slowest jobs.
- **Data quality.** Runs or logs the API would not return after retries are listed and left out of
  the statistics. A run that could not be read is never counted as green.

## Limits

- A job counts once per run however many shards fail. A partial re-run lists only the jobs it re-ran,
  so a job's latest state is its highest attempt.
- The `Tests Summary` counts and the `dir[name]` test-name form follow chainsaw's output. They only refine the
  result and do nothing for other runners.
- Per-test names come from failed-job logs only. Passing jobs are not downloaded, so tests skipped
  by a quarantine are not visible, and a flaky test inside a job that passed on re-run is known only
  by job.
- The jobs API drops steps inside composite actions, so a failure is classified from the log segment
  around the first error, not from a step name.
- Logs are fetched at most `max_log_jobs` times per report, runs with few failures first.
- History is limited to what the API still serves; nothing is stored between reports.

## Trusted use

Run it from a checkout of the default branch with `persist-credentials: false`. The configuration is
parsed as data, and its patterns are only ever matched against log text.
