#!/usr/bin/env python3
"""Collects CI health statistics for one workflow from the GitHub Actions API.

Deterministic on purpose: counting, grouping and classification happen here so that
anything that reads the report (a maintainer, or a later AI analysis step) starts from
numbers it did not have to derive. The collector never writes to GitHub.
"""

import collections
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

DEFAULTS = {
    "branch": "main",
    "event": "push",
    "runs": 30,
    "mass_failure_threshold": 10,
    "persistent_ratio": 0.8,
    "max_log_jobs": 60,
    "slow_jobs": 10,
    "version_pattern": r"v\d+\.\d+(\.\d+)?",
    "failed_test_pattern": r"--- FAIL: (\S+)",
    "infra_patterns": [],
}
KEYS = {"workflow"} | set(DEFAULTS)
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
STAMP = re.compile(r"^\ufeff?\d{4}-\d\d-\d\dT\S+Z ")
ERROR_LINE = re.compile(r"(FAIL|ERR|error|failed)", re.IGNORECASE)
SUMMARY = re.compile(r"^- (Passed|Failed|Skipped)\s+tests (\d+)")
PR_REF = re.compile(r"\(#(\d+)\)\s*$")
JOBS_PER_PAGE = 50
FAILURES = ("failure", "timed_out", "startup_failure")


def validate_config(config):
    if not isinstance(config, dict):
        raise ValueError("config must be a JSON object")
    unknown = set(config) - KEYS
    if unknown:
        raise ValueError(f"unknown config keys: {', '.join(sorted(unknown))}")
    if not isinstance(config.get("workflow"), str) or not config["workflow"].endswith((".yaml", ".yml")):
        raise ValueError("workflow must be a workflow file name such as check-tests.yaml")
    merged = {**DEFAULTS, **config}
    for key in ("runs", "mass_failure_threshold", "max_log_jobs", "slow_jobs"):
        if type(merged[key]) is not int or merged[key] < 1:
            raise ValueError(f"{key} must be a positive JSON integer")
    if merged["runs"] > 100:
        raise ValueError("runs must not exceed 100 (one API page)")
    ratio = merged["persistent_ratio"]
    if isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or not 0 < ratio <= 1:
        raise ValueError("persistent_ratio must be a number in (0, 1]")
    for key in ("branch", "event"):
        if not isinstance(merged[key], str) or not re.fullmatch(r"[A-Za-z0-9._/-]+", merged[key]):
            raise ValueError(f"{key} must be a plain name")
    if not isinstance(merged["infra_patterns"], list):
        raise ValueError("infra_patterns must be a list of regular expressions")
    re.compile(merged["version_pattern"])
    if re.compile(merged["failed_test_pattern"]).groups != 1:
        raise ValueError("failed_test_pattern must have exactly one capture group")
    for pattern in merged["infra_patterns"]:
        if not isinstance(pattern, str):
            raise ValueError("infra_patterns must be strings")
        re.compile(pattern)
    return merged


def load_config(path):
    return validate_config(json.loads(Path(path).read_text()))


# --- GitHub access -----------------------------------------------------------------------

class GhError(RuntimeError):
    pass


def gh(path, tries=5, text=False):
    """GET through the gh CLI, retrying transient server errors."""
    delay = float(os.environ.get("CI_HEALTH_RETRY_DELAY", "2"))
    last = ""
    for attempt in range(tries):
        proc = subprocess.run(["gh", "api", path], capture_output=True, text=True)
        if proc.returncode == 0:
            return proc.stdout if text else json.loads(proc.stdout)
        last = proc.stderr.strip()[:200]
        time.sleep(delay * (attempt + 1))
    raise GhError(f"gh api {path} failed: {last}")


def parse_time(value):
    return datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)


def list_runs(repo, cfg):
    query = f"branch={cfg['branch']}&event={cfg['event']}&status=completed&per_page=100"
    data = gh(f"repos/{repo}/actions/workflows/{cfg['workflow']}/runs?{query}")
    # Cancelled runs say nothing about the tests, so they are not part of the history.
    runs = [r for r in data["workflow_runs"] if r["conclusion"] in ("success", "failure")]
    return runs[: cfg["runs"]]


