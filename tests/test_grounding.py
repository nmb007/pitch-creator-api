"""A match reason cannot introduce a number the quote does not contain."""

from src.pitches.grounding import ground_reason


def test_reason_drops_fact_outside_the_quote() -> None:
    quote = "The team rebuilt the mobile search flow for the travel client."
    reason = "Revenue grew 50%. The team rebuilt the mobile search flow."
    grounded = ground_reason(reason, quote)
    assert "50%" not in grounded
    assert "mobile search" in grounded
