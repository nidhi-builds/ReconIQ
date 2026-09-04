from typing import TypeVar

from pydantic import BaseModel

from data.schemas import BankEntry, LedgerEntry, ReconciliationResult


Entry = TypeVar("Entry", LedgerEntry, BankEntry)


class ConfirmedReference(BaseModel):
    ref_id: str
    original_amount: float
    match: ReconciliationResult


class RefundMatchResult(BaseModel):
    matches: list[ReconciliationResult]
    remaining_ledger_entries: list[LedgerEntry]
    remaining_bank_entries: list[BankEntry]


def _group_indices_by_reference(entries: list[Entry]) -> dict[str, list[int]]:
    grouped: dict[str, list[int]] = {}
    for index, entry in enumerate(entries):
        if entry.ref_id is None or not entry.ref_id.strip():
            continue
        grouped.setdefault(entry.ref_id, []).append(index)
    return grouped


def match_refund_reversals(
    remaining_ledger_entries: list[LedgerEntry],
    remaining_bank_entries: list[BankEntry],
    confirmed_references: list[ConfirmedReference],
) -> RefundMatchResult:
    ledger_by_ref = _group_indices_by_reference(remaining_ledger_entries)
    bank_by_ref = _group_indices_by_reference(remaining_bank_entries)
    confirmed_by_ref: dict[str, list[ConfirmedReference]] = {}
    for confirmation in confirmed_references:
        if confirmation.match.matched and confirmation.match.method == "exact_ref":
            confirmed_by_ref.setdefault(confirmation.ref_id, []).append(
                confirmation
            )

    candidates: list[tuple[int, int, ConfirmedReference]] = []
    for ledger_index, refund_ledger in enumerate(remaining_ledger_entries):
        refund_ref = refund_ledger.ref_id
        if (
            refund_ledger.amount >= 0
            or refund_ref is None
            or not refund_ref.endswith("-refund")
            or len(ledger_by_ref.get(refund_ref, [])) != 1
        ):
            continue
        bank_indices = bank_by_ref.get(refund_ref, [])
        if len(bank_indices) != 1:
            continue
        bank_index = bank_indices[0]
        refund_bank = remaining_bank_entries[bank_index]
        if refund_bank.amount >= 0:
            continue
        original_ref = refund_ref.removesuffix("-refund")
        confirmations = confirmed_by_ref.get(original_ref, [])
        if len(confirmations) != 1:
            continue
        confirmation = confirmations[0]
        expected_refund = -confirmation.original_amount
        if confirmation.original_amount <= 0 or not (
            refund_ledger.amount == refund_bank.amount == expected_refund
        ):
            continue
        candidates.append((ledger_index, bank_index, confirmation))

    matched_ledger_indices = {
        ledger_index for ledger_index, _, _ in candidates
    }
    matched_bank_indices = {bank_index for _, bank_index, _ in candidates}

    # Stage 5 reads but never reopens Stage 2 matches. Refund truth coverage
    # therefore unions Stage 2 and Stage 5 record IDs in the design gate.
    matches = [
        ReconciliationResult(
            record_ids=[
                remaining_ledger_entries[ledger_index].order_id,
                remaining_bank_entries[bank_index].bank_txn_id,
            ],
            matched=True,
            confidence=0.95,
            method="refund_reversal",
            exception_reason=None,
            reasoning=f"Refund of {confirmation.ref_id}",
        )
        for ledger_index, bank_index, confirmation in candidates
    ]

    return RefundMatchResult(
        matches=matches,
        remaining_ledger_entries=[
            entry
            for index, entry in enumerate(remaining_ledger_entries)
            if index not in matched_ledger_indices
        ],
        remaining_bank_entries=[
            entry
            for index, entry in enumerate(remaining_bank_entries)
            if index not in matched_bank_indices
        ],
    )