def cached(cache_dir, name, fetch):
    """Finished runs and their logs never change, so a local cache is always valid."""
    if not cache_dir:
        return fetch()
    path = Path(cache_dir) / name
    if path.exists():
        return json.loads(path.read_text())
    value = fetch()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return value


def list_jobs(repo, run_id):
    """Every job of a run, attempts included, fetched page by page.

    A run with hundreds of matrix jobs makes the all-in-one paginated call fail often.
    """
    jobs = []
    page = 1
    while True:
        # 50 per page: page 4 of 100 returns 502 for the same 400-job runs on every retry.
        data = gh(f"repos/{repo}/actions/runs/{run_id}/jobs?filter=all&per_page={JOBS_PER_PAGE}&page={page}")
        for j in data["jobs"]:
            jobs.append({
                "id": j["id"], "name": j["name"], "conclusion": j["conclusion"],
                "attempt": j.get("run_attempt", 1), "url": j["html_url"],
                "started": j.get("started_at"), "completed": j.get("completed_at"),
            })
        if len(data["jobs"]) < JOBS_PER_PAGE:
            return jobs
        page += 1


# --- Parsing -----------------------------------------------------------------------------

def split_name(name, version_re):
    """'Group / job (v1.35.1, 3)' -> ('Group / job', 'v1.35')."""
    m = re.match(r"^(?P<base>.*?)\s*\((?P<matrix>[^)]*)\)\s*$", name)
    if not m:
        return name, None
    for part in (p.strip() for p in m.group("matrix").split(",")):
        if re.fullmatch(version_re, part):
            return m.group("base"), ".".join(part.split(".")[:2])
    return m.group("base"), None


def clean_lines(raw):
    return [ANSI.sub("", STAMP.sub("", line)).rstrip("\r") for line in raw.split("\n")]


def normalise(line):
    line = re.sub(r"0x[0-9a-f]+|[0-9a-f]{12,}", "<hex>", line.strip())
    return re.sub(r"\d+", "<n>", line)[:200]


def test_name(raw):
    """Chainsaw-style 'dir[name]' collapses to 'dir' when the name repeats the directory; any other name is kept."""
    m = re.fullmatch(r"(.*)\[(.*)\]", raw)
    if not m:
        return raw
    directory, name = m.groups()
    if directory in ("", "."):
        return name
    return directory if directory.rsplit("/", 1)[-1] == name else raw


def parse_log(raw, cfg):
    """What failed and why, from one job log.

    A runner that prints one failure line per test (go test and chainsaw do) makes per-test results
    recoverable from the log without JUnit reports; `failed_test_pattern` says what that line looks like.
    """
    lines = clean_lines(raw)
    tests = sorted({test_name(t) for line in lines for t in re.findall(cfg["failed_test_pattern"], line)})
    counts = {}
    for line in lines:
        m = SUMMARY.match(line)
        if m:
            counts[m.group(1).lower()] = int(m.group(2))
    end = next((i for i, line in enumerate(lines) if line.startswith("##[error]")), len(lines))
    start = next((i for i in range(end - 1, -1, -1) if lines[i].startswith("##[group]")), 0)
    segment = lines[start:end]
    text = "\n".join(segment)
    if tests:
        category = "test"
    elif any(re.search(p, text, re.IGNORECASE) for p in cfg["infra_patterns"]):
        category = "infra"
    elif counts:
        category = "test"
    else:
        category = "other"
    errors = [line for line in segment if ERROR_LINE.search(line)]
    if tests:
        fingerprint = "tests: " + ", ".join(tests)
    else:
        fingerprint = "msg: " + normalise(errors[-1] if errors else (segment[-1] if segment else "no output"))
    shown = [line for line in segment if line.strip()][-12:]
    return {
        "category": category, "tests": tests, "counts": counts,
        "fingerprint": fingerprint, "excerpt": "\n".join(shown)[-1500:],
    }


