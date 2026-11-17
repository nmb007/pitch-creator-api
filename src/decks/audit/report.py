"""Render a deck audit as Markdown."""

from __future__ import annotations

from src.decks.audit.types import HARD_ISSUE_STATUSES, AuditReport, CheckResult


def format_report(report: AuditReport) -> str:
    hash_label = {
        True: "match",
        False: "mismatch",
        None: "unknown",
    }[report.file_hash_ok]
    lines = [
        "# Deck audit",
        f"File: {report.file_name}",
        f"Deck: {report.deck_id}  status: {report.deck_status}  file hash: {hash_label}",
        f"Score: {report.score_percent}% ({report.passed} of {report.total} checks)",
        f"Studies fully matched: {report.studies_fully_matched} of {len(report.studies)}",
        f"Text in the file stored by no study: {report.unclaimed_count} blocks",
        f"Field overlaps to review: {len(report.field_overlaps)}",
        "",
        "## Deck-only issues",
    ]
    deck_issues = [check for check in report.deck_checks if check.status in HARD_ISSUE_STATUSES]
    if deck_issues:
        for check in deck_issues:
            lines.append(f"- {_prefix(check)}: {check.message}")
    else:
        lines.append("- None")

    issue_studies = [study for study in report.studies if study.issue_count > 0]
    lines.append("")
    lines.append("## Studies with issues")
    if not report.studies:
        lines.append("- No studies stored for this deck")
    elif not issue_studies:
        lines.append("- None")
    else:
        for study in issue_studies:
            slides = _slide_label(study.slide_numbers)
            kept = "  —  kept from earlier import" if study.kept_from_earlier else ""
            lines.append(
                f"### section: {study.section}, case study: {study.title}  —  "
                f"{slides}  —  {study.issue_count} issues{kept}"
            )
            for check in study.checks:
                if check.status not in HARD_ISSUE_STATUSES:
                    continue
                lines.append(f"- {_prefix(check)}: {check.message}")
            lines.append("")

    missing = [check for check in report.unclaimed if check.status != "match"]
    lines.append("## Text in the file stored by no study")
    if not missing:
        lines.append("- None")
    else:
        for check in missing:
            lines.append(f"- {check.message}")

    lines.append("")
    lines.append("## Field overlaps (review)")
    lines.append(
        "The same slide block appears in more than one field. Ingest allows this "
        "(for example approach and solution). Confirm each overlap is intentional."
    )
    overlaps = report.field_overlaps
    if not overlaps:
        lines.append("- None")
    else:
        for check in overlaps:
            lines.append(f"- Review: {check.message}")
    lines.append("")
    return "\n".join(lines)


def _prefix(check: CheckResult) -> str:
    return {
        "match": "Match",
        "missing": "Missing",
        "incorrect": "Incorrect",
        "extra": "Extra",
        "review": "Review",
    }[check.status]


def _slide_label(numbers: list[int]) -> str:
    if not numbers:
        return "no slides"
    if len(numbers) == 1:
        return f"slide {numbers[0]}"
    return f"slides {numbers[0]}–{numbers[-1]}"
