from datetime import date

import pytest
from pydantic import ValidationError

from data.schemas import BankEntry, LedgerEntry, SettlementEntry, Tax26ASEntry


def test_ledger_entry_parses_an_iso_order_date() -> None:
    entry = LedgerEntry(
        order_id="order-001",
        ref_id="ref-001",
        amount=499.0,
        currency="INR",
        payment_method="upi",
        order_date="2026-08-29",
        customer_id="customer-001",
    )

    assert entry.order_date == date(2026, 8, 29)
    assert entry.ref_id == "ref-001"


def test_ledger_entry_rejects_a_missing_customer_id() -> None:
    with pytest.raises(ValidationError):
        LedgerEntry(
            order_id="order-001",
            ref_id=None,
            amount=499.0,
            currency="INR",
            payment_method="upi",
            order_date="2026-08-29",
        )


def test_settlement_entry_allows_a_missing_reference_id() -> None:
    entry = SettlementEntry(
        settlement_id="settlement-001",
        ref_id=None,
        gross_amount=1_000.0,
        fee=20.0,
        gst_on_fee=3.6,
        net_amount=976.4,
        payment_method="card",
        settlement_date="2026-08-30",
        tds_deducted=10.0,
        tds_section="194H",
        challan_number="CHALLAN-001",
    )

    assert entry.ref_id is None
    assert entry.settlement_date == date(2026, 8, 30)
    assert entry.tds_section == "194H"


def test_tax_26as_entry_parses_expected_tax_evidence() -> None:
    entry = Tax26ASEntry(
        case_id="case-001",
        ref_id="ref-001",
        tds_expected=10.0,
        tds_section_expected="194H",
        challan_number="CHALLAN-001",
    )

    assert entry.tds_expected == 10.0


def test_bank_entry_excludes_the_generator_only_transaction_flag() -> None:
    entry = BankEntry(
        bank_txn_id="bank-001",
        ref_id="ref-001",
        amount=976.4,
        value_date="2026-08-30",
        narration="RAZORPAY SETTLEMENT",
    )

    assert "is_transaction" not in BankEntry.model_fields
    assert "is_transaction" not in entry.model_dump()