# --- Analysis ----------------------------------------------------------------------------

def duration(job):
    if job.get("started") and job.get("completed"):
        return (parse_time(job["completed"]) - parse_time(job["started"])).total_seconds()
    return None


def analyse(repo, cfg, cache_dir=None):
    version_re = cfg["version_pattern"]
    runs = list_runs(repo, cfg)
    skipped, run_rows, per_run_failed = [], [], []
    keys = collections.defaultdict(lambda: {
        "runs_seen": 0, "failed_runs": 0, "single_events": 0, "cross_events": 0,
        "mass_events": 0, "recovered": 0, "durations": [], "examples": [],
    })
    for run in runs:
        try:
            jobs = cached(cache_dir, f"jobs-{run['id']}.json", lambda r=run: list_jobs(repo, r["id"]))
        except GhError as err:
            skipped.append({"run_id": run["id"], "reason": str(err)})
            continue
        # A partial re-run lists only the jobs it re-ran, so the latest state of a run is the
        # highest attempt of each job, not everything in the highest attempt.
        latest, failed_before = {}, set()
        for j in jobs:
            if j["conclusion"] is None:
                continue
            if j["conclusion"] in FAILURES:
                failed_before.add(j["name"])
            if j["name"] not in latest or j["attempt"] > latest[j["name"]]["attempt"]:
                latest[j["name"]] = j
        if not latest:
            continue
        final = max(j["attempt"] for j in latest.values())
        failed = {n: j for n, j in latest.items() if j["conclusion"] in FAILURES}
        mass = len(failed) >= cfg["mass_failure_threshold"]
        recovered = {n for n in failed_before if latest[n]["conclusion"] == "success"}
        minutes = sum(d for d in (duration(j) for j in latest.values()) if d) / 60
        run_rows.append({
            "run_id": run["id"], "sha": run["head_sha"], "conclusion": run["conclusion"],
            "created": run["created_at"], "url": run["html_url"], "attempts": final,
            "failed_jobs": len(failed), "mass_failure": mass, "jobs": len(latest),
            "runner_minutes": round(minutes, 1),
        })
        # Jobs of one matrix are one key; a run counts once per key however many shards fail.
        seen, bad = set(), collections.defaultdict(list)
        for name, j in latest.items():
            base, ver = split_name(name, version_re)
            key = (base, ver)
            seen.add(key)
            d = duration(j)
            if d is not None and j["conclusion"] == "success":
                keys[key]["durations"].append(d)
            if name in failed:
                bad[key].append(j)
        by_base = collections.defaultdict(set)
        for base, ver in bad:
            by_base[base].add(ver)
        for key in seen:
            keys[key]["runs_seen"] += 1
        for key, js in bad.items():
            k = keys[key]
            k["failed_runs"] += 1
            if mass:
                k["mass_events"] += 1
            elif len(by_base[key[0]]) > 1:
                k["cross_events"] += 1
            else:
                k["single_events"] += 1
            if len(k["examples"]) < 3:
                k["examples"].append(js[0]["url"])
        for n in recovered:
            keys[split_name(n, version_re)]["recovered"] += 1
        per_run_failed.append((run, mass, list(failed.values())))

    findings = []
    for (base, ver), k in keys.items():
        if not (k["failed_runs"] or k["recovered"]):
            continue
        lone = k["single_events"] + k["cross_events"]
        rate = k["failed_runs"] / k["runs_seen"]
        if k["recovered"]:
            kind = "flaky"
        elif lone == 0:
            kind = "fan-out-only"
        elif k["runs_seen"] >= 3 and rate >= cfg["persistent_ratio"]:
            kind = "persistent"
        elif k["single_events"] == 0:
            kind = "cross-version"
        else:
            kind = "intermittent"
        findings.append({"job": base, "k8s": ver, "kind": kind, "fail_rate": round(rate, 2),
                         **{x: k[x] for x in ("runs_seen", "failed_runs", "single_events",
                                              "cross_events", "mass_events", "recovered", "examples")}})
    order = {"flaky": 0, "persistent": 1, "cross-version": 2, "intermittent": 3, "fan-out-only": 4}
    findings.sort(key=lambda f: (order[f["kind"]], -f["failed_runs"]))

    slow = sorted(
        ({"job": b, "k8s": v, "runs": len(k["durations"]),
          "avg_minutes": round(sum(k["durations"]) / len(k["durations"]) / 60, 1)}
         for (b, v), k in keys.items() if k["durations"]),
        key=lambda s: -s["avg_minutes"])[: cfg["slow_jobs"]]

    causes, log_errors = root_causes(repo, cfg, per_run_failed, version_re, cache_dir)
    return {
        "repo": repo, "workflow": cfg["workflow"], "branch": cfg["branch"],
        "runs_requested": cfg["runs"], "runs_analysed": len(run_rows),
        "red_runs": sum(r["conclusion"] == "failure" for r in run_rows),
        "mass_failure_runs": sum(r["mass_failure"] for r in run_rows),
        "mean_runner_minutes": round(sum(r["runner_minutes"] for r in run_rows) / len(run_rows), 1) if run_rows else 0,
        "streak": streak(repo, run_rows),
        "root_causes": causes, "findings": findings, "slowest_jobs": slow, "runs": run_rows,
        "data_quality": {"skipped_runs": skipped, "log_errors": log_errors},
    }


