from datetime import date

import pytest

from data.generator import generate_dataset
from data.schemas import BankEntry, LedgerEntry, SettlementEntry
from matching.dedup import deduplicate_ledger
from matching.exact_match import match_exact_references
from matching.fee_adjusted_match import match_fee_adjusted
from matching.scope_filter import filter_non_transactions


def ledger(
    ref_id: str | None,
    order_id: str = "order-1",
    amount: float = 1_000.0,
    payment_method: str = "upi",
    currency: str = "INR",
) -> LedgerEntry:
    return LedgerEntry(
        order_id=order_id,
        ref_id=ref_id,
        amount=amount,
        currency=currency,
        payment_method=payment_method,
        order_date=date(2026, 1, 1),
        customer_id="customer-1",
    )


def settlement(
    ref_id: str | None,
    settlement_id: str = "settlement-1",
    gross_amount: float = 1_000.0,
    fee: float = 10.0,
    gst_on_fee: float = 1.8,
    net_amount: float = 988.2,
    payment_method: str = "upi",
    day: int = 2,
) -> SettlementEntry:
    return SettlementEntry(
        settlement_id=settlement_id,
        ref_id=ref_id,
        gross_amount=gross_amount,
        fee=fee,
        gst_on_fee=gst_on_fee,
        net_amount=net_amount,
        payment_method=payment_method,
        settlement_date=date(2026, 1, day),
        tds_deducted=10.0,
        tds_section="194H",
        challan_number="challan-1",
    )


def bank(
    bank_txn_id: str = "bank-1",
    amount: float = 988.2,
    ref_id: str | None = None,
    day: int = 3,
) -> BankEntry:
    return BankEntry(
        bank_txn_id=bank_txn_id,
        ref_id=ref_id,
        amount=amount,
        value_date=date(2026, 1, day),
        narration="RAZORPAY SETTLEMENT",
    )


def test_fee_adjusted_match_resolves_valid_amount_and_window() -> None:
    result = match_fee_adjusted(
        [ledger("ref-1")], [settlement("ref-1")], [bank()]
    )

    assert len(result.matches) == 1
    assert result.matches[0].record_ids == ["order-1", "settlement-1", "bank-1"]
    assert result.matches[0].matched is True
    assert result.matches[0].confidence == 0.9
    assert result.matches[0].method == "fee_adjusted_window"
    assert result.remaining_ledger_entries == []
    assert result.remaining_settlement_entries == []
    assert result.remaining_bank_entries == []


@pytest.mark.parametrize("bank_amount", [987.71, 988.69])
def test_fee_adjusted_match_accepts_amount_just_inside_tolerance(
    bank_amount: float,
) -> None:
    result = match_fee_adjusted(
        [ledger("ref-1")],
        [settlement("ref-1")],
        [bank(amount=bank_amount)],
    )

    assert len(result.matches) == 1


@pytest.mark.parametrize("bank_amount", [987.7, 988.7, 987.69, 988.71])
def test_fee_adjusted_match_rejects_amount_at_or_outside_tolerance(
    bank_amount: float,
) -> None:
    result = match_fee_adjusted(
        [ledger("ref-1")],
        [settlement("ref-1")],
        [bank(amount=bank_amount)],
    )

    assert result.matches == []


def test_fee_adjusted_match_uses_inclusive_method_window() -> None:
    at_edge = match_fee_adjusted(
        [ledger("ref-1")], [settlement("ref-1")], [bank(day=4)]
    )
    beyond_edge = match_fee_adjusted(
        [ledger("ref-1")], [settlement("ref-1")], [bank(day=5)]
    )
    before_settlement = match_fee_adjusted(
        [ledger("ref-1")], [settlement("ref-1")], [bank(day=1)]
    )

    assert len(at_edge.matches) == 1
    assert beyond_edge.matches == []
    assert before_settlement.matches == []


@pytest.mark.parametrize(
    ("ledger_entry", "settlement_entry", "bank_entry"),
    [
        (ledger("ref-1", currency="USD"), settlement("ref-1"), bank()),
        (
            ledger("ref-1", payment_method="upi"),
            settlement(
                "ref-1",
                gross_amount=1_012.09,
                fee=20.24,
                gst_on_fee=3.64,
                net_amount=988.21,
                payment_method="card",
            ),
            bank(amount=988.21),
        ),
        (ledger("ref-1"), settlement("ref-1", fee=99.0), bank()),
        (ledger("ref-1"), settlement("ref-1"), bank(ref_id="other-ref")),
    ],
)
def test_fee_adjusted_match_rejects_ineligible_pairs_and_conflicting_bank_refs(
    ledger_entry: LedgerEntry,
    settlement_entry: SettlementEntry,
    bank_entry: BankEntry,
) -> None:
    result = match_fee_adjusted(
        [ledger_entry], [settlement_entry], [bank_entry]
    )

    assert result.matches == []
    assert result.remaining_ledger_entries == [ledger_entry]
    assert result.remaining_settlement_entries == [settlement_entry]
    assert result.remaining_bank_entries == [bank_entry]


def test_fee_adjusted_match_leaves_two_pairs_competing_for_one_bank() -> None:
    ledgers = [
        ledger("ref-upi", "order-upi"),
        ledger(
            "ref-card",
            "order-card",
            amount=1_012.09,
            payment_method="card",
        ),
    ]
    settlements = [
        settlement("ref-upi", "settlement-upi"),
        settlement(
            "ref-card",
            "settlement-card",
            gross_amount=1_012.09,
            fee=20.24,
            gst_on_fee=3.64,
            net_amount=988.21,
            payment_method="card",
        ),
    ]
    banks = [bank(amount=988.2)]

    result = match_fee_adjusted(ledgers, settlements, banks)

    assert result.matches == []
    assert result.remaining_ledger_entries == ledgers
    assert result.remaining_settlement_entries == settlements
    assert result.remaining_bank_entries == banks


