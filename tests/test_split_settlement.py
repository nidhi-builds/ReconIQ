from datetime import date

import pytest

from data.generator import generate_dataset
from data.schemas import BankEntry, LedgerEntry, SettlementEntry
from matching.dedup import deduplicate_ledger
from matching.exact_match import match_exact_references
from matching.fee_adjusted_match import match_fee_adjusted
from matching.scope_filter import filter_non_transactions
from matching.split_settlement import match_split_settlements


def ledger(
    index: int,
    amount: float = 100.0,
    payment_method: str = "upi",
    currency: str = "INR",
    ref_id: str | None = None,
) -> LedgerEntry:
    return LedgerEntry(
        order_id=f"order-{index}",
        ref_id=ref_id or f"ref-{index}",
        amount=amount,
        currency=currency,
        payment_method=payment_method,
        order_date=date(2026, 1, 1),
        customer_id=f"customer-{index}",
    )


def settlement(
    index: int,
    net_amount: float = 100.0,
    payment_method: str = "upi",
    day: int = 2,
    ref_id: str | None = None,
) -> SettlementEntry:
    return SettlementEntry(
        settlement_id=f"settlement-{index}",
        ref_id=ref_id or f"ref-{index}",
        gross_amount=net_amount + 1.18,
        fee=1.0,
        gst_on_fee=0.18,
        net_amount=net_amount,
        payment_method=payment_method,
        settlement_date=date(2026, 1, day),
        tds_deducted=1.0,
        tds_section="194H",
        challan_number=f"challan-{index}",
    )


def bank(
    amount: float,
    bank_txn_id: str = "bank-1",
    day: int = 3,
    ref_id: str | None = None,
) -> BankEntry:
    return BankEntry(
        bank_txn_id=bank_txn_id,
        ref_id=ref_id,
        amount=amount,
        value_date=date(2026, 1, day),
        narration="RAZORPAY SETTLEMENT",
    )


def parts(net_amounts: list[float]) -> tuple[list[LedgerEntry], list[SettlementEntry]]:
    return (
        [ledger(index, amount=value + 1.18) for index, value in enumerate(net_amounts, 1)],
        [settlement(index, net_amount=value) for index, value in enumerate(net_amounts, 1)],
    )


@pytest.mark.parametrize("part_count", [2, 3, 4, 5])
def test_split_settlement_matches_every_supported_part_count(part_count: int) -> None:
    ledgers, settlements = parts([100.0 + index for index in range(part_count)])
    bank_entry = bank(sum(row.net_amount for row in settlements))

    result = match_split_settlements(ledgers, settlements, [bank_entry])

    assert len(result.matches) == 1
    assert result.matches[0].record_ids == [
        *[row.order_id for row in ledgers],
        *[row.settlement_id for row in settlements],
        bank_entry.bank_txn_id,
    ]
    assert result.matches[0].matched is True
    assert result.matches[0].confidence == 0.85
    assert result.matches[0].method == "split_settlement"
    assert result.remaining_ledger_entries == []
    assert result.remaining_settlement_entries == []
    assert result.remaining_bank_entries == []


@pytest.mark.parametrize(
    ("bank_amount", "matched"),
    [(199.51, True), (200.49, True), (199.50, False), (200.50, False)],
)
def test_split_settlement_uses_strict_amount_tolerance(
    bank_amount: float, matched: bool
) -> None:
    ledgers, settlements = parts([100.0, 100.0])

    result = match_split_settlements(ledgers, settlements, [bank(bank_amount)])

    assert bool(result.matches) is matched


def test_split_settlement_requires_same_method_and_settlement_date() -> None:
    ledgers, settlements = parts([100.0, 100.0])
    settlements[1] = settlement(2, payment_method="card")
    method_result = match_split_settlements(ledgers, settlements, [bank(200.0)])
    settlements[1] = settlement(2, day=3)
    date_result = match_split_settlements(ledgers, settlements, [bank(200.0)])

    assert method_result.matches == []
    assert date_result.matches == []


@pytest.mark.parametrize(
    "bank_entry",
    [
        bank(200.0, ref_id="conflicting-ref"),
        bank(200.0, day=1),
        bank(200.0, day=5),
    ],
)
def test_split_settlement_rejects_ineligible_bank_rows(bank_entry: BankEntry) -> None:
    ledgers, settlements = parts([100.0, 100.0])

    result = match_split_settlements(ledgers, settlements, [bank_entry])

    assert result.matches == []
    assert result.remaining_bank_entries == [bank_entry]


def test_split_settlement_accepts_inclusive_method_window_edge() -> None:
    ledgers, settlements = parts([100.0, 100.0])

    result = match_split_settlements(ledgers, settlements, [bank(200.0, day=4)])

    assert len(result.matches) == 1


@pytest.mark.parametrize("ref_id", [None, "", "   "])
def test_split_settlement_treats_missing_bank_references_as_blank(
    ref_id: str | None,
) -> None:
    ledgers, settlements = parts([100.0, 100.0])

    result = match_split_settlements(
        ledgers, settlements, [bank(200.0, ref_id=ref_id)]
    )

    assert len(result.matches) == 1