def root_causes(repo, cfg, per_run_failed, version_re, cache_dir):
    """Group failed jobs by what failed, so one regression is one row instead of dozens."""
    budget = cfg["max_log_jobs"]
    groups = {}
    log_errors = []
    # Runs that failed a handful of jobs first: their failures are the least ambiguous.
    for run, mass, failed in sorted(per_run_failed, key=lambda t: (t[1], 0)):
        for j in failed:
            if budget <= 0:
                break
            budget -= 1
            try:
                parsed = cached(cache_dir, f"log-{j['id']}.json",
                                lambda job=j: parse_log(gh(f"repos/{repo}/actions/jobs/{job['id']}/logs", text=True), cfg))
            except GhError as err:
                log_errors.append({"job": j["name"], "reason": str(err)})
                continue
            base, ver = split_name(j["name"], version_re)
            g = groups.setdefault(parsed["fingerprint"], {
                "fingerprint": parsed["fingerprint"], "category": parsed["category"],
                "tests": parsed["tests"], "jobs": set(), "versions": set(), "runs": set(),
                "excerpt": parsed["excerpt"], "examples": [],
            })
            g["jobs"].add(base)
            g["versions"].add(ver)
            g["runs"].add(run["id"])
            if len(g["examples"]) < 3:
                g["examples"].append(j["url"])
    out = []
    for g in groups.values():
        out.append({**g, "jobs": sorted(g["jobs"]), "versions": sorted(v for v in g["versions"] if v),
                    "runs": sorted(g["runs"], reverse=True)})
    out.sort(key=lambda g: (-len(g["runs"]), -len(g["jobs"])))
    return out, log_errors


def streak(repo, run_rows):
    """The current red streak and the commits since the last green run, when it is in the window."""
    if not run_rows or run_rows[0]["conclusion"] != "failure":
        return None
    red = 0
    for r in run_rows:
        if r["conclusion"] != "failure":
            break
        red += 1
    result = {"red_runs": red, "since_green": None, "commits": []}
    if red < len(run_rows):
        green = run_rows[red]
        result["since_green"] = green["sha"][:10]
        try:
            data = gh(f"repos/{repo}/compare/{green['sha']}...{run_rows[0]['sha']}")
            for c in data.get("commits", [])[:50]:
                subject = c["commit"]["message"].split("\n")[0]
                pr = PR_REF.search(subject)
                result["commits"].append({
                    "sha": c["sha"][:10], "subject": subject[:100],
                    "author": (c.get("author") or {}).get("login"), "pr": int(pr.group(1)) if pr else None,
                })
        except GhError:
            result["commits"] = None
    return result


