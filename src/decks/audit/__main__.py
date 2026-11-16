"""Run: python -m src.decks.audit <deck-uuid>."""

from __future__ import annotations

import argparse
import logging
import sys
from uuid import UUID

from src.decks.audit import run_audit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit one deck import against its uploaded PowerPoint.",
    )
    parser.add_argument("deck_id", type=UUID, help="deck_imports.id")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    try:
        run_audit(args.deck_id)
    except LookupError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
