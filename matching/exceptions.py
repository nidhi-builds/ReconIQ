import re
from datetime import timedelta

from pydantic import BaseModel, Field

from data.schemas import BankEntry, LedgerEntry, ReconciliationResult, SettlementEntry
from matching.settlement_windows import settlement_window_days
from matching.split_settlement import find_split_candidates


class ExceptionEvidence(BaseModel):
    record_ids: list[str] = Field(min_length=1)
    ledger_ref_id: str | None
    settlement_ref_id: str | None
    currency: str
    has_ledger_or_settlement: bool = True
    days_since_expected: int | None = None
    method_window_days: int | None = None
    amount_difference: float | None = None
    refund_candidate: bool = False
    split_candidate: bool = False


def _is_blank(value: str | None) -> bool:
    return value is None or not value.strip()


def _narration_mentions(narration: str, ref_id: str) -> bool:
    return re.search(
        rf"(?<![A-Za-z0-9]){re.escape(ref_id)}(?![A-Za-z0-9])",
        narration,
        re.IGNORECASE,
    ) is not None


def _bank_reference_compatible(bank: BankEntry, ref_id: str | None) -> bool:
    return _is_blank(bank.ref_id) or bank.ref_id == ref_id


def build_exception_evidence(
    ledger_entries: list[LedgerEntry],
    settlement_entries: list[SettlementEntry],
    bank_entries: list[BankEntry],
) -> list[ExceptionEvidence]:
    refund_pairs: list[tuple[int, int]] = []
    for ledger_index, ledger in enumerate(ledger_entries):
        if (
            ledger.amount >= 0
            or _is_blank(ledger.ref_id)
            or not (ledger.ref_id or "").endswith("-refund")
        ):
            continue
        matching_ledgers = [
            row for row in ledger_entries if row.ref_id == ledger.ref_id
        ]
        matching_banks = [
            index
            for index, bank in enumerate(bank_entries)
            if bank.ref_id == ledger.ref_id and bank.amount < 0
        ]
        if len(matching_ledgers) == len(matching_banks) == 1:
            refund_pairs.append((ledger_index, matching_banks[0]))

    refund_ledger_indices = {ledger_index for ledger_index, _ in refund_pairs}
    refund_bank_indices = {bank_index for _, bank_index in refund_pairs}
    split_candidates = find_split_candidates(
        ledger_entries, settlement_entries, bank_entries
    )
    split_components: list[set[str]] = []
    for combo, bank_index in split_candidates:
        component = {
            *[ledger_entries[index].order_id for index, _ in combo],
            *[settlement_entries[index].settlement_id for _, index in combo],
            bank_entries[bank_index].bank_txn_id,
        }
        overlaps = [group for group in split_components if group & component]
        for group in overlaps:
            component.update(group)
            split_components.remove(group)
        split_components.append(component)
    split_ledger_indices = {
        ledger_index
        for combo, _ in split_candidates
        for ledger_index, _ in combo
    }
    split_settlement_indices = {
        settlement_index
        for combo, _ in split_candidates
        for _, settlement_index in combo
    }
    split_bank_indices = {bank_index for _, bank_index in split_candidates}

    unused_settlements = set(range(len(settlement_entries))) - split_settlement_indices
    source_pairs: list[tuple[LedgerEntry, SettlementEntry]] = []

    for ledger_index, ledger in enumerate(ledger_entries):
        if ledger_index in refund_ledger_indices | split_ledger_indices:
            continue
        candidates = [
            index
            for index in unused_settlements
            if (
                not _is_blank(ledger.ref_id)
                and settlement_entries[index].ref_id == ledger.ref_id
            )
            or (
                _is_blank(ledger.ref_id)
                and _is_blank(settlement_entries[index].ref_id)
                and settlement_entries[index].gross_amount == ledger.amount
                and settlement_entries[index].payment_method == ledger.payment_method
                and settlement_entries[index].settlement_date
                == ledger.order_date + timedelta(days=1)
            )
        ]
        if len(candidates) == 1:
            settlement_index = candidates[0]
            unused_settlements.remove(settlement_index)
            source_pairs.append((ledger, settlement_entries[settlement_index]))

    unused_banks = (
        set(range(len(bank_entries))) - refund_bank_indices - split_bank_indices
    )
    assignments: dict[int, int] = {}

    for pair_index, (ledger, _) in enumerate(source_pairs):
        if _is_blank(ledger.ref_id):
            continue
        candidates = [
            index
            for index in unused_banks
            if _bank_reference_compatible(bank_entries[index], ledger.ref_id)
            and _narration_mentions(bank_entries[index].narration, ledger.ref_id or "")
        ]
        if len(candidates) == 1:
            assignments[pair_index] = candidates[0]
            unused_banks.remove(candidates[0])

    for pair_index, (_, settlement) in enumerate(source_pairs):
        if pair_index in assignments:
            continue
        exact_amount = [
            index
            for index in unused_banks
            if _bank_reference_compatible(bank_entries[index], settlement.ref_id)
            and bank_entries[index].amount == settlement.net_amount
        ]
        if len(exact_amount) == 1:
            assignments[pair_index] = exact_amount[0]
            unused_banks.remove(exact_amount[0])

    for pair_index, (_, settlement) in enumerate(source_pairs):
        if pair_index in assignments:
            continue
        same_date = [
            index
            for index in unused_banks
            if _bank_reference_compatible(bank_entries[index], settlement.ref_id)
            and bank_entries[index].value_date == settlement.settlement_date
        ]
        if len(same_date) == 1:
            assignments[pair_index] = same_date[0]
            unused_banks.remove(same_date[0])

    evidence_rows = [
        ExceptionEvidence(
            record_ids=[
                ledger_entries[ledger_index].order_id,
                bank_entries[bank_index].bank_txn_id,
            ],
            ledger_ref_id=ledger_entries[ledger_index].ref_id,
            settlement_ref_id=None,
            currency=ledger_entries[ledger_index].currency,
            amount_difference=round(
                abs(
                    ledger_entries[ledger_index].amount
                    - bank_entries[bank_index].amount
                ),
                2,
            ),
            refund_candidate=True,
        )
        for ledger_index, bank_index in refund_pairs
    ]
    for component in split_components:
        component_ledgers = [
            row for row in ledger_entries if row.order_id in component
        ]
        component_settlements = [
            row for row in settlement_entries if row.settlement_id in component
        ]
        evidence_rows.append(
            ExceptionEvidence(
                record_ids=[
                    *[row.order_id for row in component_ledgers],
                    *[row.settlement_id for row in component_settlements],
                    *[
                        row.bank_txn_id
                        for row in bank_entries
                        if row.bank_txn_id in component
                    ],
                ],
                ledger_ref_id=component_ledgers[0].ref_id,
                settlement_ref_id=component_settlements[0].ref_id,
                currency=component_ledgers[0].currency,
                split_candidate=True,
            )
        )
    for pair_index, (ledger, settlement) in enumerate(source_pairs):
        bank = (
            bank_entries[assignments[pair_index]]
            if pair_index in assignments
            else None
        )
        try:
            window_days = settlement_window_days(ledger.payment_method)
        except KeyError:
            window_days = None
        evidence_rows.append(
            ExceptionEvidence(
                record_ids=[
                    ledger.order_id,
                    settlement.settlement_id,
                    *([bank.bank_txn_id] if bank is not None else []),
                ],
                ledger_ref_id=ledger.ref_id,
                settlement_ref_id=settlement.ref_id,
                currency=ledger.currency,
                days_since_expected=(
                    (bank.value_date - settlement.settlement_date).days
                    if bank is not None
                    else None
                ),
                method_window_days=window_days,
                amount_difference=(
                    round(abs(bank.amount - settlement.net_amount), 2)
                    if bank is not None
                    else None
                ),
                refund_candidate=ledger.amount < 0
                or (ledger.ref_id or "").endswith("-refund"),
            )
        )

    paired_ledger_ids = {
        *[ledger.order_id for ledger, _ in source_pairs],
        *[ledger_entries[index].order_id for index in refund_ledger_indices],
        *[ledger_entries[index].order_id for index in split_ledger_indices],
    }
    evidence_rows.extend(
        ExceptionEvidence(
            record_ids=[ledger.order_id],
            ledger_ref_id=ledger.ref_id,
            settlement_ref_id=None,
            currency=ledger.currency,
            refund_candidate=ledger.amount < 0
            or (ledger.ref_id or "").endswith("-refund"),
        )
        for ledger in ledger_entries
        if ledger.order_id not in paired_ledger_ids
    )
    evidence_rows.extend(
        ExceptionEvidence(
            record_ids=[settlement_entries[index].settlement_id],
            ledger_ref_id=None,
            settlement_ref_id=settlement_entries[index].ref_id,
            currency="INR",
        )
        for index in sorted(unused_settlements)
    )
    evidence_rows.extend(
        ExceptionEvidence(
            record_ids=[bank_entries[index].bank_txn_id],
            ledger_ref_id=None,
            settlement_ref_id=None,
            currency="INR",
            has_ledger_or_settlement=False,
        )
        for index in sorted(unused_banks)
    )
    return evidence_rows


