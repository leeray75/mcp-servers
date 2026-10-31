"""R56/R57: the quality gate itself, and the requirement-to-test map, are pinned.

R56 is the requirement that is easy to satisfy by accident and easy to lose silently: someone weakens
``--cov-fail-under``, drops ``strict`` from mypy's config, or removes a prerequisite from ``check``, and
every test still passes while the gate no longer gates. These tests read the build files the same way CI
does and refuse that.

R57 requires every numbered requirement in ``docs/SPEC.md`` to be named by at least one test. Rather than
trust a hand-maintained table, the second test recomputes the map from the repository and fails on the
first requirement no test cites.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAKEFILE = ROOT / "Makefile"
PYPROJECT = ROOT / "pyproject.toml"
SPEC = ROOT / "docs" / "SPEC.md"
TESTS = ROOT / "tests"
REPORT = ROOT / "docs" / "reports" / "initial-build" / "implementation-report.md"

#: Requirements exempt from the citation sweep because they describe this very test.
SELF_CITING = {"56", "57"}

#: Requirements removed by an amendment (S9b); the R-map sweeps skip them. R23 is historical in the report.
SUPERSEDED_REQUIREMENTS = {"23"}


def _target(body: str, name: str) -> str:
    """The recipe lines of one Makefile target, as one string."""
    match = re.search(rf"^{name}:.*?(?=^\S|\Z)", body, re.MULTILINE | re.DOTALL)
    assert match is not None, f"Makefile has no {name} target"
    return match.group(0)


# ---------------------------------------------------------------------------------- R56: make check


def test_check_runs_the_three_gates() -> None:
    """R56: ``make check`` is ruff, mypy and pytest -- all three, as prerequisites."""
    body = MAKEFILE.read_text(encoding="utf-8")
    line = re.search(r"^check:([^#\n]*)", body, re.MULTILINE)
    assert line is not None, "Makefile has no check target"
    prereqs = line.group(1).split()
    assert prereqs == ["lint", "typecheck", "test"], prereqs


def test_lint_is_ruff_check_and_format_check() -> None:
    """R56: the lint gate is ruff lint plus a format *check*, never a silent format write."""
    recipe = _target(MAKEFILE.read_text(encoding="utf-8"), "lint")
    assert "ruff check ." in recipe
    assert "ruff format --check ." in recipe
    assert "ruff format ." not in recipe.replace("ruff format --check .", ""), (
        "the gate must check formatting, not rewrite files"
    )


def test_typecheck_is_mypy() -> None:
    """R56: the type gate runs mypy, whose strictness lives in pyproject (pinned next)."""
    recipe = _target(MAKEFILE.read_text(encoding="utf-8"), "typecheck")
    assert "mypy" in recipe


def test_pyproject_holds_the_strict_and_coverage_gates() -> None:
    """R56: mypy is strict over src *and* tests, and pytest fails the run under 90% branch coverage."""
    body = PYPROJECT.read_text(encoding="utf-8")
    assert re.search(r"^\s*strict\s*=\s*true", body, re.MULTILINE), "mypy must stay --strict"
    assert re.search(r'^files\s*=\s*\[\s*"src",\s*"tests"\s*\]', body, re.MULTILINE), (
        "mypy must cover the test layer too, or the tests rot untyped"
    )
    assert "--cov-fail-under=90" in body, "the 90% coverage gate must stay in addopts"
    assert "--cov=twitch_mcp" in body
    assert re.search(r"^\s*branch\s*=\s*true", body, re.MULTILINE), "coverage must count branches"
    assert '"-m" ' in body or "-m 'not live'" in body, (
        "live tests must stay opt-in, never collected by default (R55/A1-D5)"
    )
    assert "--strict-markers" in body
    assert "pytest-timeout>=2.3" in body, "the dev extra must carry pytest-timeout (S11)"
    assert re.search(r"^timeout\s*=\s*30\s*$", body, re.MULTILINE), "per-test timeout is 30s (S12)"
    assert re.search(r'^timeout_method\s*=\s*"signal"\s*$', body, re.MULTILINE), (
        "timeout_method must be signal so a hung test fails instead of exiting pytest (S12)"
    )


# --------------------------------------------------------------------------------- R57: the R-map


def _spec_requirements() -> set[str]:
    body = SPEC.read_text(encoding="utf-8")
    return set(re.findall(r"^- \*\*R(\d+)\*\*", body, re.MULTILINE))


def _cited_requirements() -> set[str]:
    cited: set[str] = set()
    for path in sorted(TESTS.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        cited.update(re.findall(r"\bR(\d+)\b", text))
    return cited


def test_every_specification_requirement_is_named_by_a_test() -> None:
    """R57: no requirement is implemented without a test that names it."""
    requirements = _spec_requirements()
    assert requirements, "docs/SPEC.md must carry the numbered requirements"
    missing = sorted(
        requirements - _cited_requirements() - SELF_CITING - SUPERSEDED_REQUIREMENTS, key=int
    )
    assert not missing, f"no test cites {', '.join('R' + r for r in missing)} (R57)"


def test_the_report_has_a_row_for_every_requirement() -> None:
    """A8: the implementation report carries a traceability row for each of R1-R57."""
    body = REPORT.read_text(encoding="utf-8")
    rows = set(re.findall(r"^\| R(\d+) \|", body, re.MULTILINE))
    missing = sorted(_spec_requirements() - rows, key=int)
    assert not missing, (
        f"implementation-report.md has no row for {', '.join('R' + r for r in missing)}"
    )


def test_every_traceability_row_cites_a_real_test_of_its_own_file() -> None:
    """R57/A8: each table row must cite at least one test that exists in the file it names.

    The check is deliberately *per row*, not a count over the whole report. A count can be satisfied by
    one genuine test cited 57 times, or by 57 empty rows plus valid citations in the prose, and both
    would leave a requirement with no traceable test while the gate stayed green. Walking the rows is
    what makes the table, rather than the document, the thing under test.
    """
    body = REPORT.read_text(encoding="utf-8")
    rows = re.findall(r"^\| (R\d+) \| ([^|]*)\|$", body, re.MULTILINE)
    assert len(rows) == len(_spec_requirements()), (
        "the traceability table must hold exactly one row per requirement"
    )
    defined: dict[str, set[str]] = {
        path.name: set(
            re.findall(r"^(?:async )?def (\w+)", path.read_text(encoding="utf-8"), re.MULTILINE)
        )
        for path in TESTS.glob("*.py")
    }
    wrong: list[str] = []
    uncovered: list[str] = []
    for label, cell in rows:
        if label.removeprefix("R") in SUPERSEDED_REQUIREMENTS:
            continue
        cited = re.findall(r"\b(test_\w+\.py)::(\w+)\b", cell)
        wrong += [
            f"{label}: {name}::{fn}" for name, fn in cited if fn not in defined.get(name, set())
        ]
        if not any(fn in defined.get(name, set()) for name, fn in cited):
            uncovered.append(label)
    assert not wrong, f"report cites tests not defined in the named file: {sorted(wrong)}"
    assert not uncovered, f"requirements with no resolvable test citation in the table: {uncovered}"


def test_the_citation_map_is_not_stale() -> None:
    """R57: a test that cites a requirement the spec no longer has is a lie worth failing on."""
    orphan = sorted(
        _cited_requirements() - _spec_requirements() - SELF_CITING - SUPERSEDED_REQUIREMENTS,
        key=int,
    )
    assert not orphan, (
        f"tests cite {', '.join('R' + r for r in orphan)}, which docs/SPEC.md does not define"
    )
