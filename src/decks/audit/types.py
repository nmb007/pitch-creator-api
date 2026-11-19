"""Shared shapes for deck audit results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

CheckStatus = Literal["match", "missing", "incorrect", "extra", "review"]

HARD_ISSUE_STATUSES = frozenset({"missing", "incorrect", "extra"})


@dataclass
class CheckResult:
    status: CheckStatus
    message: str
    study_key: str | None = None


@dataclass
class StudyReport:
    section: str
    title: str
    slide_numbers: list[int]
    kept_from_earlier: bool
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def issue_count(self) -> int:
        return sum(1 for check in self.checks if check.status in HARD_ISSUE_STATUSES)

    @property
    def review_count(self) -> int:
        return sum(1 for check in self.checks if check.status == "review")

    @property
    def fully_matched(self) -> bool:
        return bool(self.checks) and self.issue_count == 0


@dataclass
class AuditReport:
    deck_id: UUID
    file_name: str
    deck_status: str
    file_hash_ok: bool | None
    studies: list[StudyReport] = field(default_factory=list)
    deck_checks: list[CheckResult] = field(default_factory=list)
    unclaimed: list[CheckResult] = field(default_factory=list)
    attention: list[CheckResult] = field(default_factory=list)

    def all_checks(self) -> list[CheckResult]:
        checks = list(self.deck_checks)
        for study in self.studies:
            checks.extend(study.checks)
        checks.extend(self.unclaimed)
        return checks

    def scored_checks(self) -> list[CheckResult]:
        """Checks that affect the headline score. Review overlaps are excluded."""
        return [check for check in self.all_checks() if check.status != "review"]

    @property
    def passed(self) -> int:
        return sum(1 for check in self.scored_checks() if check.status == "match")

    @property
    def total(self) -> int:
        return len(self.scored_checks())

    @property
    def score_percent(self) -> float:
        if self.total == 0:
            return 100.0
        return round(100.0 * self.passed / self.total, 1)

    @property
    def studies_fully_matched(self) -> int:
        return sum(1 for study in self.studies if study.fully_matched)

    @property
    def unclaimed_count(self) -> int:
        return sum(1 for check in self.unclaimed if check.status != "match")

    @property
    def field_overlaps(self) -> list[CheckResult]:
        return [
            check for study in self.studies for check in study.checks if check.status == "review"
        ]
