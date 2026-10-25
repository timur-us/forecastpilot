"""The pip-audit gate in CI, run against recorded reports instead of a live audit."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / ".github" / "scripts" / "pip-audit-gate.sh"
FIXTURES = ROOT / "tests" / "fixtures" / "pip_audit"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("jq") is None,
    reason="the gate needs bash and jq",
)


def run_gate(report, *, as_argument=False):
    env = dict(os.environ)
    env.pop("PIP_AUDIT_JSON", None)
    args = ["bash", str(GATE)]
    if as_argument:
        args.append(str(report))
    else:
        env["PIP_AUDIT_JSON"] = str(report)
    return subprocess.run(
        args, cwd=ROOT, env=env, capture_output=True, text=True, timeout=30
    )


def test_clean_report_passes():
    result = run_gate(FIXTURES / "clean.json")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 dependencies audited, 0 finding(s) with a fix, 0 without one" in (
        result.stdout
    )


def test_report_path_can_be_an_argument():
    result = run_gate(FIXTURES / "clean.json", as_argument=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_finding_with_a_fix_fails():
    result = run_gate(FIXTURES / "fixable.json")
    assert result.returncode == 1, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    assert lines[0].split() == ["NAME", "VERSION", "ID", "FIX_VERSIONS"]
    assert lines[1].split() == [
        "example-lib",
        "1.0.0",
        "GHSA-0000-0000-0001",
        "1.0.1,1.1.0",
    ]
    assert lines[2].split() == ["example-dep", "2.3.4", "PYSEC-0000-2", "-"]
    assert (
        "::error::example-lib 1.0.0: GHSA-0000-0000-0001 is fixed in 1.0.1,1.1.0"
        in lines
    )
    assert "::warning::example-dep 2.3.4: PYSEC-0000-2 has no fixed version yet" in (
        lines
    )


def test_finding_without_a_fix_only_warns():
    result = run_gate(FIXTURES / "unfixable.json")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "::warning::example-dep 2.3.4: PYSEC-0000-2 has no fixed version yet" in (
        result.stdout
    )
    assert "::error::" not in result.stdout


def test_skipped_dependency_without_vulns_key_passes():
    result = run_gate(FIXTURES / "skipped.json")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "::notice::pip-audit skipped example-local:" in result.stdout
    assert "1 dependencies audited" in result.stdout


def test_missing_report_is_a_tool_failure(tmp_path):
    result = run_gate(tmp_path / "missing.json")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "::error::pip-audit failed: no report" in result.stdout


def test_report_without_dependencies_is_a_tool_failure():
    result = run_gate(FIXTURES / "no_dependencies.json")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "has no dependency list" in result.stdout
