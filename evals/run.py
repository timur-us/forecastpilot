"""Eval runner: scores recorded commentary offline and writes the report.

    python -m evals.run                      # score every recorded prompt version
    python -m evals.run --version 2026-10-23.1
    python -m evals.run --record --yes       # regenerate the samples (paid API calls)
    python -m evals.run --record --yes --case en-nvda-30-beats --samples 3

Gate policy: the pytest in tests/test_evals_suite.py is the CI gate for the
scorer itself (every metric must detect its failure case on artificial
answers, and the report must build from the recorded files). The pass rates
of a recorded baseline are a metric reported here, not a gate: a prompt
change is judged by comparing two reports, not by a threshold.

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

from evals.score import METRICS, SPEC_GAPS, payload_for, score

ROOT = Path(__file__).resolve().parent
CASES_DIR = ROOT / "cases"
RECORDED_DIR = ROOT / "recorded"
RESULTS_DIR = ROOT / "results"
SAMPLES = 3

# Token figures of two real replies (README example and a production call),
# used only for the cost estimate before recording.
_INPUT_TOKENS = 461
_OUTPUT_TOKENS_RANGE = (265, 676)


def load_cases(only: str | None = None) -> list:
    cases = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(CASES_DIR.glob("*.json"))
    ]
    if only:
        cases = [c for c in cases if c["id"] == only]
        if not cases:
            raise SystemExit(f"no case with id {only!r}")
    return cases


def recorded_versions() -> list:
    if not RECORDED_DIR.exists():
        return []
    return sorted(p.name for p in RECORDED_DIR.iterdir() if p.is_dir())


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
            {"case_id": case["id"], "sample": sample["sample"], "results": results}
        )
    return scored


def summarize(scored: list) -> dict:
    """Pass rates per metric over samples, over cases, over complete samples."""
    per_case = defaultdict(list)
    for entry in scored:
        per_case[entry["case_id"]].append(entry)
    metrics = {}
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
        metrics[metric] = {
            "samples_passed": sum(passed),
            "samples": len(passed),
            "cases_passed": sum(case_pass.values()),
            "cases": len(case_pass),
            "complete_passed": sum(e["results"][metric].passed for e in complete),
            "complete": len(complete),
            "unstable": unstable,
        }
    failures = [
        (e["case_id"], e["sample"], metric, e["results"][metric].reason)
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
    return {"metrics": metrics, "failures": failures, "hints": dict(hints)}


def _pct(part: int, whole: int) -> str:
    return f"{part}/{whole}" + (f" ({100 * part // whole}%)" if whole else "")


def render_table(summary: dict) -> str:
    rows = [
        "metric          samples        cases (all samples)  complete only   unstable"
    ]
    for metric, m in summary["metrics"].items():
        rows.append(
            f"{metric:<15} {_pct(m['samples_passed'], m['samples']):<14} "
            f"{_pct(m['cases_passed'], m['cases']):<20} "
            f"{_pct(m['complete_passed'], m['complete']):<15} {len(m['unstable'])}"
        )
    return "\n".join(rows)


def render_markdown(
    version: str, samples: list, cases: list, scored: list, summary: dict
) -> str:
    ids = sorted({s["case_id"] for s in samples})
    per_case = Counter(s["case_id"] for s in samples)
    complete = sum(1 for e in scored if e["results"]["complete"].passed)
    cost = sum(s.get("cost_usd") or 0 for s in samples)
    models = sorted({s["model"] for s in samples if s.get("model")})
    stamps = sorted(s["generated_at"] for s in samples if s.get("generated_at"))
    recorded = f"{stamps[0][:16]} to {stamps[-1][:16]} UTC" if stamps else "n/a"

    out = [f"# Commentary eval — prompt {version}", ""]
    out.append(
        f"Recorded {recorded} · model {', '.join(models) or 'n/a'} · "
        f"{len(ids)} cases × {max(per_case.values()) if per_case else 0} samples = "
        f"{len(samples)} samples ({complete} complete) · total cost {cost:.2f} USD"
    )
    out += [
        "",
        "With three samples per case the suite shows instability, not statistical "
        "significance; a difference of one or two cases between prompt versions is "
        "noise and is reported as such. A case passes a metric only when all of its "
        "samples pass.",
        "",
        "## Pass rates",
        "",
        "| Metric | Samples | Cases (all samples) | Complete samples only | Unstable cases |",
        "| --- | --- | --- | --- | --- |",
    ]
    for metric, m in summary["metrics"].items():
        out.append(
            f"| {metric} | {_pct(m['samples_passed'], m['samples'])} | "
            f"{_pct(m['cases_passed'], m['cases'])} | "
            f"{_pct(m['complete_passed'], m['complete'])} | "
            f"{', '.join(m['unstable']) or '—'} |"
        )

    out += ["", "## Failures", ""]
    if summary["failures"]:
        out += ["| Case | Sample | Metric | Reason |", "| --- | --- | --- | --- |"]
        for cid, n, metric, reason in summary["failures"]:
            out.append(f"| {cid} | {n} | {metric} | {_cell(reason)} |")
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

    out += ["", "## Spec gaps", ""]
    out.append(
        "Points the prompt leaves open. They are listed here as input for the next "
        "prompt revision and never counted as model failures."
    )
    out.append("")
    for tag, description in SPEC_GAPS:
        out.append(f"- **{tag}** {description}")

    out += [
        "",
        "## Cases",
        "",
        "| Case | Language | Horizon | Verdict | Covers |",
        "| --- | --- | --- | --- | --- |",
    ]
    for case in cases:
        if case["id"] in per_case:
            out.append(
                f"| {case['id']} | {case['language']} | {case['horizon']} | "
                f"{case['expect']['verdict']} | {_cell(case['covers'])} |"
            )
    return "\n".join(out) + "\n"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def build_report(version: str) -> tuple:
    """(markdown, summary) for one recorded prompt version."""
    cases = load_cases()
    samples = load_samples(version)
    if not samples:
        raise SystemExit(f"no recorded samples under {RECORDED_DIR / version}")
    scored = score_samples(samples, cases)
    summary = summarize(scored)
    return render_markdown(version, samples, cases, scored, summary), summary


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
    print(
        f"Recording {len(cases)} cases × {samples} samples = {calls} calls to the "
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
    parser.add_argument("--samples", type=int, default=SAMPLES, help="samples per case")
    args = parser.parse_args(argv)

    if args.record:
        record(load_cases(args.case), args.samples, args.yes)
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
