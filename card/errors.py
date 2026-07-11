"""Card-generation error types."""

from __future__ import annotations


class CardError(Exception):
    """Unrecoverable card-generation failure (nothing is written)."""


class CardValidationError(CardError):
    """LLM output failed schema validation; carries the error list."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors
