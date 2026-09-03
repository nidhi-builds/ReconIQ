from datetime import date

import pytest

from data.generator import generate_dataset
from data.schemas import BankEntry, LedgerEntry, SettlementEntry
from matching.dedup import deduplicate_ledger
from matching.exact_match import match_exact_references
from matching.scope_filter import filter_non_transactions


def ledger(ref_id: str | None, order_id: str = "order-1") -> LedgerEntry:
    return LedgerEntry(
        order_id=order_id,
        ref_id=ref_id,
        amount=100.0,
        currency="INR",
        payment_method="upi",
        order_date=date(2026, 1, 1),
        customer_id="customer-1",
    )


def settlement(
    ref_id: str | None, settlement_id: str = "settlement-1"
) -> SettlementEntry:
    return SettlementEntry(
        settlement_id=settlement_id,
        ref_id=ref_id,
        gross_amount=100.0,
        fee=2.0,
        gst_on_fee=0.36,
        net_amount=97.64,
        payment_method="upi",
        settlement_date=date(2026, 1, 2),
        tds_deducted=1.0,
        tds_section="194H",
        challan_number="challan-1",
    )


def bank(ref_id: str | None, bank_txn_id: str = "bank-1") -> BankEntry:
    return BankEntry(
        bank_txn_id=bank_txn_id,
        ref_id=ref_id,
        amount=97.64,
        value_date=date(2026, 1, 2),
        narration="RAZORPAY SETTLEMENT",
    )


def test_exact_match_confirms_unique_three_source_reference() -> None:
    result = match_exact_references(
        [ledger("ref-1")], [settlement("ref-1")], [bank("ref-1")]
    )

    assert len(result.matches) == 1
    assert result.matches[0].record_ids == ["order-1", "settlement-1", "bank-1"]
    assert result.matches[0].matched is True
    assert result.matches[0].confidence == 1.0
    assert result.matches[0].method == "exact_ref"
    assert result.matches[0].exception_reason is None
    assert result.matches[0].reasoning is None
    assert result.remaining_ledger_entries == []
    assert result.remaining_settlement_entries == []
    assert result.remaining_bank_entries == []


@pytest.mark.parametrize("missing_source", ["ledger", "settlement", "bank"])
def test_exact_match_leaves_missing_reference_untouched(missing_source: str) -> None:
    ledgers = [ledger(None if missing_source == "ledger" else "ref-1")]
    settlements = [
        settlement(None if missing_source == "settlement" else "ref-1")
    ]
    banks = [bank(None if missing_source == "bank" else "ref-1")]

    result = match_exact_references(ledgers, settlements, banks)

    assert result.matches == []
    assert result.remaining_ledger_entries == ledgers
    assert result.remaining_settlement_entries == settlements
    assert result.remaining_bank_entries == banks


@pytest.mark.parametrize("blank_ref", ["", "   "])
def test_exact_match_treats_blank_references_as_missing(blank_ref: str) -> None:
    result = match_exact_references(
        [ledger(blank_ref)], [settlement(blank_ref)], [bank(blank_ref)]
    )

    assert result.matches == []


def test_exact_match_is_case_sensitive_and_does_not_trim() -> None:
    ledgers = [ledger("ref-1")]
    settlements = [settlement("REF-1")]
    banks = [bank(" ref-1 ")]

    result = match_exact_references(ledgers, settlements, banks)

    assert result.matches == []
    assert result.remaining_ledger_entries == ledgers
    assert result.remaining_settlement_entries == settlements
    assert result.remaining_bank_entries == banks


@pytest.mark.parametrize("absent_source", ["ledger", "settlement", "bank"])
def test_exact_match_does_not_consume_two_source_references(absent_source: str) -> None:
    ledgers = [] if absent_source == "ledger" else [ledger("ref-1")]
    settlements = (
        [] if absent_source == "settlement" else [settlement("ref-1")]
    )
    banks = [] if absent_source == "bank" else [bank("ref-1")]

    result = match_exact_references(ledgers, settlements, banks)

    assert result.matches == []
    assert result.remaining_ledger_entries == ledgers
    assert result.remaining_settlement_entries == settlements
    assert result.remaining_bank_entries == banks