def test_fee_adjusted_match_leaves_one_pair_competing_for_two_banks() -> None:
    ledgers = [ledger("ref-1")]
    settlements = [settlement("ref-1")]
    banks = [bank("bank-1", 988.0), bank("bank-2", 988.4)]

    result = match_fee_adjusted(ledgers, settlements, banks)

    assert result.matches == []
    assert result.remaining_ledger_entries == ledgers
    assert result.remaining_settlement_entries == settlements
    assert result.remaining_bank_entries == banks


def test_fee_adjusted_match_does_not_treat_split_or_refund_as_single_match() -> None:
    ledgers = [
        ledger("ref-part-1", "order-part-1", amount=500.0),
        ledger("ref-part-2", "order-part-2", amount=500.0),
        ledger("ref-refund", "order-refund", amount=-1_000.0),
    ]
    settlements = [
        settlement(
            "ref-part-1",
            "settlement-part-1",
            gross_amount=500.0,
            fee=5.0,
            gst_on_fee=0.9,
            net_amount=494.1,
        ),
        settlement(
            "ref-part-2",
            "settlement-part-2",
            gross_amount=500.0,
            fee=5.0,
            gst_on_fee=0.9,
            net_amount=494.1,
        ),
    ]
    banks = [bank(amount=988.2)]

    result = match_fee_adjusted(ledgers, settlements, banks)

    assert result.matches == []
    assert result.remaining_ledger_entries == ledgers
    assert result.remaining_settlement_entries == settlements
    assert result.remaining_bank_entries == banks


def test_fee_adjusted_match_preserves_match_and_remaining_order() -> None:
    ledgers = [
        ledger("ref-2", "order-2", amount=2_000.0),
        ledger(None, "order-unmatched"),
        ledger("ref-1", "order-1"),
    ]
    settlements = [
        settlement(None, "settlement-unmatched"),
        settlement("ref-1", "settlement-1"),
        settlement(
            "ref-2",
            "settlement-2",
            gross_amount=2_000.0,
            fee=20.0,
            gst_on_fee=3.6,
            net_amount=1_976.4,
        ),
    ]
    banks = [
        bank("bank-1", 988.2),
        bank("bank-unmatched", 500.0),
        bank("bank-2", 1_976.4),
    ]

    result = match_fee_adjusted(ledgers, settlements, banks)

    assert [match.record_ids[0] for match in result.matches] == [
        "order-2",
        "order-1",
    ]
    assert [row.order_id for row in result.remaining_ledger_entries] == [
        "order-unmatched"
    ]
    assert [row.settlement_id for row in result.remaining_settlement_entries] == [
        "settlement-unmatched"
    ]
    assert [row.bank_txn_id for row in result.remaining_bank_entries] == [
        "bank-unmatched"
    ]


def test_fee_adjusted_match_handles_empty_inputs() -> None:
    result = match_fee_adjusted([], [], [])

    assert result.matches == []
    assert result.remaining_ledger_entries == []
    assert result.remaining_settlement_entries == []
    assert result.remaining_bank_entries == []


def test_fee_adjusted_match_passes_design_gate() -> None:
    design = generate_dataset(seed=42).design
    ledgers = deduplicate_ledger(design.ledger).retained_entries
    banks = filter_non_transactions(design.bank).remaining_entries
    exact = match_exact_references(ledgers, design.settlements, banks)

    result = match_fee_adjusted(
        exact.remaining_ledger_entries,
        exact.remaining_settlement_entries,
        exact.remaining_bank_entries,
    )
    actual = {tuple(match.record_ids) for match in result.matches}
    expected = {
        (truth.ledger_ids[0], truth.settlement_ids[0], truth.bank_txn_ids[0])
        for truth in design.ground_truth
        if truth.case_type in {
            "FEE_ADJUSTED_MATCH",
            "TIMING_LAG_WITHIN_WINDOW",
        }
    }
    truth_by_ledger = {
        ledger_id: truth
        for truth in design.ground_truth
        for ledger_id in truth.ledger_ids
    }
    truth_by_settlement = {
        settlement_id: truth
        for truth in design.ground_truth
        for settlement_id in truth.settlement_ids
    }
    truth_by_bank = {
        bank_id: truth
        for truth in design.ground_truth
        for bank_id in truth.bank_txn_ids
    }
    valid = sum(
        truth_by_ledger[match.record_ids[0]].case_id
        == truth_by_settlement[match.record_ids[1]].case_id
        == truth_by_bank[match.record_ids[2]].case_id
        and truth_by_ledger[match.record_ids[0]].true_match_group is not None
        for match in result.matches
    )
    hard_negative_bank_ids = {
        bank_id
        for truth in design.ground_truth
        if truth.case_type == "HARD_NEGATIVE"
        for bank_id in truth.bank_txn_ids
    }

    assert valid / len(result.matches) >= 0.95
    assert len(actual & expected) / len(expected) >= 0.90
    # Hard negatives are structurally excluded by their nonblank bank ref_id;
    # this is not a stress test of the amount tolerance.
    assert not hard_negative_bank_ids.intersection(
        match.record_ids[2] for match in result.matches
    )
