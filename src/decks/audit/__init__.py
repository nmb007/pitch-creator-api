"""Read-only audit of one deck import against its uploaded PowerPoint."""

from src.decks.audit.checks import audit_deck
from src.decks.audit.report import format_report
from src.decks.audit.types import AuditReport

__all__ = ["AuditReport", "audit_deck", "format_report", "run_audit"]


def run_audit(deck_id) -> AuditReport:  # noqa: ANN001
    """Load the deck, run checks, write the report, and return it."""
    from uuid import UUID

    from src.decks import service as deck_service
    from src.decks.audit.report import format_report as render

    deck_uuid = deck_id if isinstance(deck_id, UUID) else UUID(str(deck_id))
    with deck_service.get_sync_session() as session:
        report = audit_deck(session, deck_uuid)
    text = render(report)
    out_dir = deck_service.storage_root() / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{deck_uuid}.md"
    out_path.write_text(text, encoding="utf-8")
    print(text, end="" if text.endswith("\n") else "\n")
    print(f"Wrote {out_path}")
    return report
