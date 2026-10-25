"""Eval runner: scores recorded commentary offline and writes the report.

    python -m evals.run                      # score every recorded prompt version
    python -m evals.run --version 2026-10-23.1
    python -m evals.run --record --yes       # regenerate the samples (paid API calls)
    python -m evals.run --record --yes --set holdout
    python -m evals.run --record --yes --case en-nvda-30-beats --samples 3

Two case sets: the golden set (evals/cases/), from which prompt rules are
derived, and the hold-out set (evals/holdout/), which is only ever scored.
The report shows them separately.

Gate policy: the pytest in tests/test_evals_suite.py is the CI gate for the
scorer itself (every metric must detect its failure case on artificial
answers, and the report must build from the recorded files) and the
regression gate between prompt versions: the newest recorded version must
not have a lower case pass rate than the previous one on any correctness
metric (numbers_match, verdict, coverage, complete). All other pass rates
are metrics, not gates: a prompt change is judged by comparing two reports
under the scoring rule written at the top of each report.

Recording calls app.commentary.call_model directly (the Anthropic API, not
the deployed service), so the daily cache is bypassed and every sample is an
independent draw. It never runs in CI.
"""

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from evals.score import (
    CLOSED_BY,
    COMMUNICATION_METRICS,
    CORRECTNESS_METRICS,
    METRICS,
    REJECTED_VERSIONS,
    SCORER_CHANGELOG,
    SCORING_RULE,
    SPEC_GAPS,
    payload_for,
    score,
)

ROOT = Path(__file__).resolve().parent
SETS = {"golden": ROOT / "cases", "holdout": ROOT / "holdout"}
RECORDED_DIR = ROOT / "recorded"
RESULTS_DIR = ROOT / "results"
SAMPLES = 3

# Token figures of two real replies (README example and a production call),
# used only for the cost estimate before recording.
_INPUT_TOKENS = 461
_OUTPUT_TOKENS_RANGE = (265, 676)


def load_cases(only: str | None = None, sets: tuple = tuple(SETS)) -> list:
    """Cases of the given sets, each tagged with its set name."""
    cases = []
    for name in sets:
        for path in sorted(SETS[name].glob("*.json")):
            case = json.loads(path.read_text(encoding="utf-8"))
            case["set"] = name
            cases.append(case)
    if only:
        cases = [c for c in cases if c["id"] == only]
        if not cases:
            raise SystemExit(f"no case with id {only!r}")
    return cases


def version_key(version: str) -> tuple:
    """Sort key for 'YYYY-MM-DD.N' labels: the date, then the counter."""
    date, _, counter = version.partition(".")
    return (date, int(counter or 0))


def recorded_versions() -> list:
    if not RECORDED_DIR.exists():
        return []
    return sorted(
        (p.name for p in RECORDED_DIR.iterdir() if p.is_dir()), key=version_key
    )


def accepted_versions() -> list:
    """Recorded versions that were not rejected, oldest first."""
    return [v for v in recorded_versions() if v not in REJECTED_VERSIONS]


def load_samples(version: str) -> list:
    samples = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((RECORDED_DIR / version).glob("*.json"))
    ]
    return sorted(samples, key=lambda s: (s["case_id"], s["sample"]))


def score_samples(samples: list, cases: list) -> list:
    by_id = {c["id"]: c for c in cases}
    scored = []
    for sample in samples:
        case = by_id[sample["case_id"]]
        results = score(
            case, sample.get("text"), sample.get("stop_reason"), sample.get("error")
        )
        scored.append(
            {
                "case_id": case["id"],
                "set": case["set"],
                "sample": sample["sample"],
                "results": results,
            }
        )
    return scored


def _metric_table(scored: list) -> dict:
    """Per metric: pass counts over samples, cases and complete samples."""
    per_case = defaultdict(list)
    for entry in scored:
        per_case[entry["case_id"]].append(entry)
    table = {}
    for metric in METRICS:
        passed = [e["results"][metric].passed for e in scored]
        complete = [e for e in scored if e["results"]["complete"].passed]
        case_pass = {
            cid: all(e["results"][metric].passed for e in es)
            for cid, es in per_case.items()
        }
        unstable = sorted(
            cid
            for cid, es in per_case.items()
            if len({e["results"][metric].passed for e in es}) > 1
        )
        table[metric] = {
            "samples_passed": sum(passed),
            "samples": len(passed),
            "cases_passed": sum(case_pass.values()),
            "cases": len(case_pass),
            "complete_passed": sum(e["results"][metric].passed for e in complete),
            "complete": len(complete),
            "unstable": unstable,
        }
    return table