# --- Rendering ---------------------------------------------------------------------------

def cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def fence(text):
    longest = max([2] + [len(m) for m in re.findall(r"`+", text)])
    return "`" * (longest + 1)


def render(report):
    out = [f"## CI health: {report['workflow']} on `{report['branch']}`", ""]
    out.append(f"{report['runs_analysed']} runs analysed, {report['red_runs']} red, "
               f"{report['mass_failure_runs']} mass failures; a run costs about "
               f"{report['mean_runner_minutes']} runner-minutes.")
    q = report["data_quality"]
    if q["skipped_runs"] or q["log_errors"]:
        out += ["", f"> Partial data: {len(q['skipped_runs'])} run(s) skipped and {len(q['log_errors'])} "
                    "log(s) unavailable after retries. Statistics below ignore them; a skipped run is not counted as green."]
    s = report["streak"]
    if s:
        out += ["", f"### Red for the last {s['red_runs']} run(s)", ""]
        if s["commits"]:
            out.append(f"Commits since the last green run (`{s['since_green']}`):")
            for c in s["commits"]:
                ref = f" #{c['pr']}" if c["pr"] else ""
                out.append(f"- `{c['sha']}` {cell(c['subject'])}{ref} by `{c['author']}`")
        elif s["since_green"] is None:
            out.append("No green run in the window, so the first bad commit is older than this report.")
        else:
            out.append("The commit list could not be fetched.")
    if report["root_causes"]:
        out += ["", "### Root causes", "",
                "| What failed | Category | Jobs | k8s | Runs |", "| --- | --- | --- | --- | --- |"]
        for g in report["root_causes"][:15]:
            out.append(f"| {cell(g['fingerprint'][:120])} | {g['category']} | {cell(', '.join(g['jobs'])[:120])} | "
                       f"{', '.join(g['versions']) or '-'} | {len(g['runs'])} |")
        top = report["root_causes"][0]
        f = fence(top["excerpt"])
        out += ["", f"<details><summary>Most frequent failure, log excerpt</summary>\n\n{f}text\n{top['excerpt']}\n{f}\n\n</details>"]
    if report["findings"]:
        out += ["", "### Jobs", "", "| Job | k8s | Kind | Failed runs | Alone | With other versions | In mass failures | Recovered on re-run |",
                "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for f in report["findings"][:20]:
            out.append(f"| {cell(f['job'])} | {f['k8s'] or '-'} | {f['kind']} | {f['failed_runs']}/{f['runs_seen']} | "
                       f"{f['single_events']} | {f['cross_events']} | {f['mass_events']} | {f['recovered']} |")
    if report["slowest_jobs"]:
        out += ["", "### Slowest jobs", "", "| Job | k8s | Average minutes |", "| --- | --- | --- |"]
        out += [f"| {cell(s['job'])} | {s['k8s'] or '-'} | {s['avg_minutes']} |" for s in report["slowest_jobs"]]
    return "\n".join(out) + "\n"


def main():
    mode = os.environ["CI_HEALTH_MODE"]
    config_path = os.environ.get("CI_HEALTH_CONFIG", ".github/ci-health.json")
    try:
        cfg = load_config(config_path)
    except (OSError, ValueError, re.error) as err:
        print(f"::error::{config_path}: {err}")
        return 1
    if mode == "validate":
        print(f"{config_path} is valid")
        return 0
    if mode != "collect":
        print(f"::error::unknown mode {mode!r}; use collect or validate")
        return 1
    repo = os.environ["CI_HEALTH_REPOSITORY"]
    out_dir = Path(os.environ.get("CI_HEALTH_OUT", "ci-health"))
    out_dir.mkdir(parents=True, exist_ok=True)
    report = analyse(repo, cfg, os.environ.get("CI_HEALTH_CACHE") or None)
    (out_dir / "report.json").write_text(json.dumps(report, indent=1))
    markdown = render(report)
    (out_dir / "report.md").write_text(markdown)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(markdown)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"report-path={out_dir / 'report.json'}\n")
    print(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
