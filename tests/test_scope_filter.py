from datetime import date

import pytest

from data.generator import generate_dataset
from data.schemas import BankEntry
from matching.scope_filter import filter_non_transactions


def bank_entry(narration: str, bank_txn_id: str = "bank-1") -> BankEntry:
    return BankEntry(
        bank_txn_id=bank_txn_id,
        ref_id=None,
        amount=100.0,
        value_date=date(2026, 1, 1),
        narration=narration,
    )


@pytest.mark.parametrize(
    ("narration", "phrase"),
    [
        ("LOAN DISBURSEMENT", "LOAN DISBURSEMENT"),
        ("gst   refund", "GST REFUND"),
        ("prefix INTERNAL-TRANSFER: suffix", "INTERNAL TRANSFER"),
    ],
)
def test_scope_filter_excludes_normalized_keyword_phrases(
    narration: str, phrase: str
) -> None:
    entry = bank_entry(narration)

    result = filter_non_transactions([entry])

    assert result.remaining_entries == []
    assert result.exclusions[0].source_index == 0
    assert result.exclusions[0].entry == entry
    assert result.exclusions[0].matched_phrase == phrase


def test_scope_filter_uses_word_boundaries_and_fixed_priority() -> None:
    entries = [
        bank_entry("INTERNAL TRANSFERRED FUNDS", "bank-boundary-1"),
        bank_entry("GST REFUNDABLE ADJUSTMENT", "bank-boundary-2"),
        bank_entry("LOAN DISBURSEMENT then INTERNAL TRANSFER", "bank-priority"),
    ]

    result = filter_non_transactions(entries)

    assert [entry.bank_txn_id for entry in result.remaining_entries] == [
        "bank-boundary-1",
        "bank-boundary-2",
    ]
    assert [exclusion.matched_phrase for exclusion in result.exclusions] == [
        "LOAN DISBURSEMENT"
    ]
    assert result.exclusions[0].source_index == 2


def test_scope_filter_keeps_transaction_and_ambiguous_narrations_in_order() -> None:
    entries = [
        bank_entry("RAZORPAY SETTLEMENT", "bank-settlement"),
        bank_entry("REFUND ref-case-1", "bank-refund"),
        bank_entry("PAYMENT RECEIVED", "bank-ambiguous"),
    ]

    result = filter_non_transactions(entries)

    assert result.remaining_entries == entries
    assert result.exclusions == []


def test_scope_filter_handles_empty_and_empty_narration() -> None:
    empty_narration = bank_entry("", "bank-empty")

    assert filter_non_transactions([]).remaining_entries == []
    assert filter_non_transactions([empty_narration]).remaining_entries == [
        empty_narration
    ]


def test_scope_filter_passes_design_gate_without_false_exclusions() -> None:
    design = generate_dataset(seed=42).design
    expected = {
        bank_id
        for truth in design.ground_truth
        if truth.case_type == "NON_TRANSACTION_BANK_LINE"
        for bank_id in truth.bank_txn_ids
    }
    bank_by_id = {entry.bank_txn_id: entry for entry in design.bank}

    result = filter_non_transactions(design.bank)
    actual = {exclusion.entry.bank_txn_id for exclusion in result.exclusions}

    assert actual == expected
    assert {entry.bank_txn_id for entry in result.remaining_entries} == (
        set(bank_by_id) - expected
    )