def summarize(scored: list) -> dict:
    """Pass rates per set and over all cases, plus failures and hints."""
    sets = {
        name: _metric_table([e for e in scored if e["set"] == name])
        for name in SETS
        if any(e["set"] == name for e in scored)
    }
    failures = [
        (e["set"], e["case_id"], e["sample"], metric, e["results"][metric].reason)
        for e in scored
        for metric in METRICS
        if not e["results"][metric].passed
    ]
    hints = defaultdict(list)
    for e in scored:
        for metric in METRICS:
            for hint in e["results"][metric].hints:
                tag, _, message = hint.partition(": ")
                hints[tag].append((e["case_id"], e["sample"], metric, message))
    return {
        "sets": sets,
        "all": _metric_table(scored),
        "failures": failures,
        "hints": dict(hints),
    }


def case_pass_rate(summary: dict, metric: str, set_name: str | None = None) -> float:
    """Share of cases passing `metric` (all samples), in one set or over all."""
    table = summary["all"] if set_name is None else summary["sets"][set_name]
    m = table[metric]
    return m["cases_passed"] / m["cases"] if m["cases"] else 0.0


def _pct(part: int, whole: int) -> str:
    return f"{part}/{whole}" + (f" ({100 * part // whole}%)" if whole else "")


def _group(metric: str) -> str:
    if metric in CORRECTNESS_METRICS:
        return "correctness"
    if metric in COMMUNICATION_METRICS:
        return "communication"
    return "contract"


def render_table(summary: dict) -> str:
    rows = []
    for name, table in summary["sets"].items():
        rows.append(f"[{name}]")
        rows.append(
            "metric          group          samples        cases (all samples)  "
            "complete only   unstable"
        )
        for metric, m in table.items():
            rows.append(
                f"{metric:<15} {_group(metric):<14} "
                f"{_pct(m['samples_passed'], m['samples']):<14} "
                f"{_pct(m['cases_passed'], m['cases']):<20} "
                f"{_pct(m['complete_passed'], m['complete']):<15} {len(m['unstable'])}"
            )
    return "\n".join(rows)


def _set_header(name: str, samples: list, scored: list) -> str:
    ids = {e["case_id"] for e in scored if e["set"] == name}
    if not ids:
        return f"{name}: not recorded"
    per_case = Counter(e["case_id"] for e in scored if e["set"] == name)
    entries = [e for e in scored if e["set"] == name]
    complete = sum(1 for e in entries if e["results"]["complete"].passed)
    return (
        f"{name} {len(ids)} cases × {max(per_case.values())} samples = "
        f"{len(entries)} ({complete} complete)"
    )


