from collections import Counter
from datetime import date, timedelta
from itertools import combinations
from typing import TypeVar

from pydantic import BaseModel

from data.schemas import (
    BankEntry,
    LedgerEntry,
    ReconciliationResult,
    SettlementEntry,
)
from matching.settlement_windows import settlement_window_days


Entry = TypeVar("Entry", LedgerEntry, SettlementEntry)
Part = tuple[int, int]
Candidate = tuple[tuple[Part, ...], int]


class SplitSettlementMatchResult(BaseModel):
    matches: list[ReconciliationResult]
    remaining_ledger_entries: list[LedgerEntry]
    remaining_settlement_entries: list[SettlementEntry]
    remaining_bank_entries: list[BankEntry]


def find_split_candidates(
    ledger_entries: list[LedgerEntry],
    settlement_entries: list[SettlementEntry],
    bank_entries: list[BankEntry],
) -> list[Candidate]:
    ledger_by_ref = _group_indices_by_reference(ledger_entries)
    settlement_by_ref = _group_indices_by_reference(settlement_entries)
    parts_by_batch: dict[tuple[str, date], list[Part]] = {}

    for ledger_index, ledger in enumerate(ledger_entries):
        if (
            ledger.ref_id is None
            or len(ledger_by_ref.get(ledger.ref_id, [])) != 1
            or ledger.currency != "INR"
            or ledger.amount <= 0
        ):
            continue
        settlement_indices = settlement_by_ref.get(ledger.ref_id, [])
        if len(settlement_indices) != 1:
            continue
        settlement_index = settlement_indices[0]
        settlement = settlement_entries[settlement_index]
        if ledger.payment_method != settlement.payment_method:
            continue
        key = (settlement.payment_method, settlement.settlement_date)
        parts_by_batch.setdefault(key, []).append(
            (ledger_index, settlement_index)
        )

    candidates: list[Candidate] = []
    for bank_index, bank in enumerate(bank_entries):
        if not _has_blank_reference(bank):
            continue
        for (method, settlement_date), parts in parts_by_batch.items():
            try:
                window_days = settlement_window_days(method)
            except KeyError:
                continue
            if not (
                settlement_date
                <= bank.value_date
                <= settlement_date + timedelta(days=window_days)
            ):
                continue
            for size in range(2, min(5, len(parts)) + 1):
                for combo in combinations(parts, size):
                    total = round(
                        sum(
                            settlement_entries[settlement_index].net_amount
                            for _, settlement_index in combo
                        ),
                        2,
                    )
                    if round(abs(total - bank.amount), 2) < 0.50:
                        candidates.append((combo, bank_index))
    return candidates


def _group_indices_by_reference(entries: list[Entry]) -> dict[str, list[int]]:
    grouped: dict[str, list[int]] = {}
    for index, entry in enumerate(entries):
        if entry.ref_id is None or not entry.ref_id.strip():
            continue
        grouped.setdefault(entry.ref_id, []).append(index)
    return grouped


def _has_blank_reference(entry: BankEntry) -> bool:
    # Kept local while blank-reference handling has no broader shared policy.
    return entry.ref_id is None or not entry.ref_id.strip()


def match_split_settlements(
    ledger_entries: list[LedgerEntry],
    settlement_entries: list[SettlementEntry],
    bank_entries: list[BankEntry],
) -> SplitSettlementMatchResult:
    # ponytail: O(n choose k), k=2..5, is for hackathon-size batches only;
    # replace with bounded candidate indexing if production volume requires it.
    candidates = find_split_candidates(
        ledger_entries, settlement_entries, bank_entries
    )

    bank_counts = Counter(bank_index for _, bank_index in candidates)
    ledger_id_counts = Counter(
        ledger_entries[ledger_index].order_id
        for combo, _ in candidates
        for ledger_index, _ in combo
    )
    settlement_id_counts = Counter(
        settlement_entries[settlement_index].settlement_id
        for combo, _ in candidates
        for _, settlement_index in combo
    )
    resolved = [
        candidate
        for candidate in candidates
        if bank_counts[candidate[1]] == 1
        and all(
            ledger_id_counts[ledger_entries[ledger_index].order_id] == 1
            and settlement_id_counts[
                settlement_entries[settlement_index].settlement_id
            ]
            == 1
            for ledger_index, settlement_index in candidate[0]
        )
    ]
    matched_ledger_indices = {
        ledger_index for combo, _ in resolved for ledger_index, _ in combo
    }
    matched_settlement_indices = {
        settlement_index for combo, _ in resolved for _, settlement_index in combo
    }
    matched_bank_indices = {bank_index for _, bank_index in resolved}
    matches = [
        ReconciliationResult(
            record_ids=[
                *[
                    ledger_entries[index].order_id
                    for index in sorted(ledger_index for ledger_index, _ in combo)
                ],
                *[
                    settlement_entries[index].settlement_id
                    for index in sorted(
                        settlement_index for _, settlement_index in combo
                    )
                ],
                bank_entries[bank_index].bank_txn_id,
            ],
            matched=True,
            confidence=0.85,
            method="split_settlement",
            exception_reason=None,
            reasoning=(
                f"Unique {len(combo)}-part settlement net-total matched one bank entry "
                "within the allowed window."
            ),
        )
        for combo, bank_index in resolved
    ]

    return SplitSettlementMatchResult(
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