def test_split_settlement_trusts_stored_settlement_net_amount() -> None:
    ledgers, settlements = parts([100.0, 100.0])
    settlements[0] = settlements[0].model_copy(
        update={"gross_amount": 9_999.0, "fee": 1.0, "gst_on_fee": 0.18}
    )

    result = match_split_settlements(ledgers, settlements, [bank(200.0)])

    assert len(result.matches) == 1


@pytest.mark.parametrize(
    "invalid_ledger",
    [ledger(1, currency="USD"), ledger(1, amount=-100.0)],
)
def test_split_settlement_rejects_non_inr_or_nonpositive_parts(
    invalid_ledger: LedgerEntry,
) -> None:
    ledgers, settlements = parts([100.0, 100.0])
    ledgers[0] = invalid_ledger

    result = match_split_settlements(ledgers, settlements, [bank(200.0)])

    assert result.matches == []


def test_split_settlement_requires_unique_nonblank_pair_references() -> None:
    ledgers, settlements = parts([100.0, 100.0])
    ledgers.append(ledger(3, ref_id="ref-1"))

    result = match_split_settlements(ledgers, settlements, [bank(200.0)])

    assert result.matches == []


def test_split_settlement_leaves_multiple_combinations_for_one_bank_unresolved() -> None:
    ledgers, settlements = parts([100.0, 200.0, 120.0, 180.0])

    result = match_split_settlements(ledgers, settlements, [bank(300.0)])

    assert result.matches == []
    assert result.remaining_ledger_entries == ledgers
    assert result.remaining_settlement_entries == settlements


def test_split_settlement_searches_all_sizes_before_resolving() -> None:
    ledgers, settlements = parts([100.0, 200.0, 50.0, 50.0, 200.0])

    result = match_split_settlements(ledgers, settlements, [bank(300.0)])

    assert result.matches == []


def test_split_settlement_leaves_one_combination_for_two_banks_unresolved() -> None:
    ledgers, settlements = parts([100.0, 200.0])
    banks = [bank(300.0, "bank-1"), bank(300.0, "bank-2")]

    result = match_split_settlements(ledgers, settlements, banks)

    assert result.matches == []
    assert result.remaining_bank_entries == banks


def test_split_settlement_leaves_cross_bank_partial_overlap_unresolved() -> None:
    ledgers, settlements = parts([100.0, 200.0, 250.0])
    banks = [bank(300.0, "bank-1"), bank(350.0, "bank-2")]

    result = match_split_settlements(ledgers, settlements, banks)

    assert result.matches == []
    assert result.remaining_ledger_entries == ledgers
    assert result.remaining_settlement_entries == settlements
    assert result.remaining_bank_entries == banks


def test_split_settlement_preserves_source_order() -> None:
    ledgers, settlements = parts([100.0, 200.0])
    unmatched_ledger = ledger(3)
    unmatched_settlement = settlement(4)
    ledgers = [ledgers[1], unmatched_ledger, ledgers[0]]
    settlements = [settlements[0], unmatched_settlement, settlements[1]]
    unmatched_bank = bank(900.0, "bank-unmatched")

    result = match_split_settlements(
        ledgers, settlements, [unmatched_bank, bank(300.0)]
    )

    assert result.matches[0].record_ids == [
        "order-2",
        "order-1",
        "settlement-1",
        "settlement-2",
        "bank-1",
    ]
    assert result.remaining_ledger_entries == [unmatched_ledger]
    assert result.remaining_settlement_entries == [unmatched_settlement]
    assert result.remaining_bank_entries == [unmatched_bank]


def test_split_settlement_handles_empty_inputs() -> None:
    result = match_split_settlements([], [], [])

    assert result.matches == []
    assert result.remaining_ledger_entries == []
    assert result.remaining_settlement_entries == []
    assert result.remaining_bank_entries == []


def test_split_settlement_passes_design_gate() -> None:
    design = generate_dataset(seed=42).design
    ledgers = deduplicate_ledger(design.ledger).retained_entries
    banks = filter_non_transactions(design.bank).remaining_entries
    exact = match_exact_references(ledgers, design.settlements, banks)
    fee_adjusted = match_fee_adjusted(
        exact.remaining_ledger_entries,
        exact.remaining_settlement_entries,
        exact.remaining_bank_entries,
    )

    result = match_split_settlements(
        fee_adjusted.remaining_ledger_entries,
        fee_adjusted.remaining_settlement_entries,
        fee_adjusted.remaining_bank_entries,
    )
    actual = {tuple(match.record_ids) for match in result.matches}
    expected = {
        tuple([*truth.ledger_ids, *truth.settlement_ids, *truth.bank_txn_ids])
        for truth in design.ground_truth
        if truth.case_type == "SPLIT_SETTLEMENT"
    }
    all_truth_groups = {
        tuple([*truth.ledger_ids, *truth.settlement_ids, *truth.bank_txn_ids])
        for truth in design.ground_truth
        if truth.true_match_group is not None
    }
    hard_negative_bank_ids = {
        bank_id
        for truth in design.ground_truth
        if truth.case_type == "HARD_NEGATIVE"
        for bank_id in truth.bank_txn_ids
    }

    assert len(actual & all_truth_groups) / len(actual) >= 0.90
    # Deliberately stronger than the plan's report-only recall requirement.
    assert len(actual & expected) / len(expected) >= 0.90
    assert not hard_negative_bank_ids.intersection(
        match.record_ids[-1] for match in result.matches
    )
