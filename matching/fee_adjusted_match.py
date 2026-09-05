from datetime import timedelta
from typing import TypeVar

from pydantic import BaseModel

from data.schemas import (
    BankEntry,
    LedgerEntry,
    ReconciliationResult,
    SettlementEntry,
)
from matching.rate_card import calculate_settlement_amounts
from matching.settlement_windows import settlement_window_days


Entry = TypeVar("Entry", LedgerEntry, SettlementEntry)


class FeeAdjustedMatchResult(BaseModel):
    matches: list[ReconciliationResult]
    remaining_ledger_entries: list[LedgerEntry]
    remaining_settlement_entries: list[SettlementEntry]
    remaining_bank_entries: list[BankEntry]


def _group_indices_by_reference(entries: list[Entry]) -> dict[str, list[int]]:
    grouped: dict[str, list[int]] = {}
    for index, entry in enumerate(entries):
        if entry.ref_id is None or not entry.ref_id.strip():
            continue
        grouped.setdefault(entry.ref_id, []).append(index)
    return grouped


def _is_valid_pair(ledger: LedgerEntry, settlement: SettlementEntry) -> bool:
    if (
        ledger.amount <= 0
        or ledger.currency != "INR"
        or ledger.payment_method != settlement.payment_method
        or ledger.amount != settlement.gross_amount
    ):
        return False
    try:
        fee, gst_on_fee, net_amount = calculate_settlement_amounts(
            ledger.amount, ledger.payment_method
        )
    except KeyError:
        return False
    return (
        settlement.fee == fee
        and settlement.gst_on_fee == gst_on_fee
        and settlement.net_amount == net_amount
    )


def match_fee_adjusted(
    ledger_entries: list[LedgerEntry],
    settlement_entries: list[SettlementEntry],
    bank_entries: list[BankEntry],
) -> FeeAdjustedMatchResult:
    ledger_by_ref = _group_indices_by_reference(ledger_entries)
    settlement_by_ref = _group_indices_by_reference(settlement_entries)
    pairs: list[tuple[int, int]] = []

    for ledger_index, ledger in enumerate(ledger_entries):
        if ledger.ref_id is None or len(ledger_by_ref.get(ledger.ref_id, [])) != 1:
            continue
        settlement_indices = settlement_by_ref.get(ledger.ref_id, [])
        if len(settlement_indices) != 1:
            continue
        settlement_index = settlement_indices[0]
        if _is_valid_pair(ledger, settlement_entries[settlement_index]):
            pairs.append((ledger_index, settlement_index))

    # Hard negatives are structurally excluded by their nonblank bank ref_id;
    # the amount tolerance alone does not discriminate a close-but-wrong amount.
    blank_bank_indices = [
        index
        for index, entry in enumerate(bank_entries)
        if entry.ref_id is None or not entry.ref_id.strip()
    ]
    pair_candidates: dict[int, list[int]] = {}
    bank_candidates: dict[int, list[int]] = {
        index: [] for index in blank_bank_indices
    }

    for pair_index, (_, settlement_index) in enumerate(pairs):
        settlement = settlement_entries[settlement_index]
        try:
            window_days = settlement_window_days(settlement.payment_method)
        except KeyError:
            pair_candidates[pair_index] = []
            continue
        candidates = [
            bank_index
            for bank_index in blank_bank_indices
            if round(
                abs(bank_entries[bank_index].amount - settlement.net_amount), 2
            )
            < 0.50
            and settlement.settlement_date
            <= bank_entries[bank_index].value_date
            <= settlement.settlement_date + timedelta(days=window_days)
        ]
        pair_candidates[pair_index] = candidates
        for bank_index in candidates:
            bank_candidates[bank_index].append(pair_index)

    resolved = [
        (pair_index, candidates[0])
        for pair_index, candidates in pair_candidates.items()
        if len(candidates) == 1
        and len(bank_candidates[candidates[0]]) == 1
    ]
    matched_ledger_indices = {pairs[index][0] for index, _ in resolved}
    matched_settlement_indices = {pairs[index][1] for index, _ in resolved}
    matched_bank_indices = {bank_index for _, bank_index in resolved}
    matches = [
        ReconciliationResult(
            record_ids=[
                ledger_entries[pairs[pair_index][0]].order_id,
                settlement_entries[pairs[pair_index][1]].settlement_id,
                bank_entries[bank_index].bank_txn_id,
            ],
            matched=True,
            confidence=0.9,
            method="fee_adjusted_window",
            exception_reason=None,
            reasoning="Unique fee-adjusted settlement net amount matched one bank entry within the allowed window.",
        )
        for pair_index, bank_index in resolved
    ]

    return FeeAdjustedMatchResult(
        matches=matches,
        remaining_ledger_entries=[
            entry
            for index, entry in enumerate(ledger_entries)
            if index not in matched_ledger_indices
        ],
        remaining_settlement_entries=[
            entry
            for index, entry in enumerate(settlement_entries)
            if index not in matched_settlement_indices
        ],
        remaining_bank_entries=[
            entry
            for index, entry in enumerate(bank_entries)
            if index not in matched_bank_indices
        ],
    )
