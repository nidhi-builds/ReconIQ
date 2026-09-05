from collections import Counter
from typing import Literal

from pydantic import BaseModel

from data.schemas import ReconciliationResult, SettlementEntry, Tax26ASEntry


TaxMismatch = Literal[
    "SHORT_DEDUCTION",
    "MISSING_CHALLAN",
    "WRONG_SECTION",
]

_GST_CATEGORIES = {
    "upi": "PAYMENT_GATEWAY_SERVICE",
    "card": "CARD_PROCESSING_SERVICE",
    "wallet": "DIGITAL_WALLET_SERVICE",
    "netbanking": "BANKING_PAYMENT_SERVICE",
    "emi": "EMI_PROCESSING_SERVICE",
}


def _is_blank(value: str | None) -> bool:
    return value is None or not value.strip()


class TaxFinding(BaseModel):
    record_ids: list[str]
    settlement_id: str
    ref_id: str | None
    gst_category: str
    tds_section: str
    expected_tds: float | None
    actual_tds: float
    status: Literal["CLEAR", "MISMATCH", "UNVERIFIABLE"]
    mismatch_reason: TaxMismatch | None
    reasoning: str


def enrich_tax_lines(
    matches: list[ReconciliationResult],
    settlements: list[SettlementEntry],
    tax_26as: list[Tax26ASEntry],
) -> list[TaxFinding]:
    settlement_by_id = {row.settlement_id: row for row in settlements}
    selected = [
        (match, settlement_by_id[record_id])
        for match in matches
        if match.matched
        for record_id in match.record_ids
        if record_id in settlement_by_id
    ]
    settlement_ref_counts = Counter(
        row.ref_id for _, row in selected if not _is_blank(row.ref_id)
    )
    tax_ref_counts = Counter(
        row.ref_id for row in tax_26as if not _is_blank(row.ref_id)
    )
    tax_by_ref = {
        row.ref_id: row
        for row in tax_26as
        if not _is_blank(row.ref_id) and tax_ref_counts[row.ref_id] == 1
    }
    findings = []

    for match, settlement in selected:
        reference = tax_by_ref.get(settlement.ref_id)
        if (
            _is_blank(settlement.ref_id)
            or settlement_ref_counts[settlement.ref_id] != 1
            or reference is None
        ):
            findings.append(
                _unverifiable(match, settlement, "Tax reference linkage is not unique.")
            )
            continue

        actual = round(settlement.tds_deducted, 2)
        expected = round(reference.tds_expected, 2)
        if settlement.challan_number in (None, "") and reference.challan_number not in (
            None,
            "",
        ):
            reason: TaxMismatch | None = "MISSING_CHALLAN"
        elif reference.challan_number in (None, ""):
            findings.append(
                _unverifiable(
                    match,
                    settlement,
                    "The 26AS challan is missing.",
                    expected_tds=expected,
                )
            )
            continue
        elif settlement.challan_number != reference.challan_number:
            findings.append(
                _unverifiable(
                    match,
                    settlement,
                    "Settlement and 26AS challan values conflict.",
                    expected_tds=expected,
                )
            )
            continue
        elif settlement.tds_section != reference.tds_section_expected:
            reason = "WRONG_SECTION"
        elif actual < expected:
            reason = "SHORT_DEDUCTION"
        elif actual > expected:
            findings.append(
                _unverifiable(
                    match,
                    settlement,
                    "Detected over-deduction, which is outside the modeled labels.",
                    expected_tds=expected,
                )
            )
            continue
        else:
            reason = None

        findings.append(
            TaxFinding(
                record_ids=match.record_ids,
                settlement_id=settlement.settlement_id,
                ref_id=settlement.ref_id,
                gst_category=_GST_CATEGORIES[settlement.payment_method],
                tds_section=settlement.tds_section,
                expected_tds=expected,
                actual_tds=actual,
                status="MISMATCH" if reason else "CLEAR",
                mismatch_reason=reason,
                reasoning=(
                    f"Detected {reason}."
                    if reason
                    else "Settlement tax fields agree with the 26AS reference."
                ),
            )
        )
    return findings


def _unverifiable(
    match: ReconciliationResult,
    settlement: SettlementEntry,
    reasoning: str,
    *,
    expected_tds: float | None = None,
) -> TaxFinding:
    return TaxFinding(
        record_ids=match.record_ids,
        settlement_id=settlement.settlement_id,
        ref_id=settlement.ref_id,
        gst_category=_GST_CATEGORIES[settlement.payment_method],
        tds_section=settlement.tds_section,
        expected_tds=expected_tds,
        actual_tds=round(settlement.tds_deducted, 2),
        status="UNVERIFIABLE",
        mismatch_reason=None,
        reasoning=reasoning,
    )
