import re

from pydantic import BaseModel

from data.schemas import BankEntry


_EXCLUSION_PHRASES = (
    "LOAN DISBURSEMENT",
    "GST REFUND",
    "INTERNAL TRANSFER",
)
_EXCLUSION_TOKENS = tuple(
    tuple(re.findall(r"[a-z0-9]+", phrase.casefold()))
    for phrase in _EXCLUSION_PHRASES
)


class ScopeExclusion(BaseModel):
    source_index: int
    entry: BankEntry
    matched_phrase: str
    reason: str = "NON_TRANSACTION_EXCLUDED"


class ScopeFilterResult(BaseModel):
    remaining_entries: list[BankEntry]
    exclusions: list[ScopeExclusion]


def _matched_phrase(narration: str) -> str | None:
    tokens = re.findall(r"[a-z0-9]+", narration.casefold())
    for phrase, phrase_tokens in zip(_EXCLUSION_PHRASES, _EXCLUSION_TOKENS):
        width = len(phrase_tokens)
        if any(tokens[index : index + width] == list(phrase_tokens) for index in range(len(tokens))):
            return phrase
    return None


def filter_non_transactions(entries: list[BankEntry]) -> ScopeFilterResult:
    remaining_entries: list[BankEntry] = []
    exclusions: list[ScopeExclusion] = []

    for source_index, entry in enumerate(entries):
        phrase = _matched_phrase(entry.narration)
        if phrase is None:
            remaining_entries.append(entry)
            continue
        exclusions.append(
            ScopeExclusion(
                source_index=source_index,
                entry=entry,
                matched_phrase=phrase,
            )
        )

    return ScopeFilterResult(
        remaining_entries=remaining_entries,
        exclusions=exclusions,
    )