def _category(evidence: ExceptionEvidence) -> tuple[str, str]:
    if evidence.refund_candidate:
        return "REFUND_UNLINKED", "The refund could not be linked to an original sale."
    if evidence.has_ledger_or_settlement and (
        _is_blank(evidence.ledger_ref_id)
        or _is_blank(evidence.settlement_ref_id)
    ):
        return "MISSING_REF_ID", "The ledger or settlement identity is missing."
    if evidence.currency != "INR":
        return "CURRENCY_MISMATCH", "The transaction currency is outside policy."
    if (
        evidence.days_since_expected is not None
        and evidence.method_window_days is not None
        and evidence.amount_difference is not None
        and evidence.amount_difference < 0.5
        and evidence.days_since_expected > evidence.method_window_days
    ):
        return "TIMING_LAG_EXCEEDED", "The bank entry exceeded its settlement window."
    if evidence.split_candidate:
        return (
            "SPLIT_SETTLEMENT_UNRESOLVED",
            "The possible split settlement remained ambiguous.",
        )
    return (
        "AMOUNT_MISMATCH_UNEXPLAINED",
        "The identified transaction has an unexplained amount difference.",
    )


def categorize_exceptions(
    evidence_rows: list[ExceptionEvidence],
) -> list[ReconciliationResult]:
    seen_ids: set[str] = set()
    results = []
    for evidence in evidence_rows:
        if seen_ids.intersection(evidence.record_ids):
            raise ValueError("record appears in more than one exception context")
        seen_ids.update(evidence.record_ids)
        category, reasoning = _category(evidence)
        results.append(
            ReconciliationResult(
                record_ids=evidence.record_ids,
                matched=False,
                confidence=1.0,
                method="exception_rules",
                exception_reason=category,
                reasoning=reasoning,
            )
        )
    return results
