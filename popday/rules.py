"""Detection phrases and read-only rules metadata."""

from __future__ import annotations

from dataclasses import dataclass


INCLUDE_PHRASES = [
    "investor day",
    "analyst day",
    "capital markets day",
    "capital market day",
    "investor seminar",
    "investor event",
    "strategy day",
    "technology day",
    "r&d day",
    "teach-in",
]

ROUTINE_PHRASES = [
    "earnings call",
    "quarterly results call",
    "annual meeting",
    "shareholder meeting",
]

ALERT_REQUIREMENTS = [
    "Filing has not been processed before.",
    "Form type is 8-K or 6-K.",
    "A qualifying investor-event phrase is found.",
    "The filing appears to announce a future event.",
    "A future event date is extracted from nearby text.",
    "The event has not already been alerted.",
]


@dataclass(frozen=True)
class Rule:
    rule_type: str
    phrase: str
    description: str
    active: bool = True


def default_rules() -> list[Rule]:
    rules: list[Rule] = []
    for phrase in INCLUDE_PHRASES:
        rules.append(
            Rule(
                rule_type="include",
                phrase=phrase,
                description="Qualifying investor-event phrase.",
            )
        )
    for phrase in ROUTINE_PHRASES:
        rules.append(
            Rule(
                rule_type="routine_context",
                phrase=phrase,
                description="Context-sensitive routine phrase; not an automatic exclusion.",
            )
        )
    return rules
