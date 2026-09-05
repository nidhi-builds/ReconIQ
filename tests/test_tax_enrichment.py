from collections import Counter
from copy import deepcopy

import pytest

from data.generator import generate_dataset
from data.schemas import ReconciliationResult, SettlementEntry, Tax26ASEntry
from matching.dedup import deduplicate_ledger
from matching.exact_match import match_exact_references
from matching.fee_adjusted_match import match_fee_adjusted
from matching.refund_match import ConfirmedReference, match_refund_reversals
from matching.scope_filter import filter_non_transactions
from matching.split_settlement import match_split_settlements
from tax.gst_tds_enrichment import enrich_tax_lines


def settlement(
    label: str = "1",
    *,
    ref_id: str | None = "ref-1",
    payment_method: str = "upi",
    tds_deducted: float = 10,
    tds_section: str = "194H",
    challan_number: str | None = "CH-1",
) -> SettlementEntry:
    return SettlementEntry(
        settlement_id=f"settlement-{label}",
        ref_id=ref_id,
        gross_amount=1_000,
        fee=16.95,
        gst_on_fee=3.05,
        net_amount=980,
        payment_method=payment_method,
        settlement_date="2026-01-02",
        tds_deducted=tds_deducted,
        tds_section=tds_section,
        challan_number=challan_number,
    )


def tax_reference(
    label: str = "1",
    *,
    ref_id: str | None = "ref-1",
    tds_expected: float = 10,
    tds_section_expected: str = "194H",
    challan_number: str | None = "CH-1",
) -> Tax26ASEntry:
    return Tax26ASEntry(
        case_id=f"case-{label}",
        ref_id=ref_id,
        tds_expected=tds_expected,
        tds_section_expected=tds_section_expected,
        challan_number=challan_number,
    )


def match(*record_ids: str, matched: bool = True) -> ReconciliationResult:
    return ReconciliationResult(
        record_ids=list(record_ids),
        matched=matched,
        confidence=1,
        method="exact_ref",
        exception_reason=None if matched else "MISSING_REF_ID",
        reasoning=None,
    )


@pytest.mark.parametrize(
    ("payment_method", "gst_category"),
    [
        ("upi", "PAYMENT_GATEWAY_SERVICE"),
        ("card", "CARD_PROCESSING_SERVICE"),
        ("wallet", "DIGITAL_WALLET_SERVICE"),
        ("netbanking", "BANKING_PAYMENT_SERVICE"),
        ("emi", "EMI_PROCESSING_SERVICE"),
    ],
)
def test_clear_finding_maps_every_supported_payment_method(
    payment_method: str, gst_category: str
) -> None:
    row = settlement(payment_method=payment_method)

    findings = enrich_tax_lines(
        [match("bank-1", row.settlement_id, "order-1")],
        [row],
        [tax_reference()],
    )

    assert len(findings) == 1
    assert findings[0].status == "CLEAR"
    assert findings[0].mismatch_reason is None
    assert findings[0].gst_category == gst_category
    assert findings[0].settlement_id == "settlement-1"


@pytest.mark.parametrize(
    ("settlement_updates", "expected"),
    [
        ({"tds_deducted": 5}, "SHORT_DEDUCTION"),
        ({"challan_number": None}, "MISSING_CHALLAN"),
        ({"tds_section": "194C"}, "WRONG_SECTION"),
        (
            {
                "challan_number": None,
                "tds_section": "194C",
                "tds_deducted": 5,
            },
            "MISSING_CHALLAN",
        ),
    ],
)
def test_tax_mismatch_rules_use_fixed_priority(
    settlement_updates: dict[str, object],
    expected: str,
) -> None:
    row = settlement().model_copy(update=settlement_updates)

    finding = enrich_tax_lines(
        [match("order-1", "settlement-1", "bank-1")], [row], [tax_reference()]
    )[0]

    assert finding.status == "MISMATCH"
    assert finding.mismatch_reason == expected


def test_unmatched_results_are_ignored() -> None:
    findings = enrich_tax_lines(
        [match("order-1", "settlement-1", "bank-1", matched=False)],
        [settlement()],
        [tax_reference()],
    )

    assert findings == []


@pytest.mark.parametrize(
    ("rows", "references"),
    [
        ([settlement(ref_id=None)], [tax_reference()]),
        (
            [settlement(ref_id="   ")],
            [tax_reference(ref_id="   ")],
        ),
        ([settlement()], []),
        (
            [settlement()],
            [tax_reference("a"), tax_reference("b")],
        ),
    ],
)
def test_missing_or_nonunique_tax_linkage_is_unverifiable(
    rows: list[SettlementEntry], references: list[Tax26ASEntry]
) -> None:
    finding = enrich_tax_lines(
        [match("order-1", "settlement-1", "bank-1")], rows, references
    )[0]

    assert finding.status == "UNVERIFIABLE"
    assert finding.mismatch_reason is None
    assert finding.reasoning