@pytest.mark.parametrize("repeated_source", ["ledger", "settlement", "bank"])
def test_exact_match_does_not_consume_repeated_references(
    repeated_source: str,
) -> None:
    ledgers = [ledger("ref-1")]
    settlements = [settlement("ref-1")]
    banks = [bank("ref-1")]
    if repeated_source == "ledger":
        ledgers.append(ledger("ref-1", "order-2"))
    elif repeated_source == "settlement":
        settlements.append(settlement("ref-1", "settlement-2"))
    else:
        banks.append(bank("ref-1", "bank-2"))

    result = match_exact_references(ledgers, settlements, banks)

    assert result.matches == []
    assert result.remaining_ledger_entries == ledgers
    assert result.remaining_settlement_entries == settlements
    assert result.remaining_bank_entries == banks


def test_exact_match_preserves_match_and_remaining_input_order() -> None:
    ledgers = [
        ledger("ref-2", "order-2"),
        ledger(None, "order-unmatched"),
        ledger("ref-1", "order-1"),
    ]
    settlements = [
        settlement(None, "settlement-unmatched"),
        settlement("ref-1", "settlement-1"),
        settlement("ref-2", "settlement-2"),
    ]
    banks = [
        bank("ref-1", "bank-1"),
        bank(None, "bank-unmatched"),
        bank("ref-2", "bank-2"),
    ]

    result = match_exact_references(ledgers, settlements, banks)

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


def test_exact_match_handles_empty_inputs() -> None:
    result = match_exact_references([], [], [])

    assert result.matches == []
    assert result.remaining_ledger_entries == []
    assert result.remaining_settlement_entries == []
    assert result.remaining_bank_entries == []


def test_exact_match_passes_design_gate_and_preserves_refund_remainders() -> None:
    design = generate_dataset(seed=42).design
    ledgers = deduplicate_ledger(design.ledger).retained_entries
    banks = filter_non_transactions(design.bank).remaining_entries

    result = match_exact_references(ledgers, design.settlements, banks)
    match_ids = {tuple(match.record_ids) for match in result.matches}
    truth_by_ledger_id = {
        ledger_id: truth
        for truth in design.ground_truth
        for ledger_id in truth.ledger_ids
    }
    truth_by_settlement_id = {
        settlement_id: truth
        for truth in design.ground_truth
        for settlement_id in truth.settlement_ids
    }
    truth_by_bank_id = {
        bank_id: truth
        for truth in design.ground_truth
        for bank_id in truth.bank_txn_ids
    }

    for match in result.matches:
        ledger_truth = truth_by_ledger_id[match.record_ids[0]]
        settlement_truth = truth_by_settlement_id[match.record_ids[1]]
        bank_truth = truth_by_bank_id[match.record_ids[2]]
        assert ledger_truth.case_id == settlement_truth.case_id == bank_truth.case_id
        assert ledger_truth.true_match_group is not None

    hard_negative_bank_ids = {
        bank_id
        for truth in design.ground_truth
        if truth.case_type == "HARD_NEGATIVE"
        for bank_id in truth.bank_txn_ids
    }
    assert not hard_negative_bank_ids.intersection(
        match.record_ids[2] for match in result.matches
    )

    remaining_ledger_ids = {
        row.order_id for row in result.remaining_ledger_entries
    }
    remaining_bank_ids = {row.bank_txn_id for row in result.remaining_bank_entries}
    for truth in design.ground_truth:
        if truth.case_type != "REFUND_REVERSAL":
            continue
        assert (
            truth.ledger_ids[0],
            truth.settlement_ids[0],
            truth.bank_txn_ids[0],
        ) in match_ids
        assert truth.ledger_ids[1] in remaining_ledger_ids
        assert truth.bank_txn_ids[1] in remaining_bank_ids