def render_markdown(
    version: str,
    samples: list,
    cases: list,
    scored: list,
    summary: dict,
    previous: tuple | None = None,
) -> str:
    """The report; `previous` is (version, summary) of the version before."""
    cost = sum(s.get("cost_usd") or 0 for s in samples)
    tokens = [s["output_tokens"] for s in samples if s.get("output_tokens")]
    models = sorted({s["model"] for s in samples if s.get("model")})
    stamps = sorted(s["generated_at"] for s in samples if s.get("generated_at"))
    recorded = f"{stamps[0][:16]} to {stamps[-1][:16]} UTC" if stamps else "n/a"
    per_commentary = (
        f"mean {sum(tokens) / len(tokens):.0f} output tokens and "
        f"{cost / len(tokens):.4f} USD per commentary"
        if tokens
        else "no token figures"
    )

    out = [f"# Commentary eval — prompt {version}", ""]
    out.append(
        f"Recorded {recorded} · model {', '.join(models) or 'n/a'} · "
        + " · ".join(_set_header(name, samples, scored) for name in SETS)
        + f" · total cost {cost:.2f} USD · {per_commentary}"
    )
    out += ["", f"**Scoring rule.** {SCORING_RULE}"]
    if version in REJECTED_VERSIONS:
        out += ["", f"**Rejected.** {REJECTED_VERSIONS[version]}"]
    if previous:
        out.append(f"Δ columns compare case pass rates with prompt {previous[0]}.")
    out += [
        "",
        "With three samples per case the suite shows instability, not statistical "
        "significance; a difference of one or two cases between prompt versions is "
        "noise and is reported as such. A case passes a metric only when all of its "
        "samples pass.",
    ]

    for name in SETS:
        table = summary["sets"].get(name)
        title = "Golden set" if name == "golden" else "Hold-out set"
        out += ["", f"## {title}", ""]
        if not table:
            out.append("Not recorded for this version.")
            continue
        header = "| Metric | Group | Samples | Cases (all samples) | Complete samples only | Unstable cases |"
        rule = "| --- | --- | --- | --- | --- | --- |"
        if previous and name in previous[1]["sets"]:
            header += " Δ cases |"
            rule += " --- |"
        out += [header, rule]
        for metric, m in table.items():
            row = (
                f"| {metric} | {_group(metric)} | {_pct(m['samples_passed'], m['samples'])} | "
                f"{_pct(m['cases_passed'], m['cases'])} | "
                f"{_pct(m['complete_passed'], m['complete'])} | "
                f"{', '.join(m['unstable']) or '—'} |"
            )
            if previous and name in previous[1]["sets"]:
                before = previous[1]["sets"][name][metric]["cases_passed"]
                delta = m["cases_passed"] - before
                row += f" {delta:+d} |"
            out.append(row)

    out += ["", "## Failures", ""]
    if summary["failures"]:
        out += [
            "| Set | Case | Sample | Metric | Reason |",
            "| --- | --- | --- | --- | --- |",
        ]
        for set_name, cid, n, metric, reason in summary["failures"]:
            out.append(f"| {set_name} | {cid} | {n} | {metric} | {_cell(reason)} |")
    else:
        out.append("None.")

    out += ["", "## Hints (spec gaps observed)", ""]
    if summary["hints"]:
        out += ["| Gap | Count | Example |", "| --- | --- | --- |"]
        for tag in sorted(summary["hints"]):
            items = summary["hints"][tag]
            cid, n, _metric, message = items[0]
            out.append(f"| {tag} | {len(items)} | {cid}-{n}: {_cell(message)} |")
    else:
        out.append("None.")

    out += ["", "## Scorer changes", ""]
    out.append(
        "The scorer changes only with evidence: a sample on which it measured "
        "wrongly. Every change is listed here and applies to all versions."
    )
    out += ["", "| Date | Change | Evidence |", "| --- | --- | --- |"]
    for date, change, evidence in SCORER_CHANGELOG:
        out.append(f"| {date} | {_cell(change)} | {_cell(evidence)} |")

    out += ["", "## Spec gaps", ""]
    out.append(
        "Points the prompt leaves open. They are listed here as input for the next "
        "prompt revision and never counted as model failures."
    )
    out.append("")
    for tag, description in SPEC_GAPS:
        closed = CLOSED_BY.get(tag)
        if closed and version_key(version) >= version_key(closed):
            out.append(f"- **{tag}** (closed by prompt {closed}) {description}")
        else:
            out.append(f"- **{tag}** {description}")

    recorded_ids = {s["case_id"] for s in samples}
    out += [
        "",
        "## Cases",
        "",
        "| Set | Case | Language | Horizon | Verdict | Covers |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for case in cases:
        if case["id"] in recorded_ids:
            out.append(
                f"| {case['set']} | {case['id']} | {case['language']} | {case['horizon']} | "
                f"{case['expect']['verdict']} | {_cell(case['covers'])} |"
            )
    return "\n".join(out) + "\n"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def build_summary(version: str, cases: list | None = None) -> tuple:
    """(samples, scored, summary) for one recorded prompt version."""
    cases = cases or load_cases()
    samples = load_samples(version)
    if not samples:
        raise SystemExit(f"no recorded samples under {RECORDED_DIR / version}")
    scored = score_samples(samples, cases)
    return samples, scored, summarize(scored)


def build_report(version: str) -> tuple:
    """(markdown, summary) for one recorded prompt version."""
    cases = load_cases()
    samples, scored, summary = build_summary(version, cases)
    # Compare with the accepted version before this one.
    earlier = [v for v in accepted_versions() if version_key(v) < version_key(version)]
    previous = None
    if earlier:
        previous = (earlier[-1], build_summary(earlier[-1], cases)[2])
    return render_markdown(version, samples, cases, scored, summary, previous), summary


def write_report(version: str) -> Path:
    markdown, summary = build_report(version)
    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / f"{version}.md"
    path.write_text(markdown, encoding="utf-8")
    print(f"prompt {version}")
    print(render_table(summary))
    print(f"report written to {path.relative_to(ROOT.parent)}")
    return path


def record(cases: list, samples: int, yes: bool) -> None:
    if os.getenv("CI") or os.getenv("GITHUB_ACTIONS"):
        raise SystemExit("--record never runs in CI")
    from dotenv import load_dotenv

    load_dotenv()
    import anthropic

    from app import commentary

    calls = len(cases) * samples
    low = commentary.cost_usd(_INPUT_TOKENS, _OUTPUT_TOKENS_RANGE[0]) * calls
    high = commentary.cost_usd(_INPUT_TOKENS, _OUTPUT_TOKENS_RANGE[1]) * calls
    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
    sets = ", ".join(sorted({c["set"] for c in cases}))
    print(
        f"Recording {len(cases)} cases ({sets}) × {samples} samples = {calls} calls to the "
        f"Anthropic API (model {model}, prompt {commentary.PROMPT_VERSION})."
    )
    print(f"Estimated cost {low:.2f}–{high:.2f} USD at the configured prices.")
    if not yes:
        raise SystemExit("Pass --yes to proceed.")
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY is not set.")

    out_dir = RECORDED_DIR / commentary.PROMPT_VERSION
    out_dir.mkdir(parents=True, exist_ok=True)
    total = 0.0
    for case in cases:
        payload = payload_for(case)
        for n in range(1, samples + 1):
            entry = {
                "case_id": case["id"],
                "sample": n,
                "prompt_version": commentary.PROMPT_VERSION,
                "model": model,
            }
            try:
                reply = commentary.call_model(payload, case["language"])
            except (anthropic.APIError, commentary.CommentaryError) as exc:
                entry.update(
                    text=None, stop_reason=None, error=f"{type(exc).__name__}: {exc}"
                )
                status = f"error {type(exc).__name__}"
            else:
                cost = commentary.cost_usd(reply.input_tokens, reply.output_tokens)
                total += cost
                entry.update(
                    text=reply.text,
                    stop_reason=reply.stop_reason,
                    input_tokens=reply.input_tokens,
                    output_tokens=reply.output_tokens,
                    cost_usd=cost,
                )
                status = f"{reply.stop_reason}, {reply.output_tokens} output tokens"
            entry["generated_at"] = datetime.now(timezone.utc).isoformat(
                timespec="seconds"
            )
            path = out_dir / f"{case['id']}-{n}.json"
            path.write_text(
                json.dumps(entry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            print(f"{case['id']}-{n}: {status}")
    print(
        f"{calls} calls, {total:.4f} USD, files under {out_dir.relative_to(ROOT.parent)}"
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--version", help="score only this recorded prompt version")
    parser.add_argument(
        "--record", action="store_true", help="regenerate the samples via the API"
    )
    parser.add_argument("--yes", action="store_true", help="confirm the paid calls")
    parser.add_argument("--case", help="record only this case id")
    parser.add_argument("--set", choices=tuple(SETS), help="record only this case set")
    parser.add_argument("--samples", type=int, default=SAMPLES, help="samples per case")
    args = parser.parse_args(argv)

    if args.record:
        sets = (args.set,) if args.set else tuple(SETS)
        record(load_cases(args.case, sets), args.samples, args.yes)
        return 0
    versions = [args.version] if args.version else recorded_versions()
    if not versions:
        print("nothing recorded yet: run with --record --yes", file=sys.stderr)
        return 1
    for version in versions:
        write_report(version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