def test_duplicate_selected_settlement_reference_is_unverifiable() -> None:
    rows = [settlement("1"), settlement("2")]

    findings = enrich_tax_lines(
        [
            match("order-1", "settlement-1", "bank-1"),
            match("order-2", "settlement-2", "bank-2"),
        ],
        rows,
        [tax_reference()],
    )

    assert [finding.status for finding in findings] == [
        "UNVERIFIABLE",
        "UNVERIFIABLE",
    ]


@pytest.mark.parametrize(
    ("settlement_challan", "reference_challan"),
    [("ch-1", "CH-1"), (" CH-1", "CH-1"), ("CH-1", None)],
)
def test_challan_comparison_is_exact_case_sensitive_and_untrimmed(
    settlement_challan: str,
    reference_challan: str | None,
) -> None:
    finding = enrich_tax_lines(
        [match("order-1", "settlement-1", "bank-1")],
        [settlement(challan_number=settlement_challan)],
        [tax_reference(challan_number=reference_challan)],
    )[0]

    assert finding.status == "UNVERIFIABLE"
    assert "challan" in finding.reasoning.lower()


def test_over_deduction_is_detected_but_unmodeled() -> None:
    finding = enrich_tax_lines(
        [match("order-1", "settlement-1", "bank-1")],
        [settlement(tds_deducted=10.01)],
        [tax_reference(tds_expected=10)],
    )[0]

    assert finding.status == "UNVERIFIABLE"
    assert finding.expected_tds == 10
    assert finding.actual_tds == 10.01
    assert "over-deduction" in finding.reasoning.lower()


def test_split_match_uses_membership_and_preserves_record_order() -> None:
    first = settlement("1", ref_id="ref-1")
    second = settlement("2", ref_id="ref-2")
    findings = enrich_tax_lines(
        [
            match(
                "order-1",
                "order-2",
                "settlement-2",
                "settlement-1",
                "bank-1",
            )
        ],
        [first, second],
        [tax_reference("1"), tax_reference("2", ref_id="ref-2")],
    )

    assert [finding.settlement_id for finding in findings] == [
        "settlement-2",
        "settlement-1",
    ]


def test_tax_enrichment_does_not_mutate_inputs() -> None:
    matches = [match("order-1", "settlement-1", "bank-1")]
    rows = [settlement()]
    references = [tax_reference()]
    before = deepcopy((matches, rows, references))

    enrich_tax_lines(matches, rows, references)

    assert (matches, rows, references) == before


def test_design_tax_tags_pass_accuracy_and_category_gates() -> None:
    design = generate_dataset(seed=42).design
    ledgers = deduplicate_ledger(design.ledger).retained_entries
    banks = filter_non_transactions(design.bank).remaining_entries
    exact = match_exact_references(ledgers, design.settlements, banks)
    fee = match_fee_adjusted(
        exact.remaining_ledger_entries,
        exact.remaining_settlement_entries,
        exact.remaining_bank_entries,
    )
    split = match_split_settlements(
        fee.remaining_ledger_entries,
        fee.remaining_settlement_entries,
        fee.remaining_bank_entries,
    )
    ledger_by_id = {row.order_id: row for row in ledgers}
    confirmations = [
        ConfirmedReference(
            ref_id=ledger_by_id[row.record_ids[0]].ref_id or "",
            original_amount=ledger_by_id[row.record_ids[0]].amount,
            match=row,
        )
        for row in exact.matches
        if ledger_by_id[row.record_ids[0]].ref_id
    ]
    refund = match_refund_reversals(
        split.remaining_ledger_entries,
        split.remaining_bank_entries,
        confirmations,
    )
    findings = enrich_tax_lines(
        [*exact.matches, *fee.matches, *split.matches, *refund.matches],
        design.settlements,
        design.tax_26as,
    )

    truth_by_settlement = {
        settlement_id: truth
        for truth in design.ground_truth
        for settlement_id in truth.settlement_ids
    }
    expected_tax_ids = {
        settlement_id
        for truth in design.ground_truth
        if truth.true_tax_mismatch
        for settlement_id in truth.settlement_ids
    }
    finding_by_settlement = {row.settlement_id: row for row in findings}
    assert expected_tax_ids <= finding_by_settlement.keys()

    scored = [
        (row.mismatch_reason, truth_by_settlement[row.settlement_id].true_tax_mismatch)
        for row in findings
    ]
    accuracy = sum(actual == expected for actual, expected in scored) / len(scored)
    category_metrics = {}
    for category in ["SHORT_DEDUCTION", "MISSING_CHALLAN", "WRONG_SECTION"]:
        true_positives = sum(
            actual == expected == category for actual, expected in scored
        )
        predictions = sum(actual == category for actual, _ in scored)
        expected_count = sum(expected == category for _, expected in scored)
        category_metrics[category] = {
            "precision": true_positives / predictions if predictions else 0,
            "recall": true_positives / expected_count if expected_count else 0,
        }

    print(
        {
            "accuracy": accuracy,
            "category_metrics": category_metrics,
            "status_counts": Counter(row.status for row in findings),
        }
    )
    assert accuracy >= 0.9
    assert all(
        values[metric] >= 0.9
        for values in category_metrics.values()
        for metric in ("precision", "recall")
    )
    assert not [row for row in findings if row.status == "UNVERIFIABLE"]
