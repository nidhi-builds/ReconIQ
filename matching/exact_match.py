from typing import TypeVar

from pydantic import BaseModel

from data.schemas import (
    BankEntry,
    LedgerEntry,
    ReconciliationResult,
    SettlementEntry,
)


Entry = TypeVar("Entry", LedgerEntry, SettlementEntry, BankEntry)


class ExactMatchResult(BaseModel):
    matches: list[ReconciliationResult]
    remaining_ledger_entries: list[LedgerEntry]
    remaining_settlement_entries: list[SettlementEntry]
    remaining_bank_entries: list[BankEntry]


def _group_by_reference(entries: list[Entry]) -> dict[str, list[Entry]]:
    grouped: dict[str, list[Entry]] = {}
    for entry in entries:
        if entry.ref_id is None or not entry.ref_id.strip():
            continue
        grouped.setdefault(entry.ref_id, []).append(entry)
    return grouped


def match_exact_references(
    ledger_entries: list[LedgerEntry],
    settlement_entries: list[SettlementEntry],
    bank_entries: list[BankEntry],
) -> ExactMatchResult:
    ledger_by_ref = _group_by_reference(ledger_entries)
    settlement_by_ref = _group_by_reference(settlement_entries)
    bank_by_ref = _group_by_reference(bank_entries)
    matched_refs = {
        ref_id
        for ref_id, ledgers in ledger_by_ref.items()
        if len(ledgers) == 1
        and len(settlement_by_ref.get(ref_id, [])) == 1
        and len(bank_by_ref.get(ref_id, [])) == 1
    }

    matches = [
        ReconciliationResult(
            record_ids=[
                entry.order_id,
                settlement_by_ref[entry.ref_id][0].settlement_id,
                bank_by_ref[entry.ref_id][0].bank_txn_id,
            ],
            matched=True,
            confidence=1.0,
            method="exact_ref",
            exception_reason=None,
            reasoning=None,
        )
        for entry in ledger_entries
        if entry.ref_id in matched_refs
    ]

    return ExactMatchResult(
        matches=matches,
        remaining_ledger_entries=[
            entry for entry in ledger_entries if entry.ref_id not in matched_refs
        ],
        remaining_settlement_entries=[
            entry for entry in settlement_entries if entry.ref_id not in matched_refs
        ],
        remaining_bank_entries=[
            entry for entry in bank_entries if entry.ref_id not in matched_refs
        ],
    )
