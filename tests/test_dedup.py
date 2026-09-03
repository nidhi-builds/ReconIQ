from datetime import date

import pytest

from data.generator import generate_dataset
from data.schemas import LedgerEntry
from matching.dedup import deduplicate_ledger


def ledger_entry(
    ref_id: str,
    *,
    order_id: str = "order-1",
    amount: float = 100.0,
    day: int = 1,
) -> LedgerEntry:
    return LedgerEntry(
        order_id=order_id,
        ref_id=ref_id,
        amount=amount,
        currency="INR",
        payment_method="upi",
        order_date=date(2026, 1, day),
        customer_id="customer-1",
    )


@pytest.mark.parametrize("day", [1, 2])
def test_dedup_retains_first_entry_within_inclusive_one_day_window(day: int) -> None:
    first = ledger_entry("ref-original", day=1)
    retry = ledger_entry("ref-retry", day=day)

    result = deduplicate_ledger([first, retry])

    assert result.retained_entries == [first]
    assert len(result.duplicates) == 1
    assert result.duplicates[0].retained_index == 0
    assert result.duplicates[0].duplicate_index == 1
    assert result.duplicates[0].retained_entry == first
    assert result.duplicates[0].duplicate_entry == retry


def test_dedup_keeps_entries_outside_the_key_or_window() -> None:
    entries = [
        ledger_entry("ref-original", day=1),
        ledger_entry("ref-late", day=3),
        ledger_entry("ref-other-amount", amount=101.0, day=1),
        ledger_entry("ref-other-order", order_id="order-2", day=1),
    ]

    result = deduplicate_ledger(entries)

    assert result.retained_entries == entries
    assert result.duplicates == []


def test_dedup_chain_compares_against_retained_canonical_entries() -> None:
    first = ledger_entry("ref-day-1", day=1)
    middle = ledger_entry("ref-day-2", day=2)
    last = ledger_entry("ref-day-3", day=3)

    result = deduplicate_ledger([first, middle, last])

    assert result.retained_entries == [first, last]
    assert [(link.retained_index, link.duplicate_index) for link in result.duplicates] == [
        (0, 1)
    ]


def test_dedup_handles_empty_and_single_entry_inputs() -> None:
    single = ledger_entry("ref-single")

    assert deduplicate_ledger([]).retained_entries == []
    assert deduplicate_ledger([]).duplicates == []
    assert deduplicate_ledger([single]).retained_entries == [single]
    assert deduplicate_ledger([single]).duplicates == []


def test_dedup_passes_design_set_gate_without_collapsing_split_parts() -> None:
    design = generate_dataset(seed=42).design
    expected_duplicate_ids = [
        truth.ledger_ids[0]
        for truth in design.ground_truth
        if truth.case_type == "DUPLICATE_LEDGER_ENTRY"
    ]

    result = deduplicate_ledger(design.ledger)
    actual_duplicate_ids = [link.duplicate_entry.order_id for link in result.duplicates]

    assert sorted(actual_duplicate_ids) == sorted(expected_duplicate_ids)
    assert len(result.retained_entries) == len(design.ledger) - len(expected_duplicate_ids)
    assert all(
        link.retained_entry in result.retained_entries for link in result.duplicates
    )
