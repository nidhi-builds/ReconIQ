import csv
import json
import random
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from pydantic import BaseModel

from matching.rate_card import calculate_settlement_amounts
from matching.settlement_windows import settlement_window_days

from data.schemas import (
    BankEntry,
    DatasetPartition,
    GroundTruthEntry,
    LedgerEntry,
    SettlementEntry,
    SyntheticDataset,
    Tax26ASEntry,
)


CASE_TYPES = (
    "CLEAN_EXACT_MATCH",
    "FEE_ADJUSTED_MATCH",
    "TIMING_LAG_WITHIN_WINDOW",
    "TIMING_LAG_EXCEEDED",
    "SPLIT_SETTLEMENT",
    "DUPLICATE_LEDGER_ENTRY",
    "NON_TRANSACTION_BANK_LINE",
    "MISSING_REF_ID",
    "CURRENCY_MISMATCH",
    "REFUND_REVERSAL",
    "AMBIGUOUS_REMAINDER",
    "TAX_SHORT_DEDUCTION",
    "TAX_MISSING_CHALLAN",
    "TAX_WRONG_SECTION",
    "HARD_NEGATIVE",
)

_PAYMENT_METHODS = ("upi", "card", "netbanking", "wallet", "emi")
_NON_TRANSACTION_NARRATIONS = (
    "LOAN DISBURSEMENT",
    "GST REFUND",
    "INTERNAL TRANSFER",
)
_TDS_RULES = {
    "upi": (0.01, "194H"),
    "card": (0.01, "194H"),
    "netbanking": (0.02, "194J"),
    "wallet": (0.01, "194H"),
    "emi": (0.02, "194C"),
}


@dataclass
class _CaseRows:
    ledger: list[LedgerEntry]
    settlements: list[SettlementEntry]
    bank: list[BankEntry]
    tax_26as: list[Tax26ASEntry]
    truth: GroundTruthEntry


def generate_dataset(seed: int = 42) -> SyntheticDataset:
    rng = random.Random(seed)
    design_cases = list(CASE_TYPES) * 5 + list(CASE_TYPES[:10])
    holdout_cases = list(CASE_TYPES) * 2 + list(CASE_TYPES[:5])
    rng.shuffle(design_cases)
    rng.shuffle(holdout_cases)

    return SyntheticDataset(
        design=_generate_partition("design", design_cases, rng),
        holdout=_generate_partition("holdout", holdout_cases, rng),
    )


def write_dataset(dataset: SyntheticDataset, output_dir: str | Path) -> None:
    root = Path(output_dir)
    for split, partition in (("design", dataset.design), ("holdout", dataset.holdout)):
        split_dir = root / split
        split_dir.mkdir(parents=True, exist_ok=True)
        _write_csv(split_dir / "ledger.csv", partition.ledger)
        _write_csv(split_dir / "settlement.csv", partition.settlements)
        _write_csv(split_dir / "bank.csv", partition.bank, exclude={"is_transaction"})
        _write_csv(split_dir / "tax_26as.csv", partition.tax_26as)
        _write_csv(split_dir / "ground_truth.csv", partition.ground_truth)


def _generate_partition(
    split: str, case_types: list[str], rng: random.Random
) -> DatasetPartition:
    cases = [
        _build_case(split, index, case_type, rng)
        for index, case_type in enumerate(case_types, start=1)
    ]
    _link_hard_negatives(cases, rng)

    ledger: list[LedgerEntry] = []
    settlements: list[SettlementEntry] = []
    bank: list[BankEntry] = []
    tax_26as: list[Tax26ASEntry] = []
    ground_truth: list[GroundTruthEntry] = []

    for rows in cases:
        ledger.extend(rows.ledger)
        settlements.extend(rows.settlements)
        bank.extend(rows.bank)
        tax_26as.extend(rows.tax_26as)
        ground_truth.append(rows.truth)

    return DatasetPartition(
        ledger=ledger,
        settlements=settlements,
        bank=bank,
        tax_26as=tax_26as,
        ground_truth=ground_truth,
    )


def _build_case(
    split: str, index: int, case_type: str, rng: random.Random
) -> _CaseRows:
    case_id = f"{split}-{index:03d}"
    ref_id = f"ref-{case_id}"
    order_date = date(2026, 1, 1) + timedelta(days=index)
    payment_method = _PAYMENT_METHODS[index % len(_PAYMENT_METHODS)]
    gross_amount = round(rng.uniform(200, 5_000), 2)
    fee, gst_on_fee, net_amount = calculate_settlement_amounts(
        gross_amount, payment_method
    )
    tds_rate, tds_section = _TDS_RULES[payment_method]
    tds_expected = round(gross_amount * tds_rate, 2)

    ledger = [
        LedgerEntry(
            order_id=f"order-{case_id}",
            ref_id=ref_id,
            amount=gross_amount,
            currency="INR",
            payment_method=payment_method,
            order_date=order_date,
            customer_id=f"customer-{index % 20:02d}",
        )
    ]
    settlements = [
        SettlementEntry(
            settlement_id=f"settlement-{case_id}",
            ref_id=ref_id,
            gross_amount=gross_amount,
            fee=fee,
            gst_on_fee=gst_on_fee,
            net_amount=net_amount,
            payment_method=payment_method,
            settlement_date=order_date + timedelta(days=1),
            tds_deducted=tds_expected,
            tds_section=tds_section,
            challan_number=f"CHALLAN-{case_id}",
        )
    ]
    bank = [
        BankEntry(
            bank_txn_id=f"bank-{case_id}",
            ref_id=ref_id,
            amount=net_amount,
            value_date=order_date + timedelta(days=1),
            narration="RAZORPAY SETTLEMENT",
        )
    ]
    is_transaction = True
    true_match_group: str | None = case_id
    exception_reason: str | None = None
    tax_mismatch: str | None = None
    refund_of: str | None = None

    if case_type in {"FEE_ADJUSTED_MATCH", "TIMING_LAG_WITHIN_WINDOW"}:
        bank[0].ref_id = None
        if case_type == "TIMING_LAG_WITHIN_WINDOW":
            bank[0].value_date += timedelta(
                days=settlement_window_days(payment_method) - 1
            )
    elif case_type == "TIMING_LAG_EXCEEDED":
        bank[0].ref_id = None
        bank[0].value_date += timedelta(
            days=settlement_window_days(payment_method) + 1
        )
        true_match_group = None
        exception_reason = "TIMING_LAG_EXCEEDED"
    elif case_type == "SPLIT_SETTLEMENT":
        ledger, settlements = _split_rows(
            case_id, order_date, payment_method, gross_amount, index
        )
        bank[0].ref_id = None
        bank[0].amount = round(sum(row.net_amount for row in settlements), 2)
    elif case_type == "DUPLICATE_LEDGER_ENTRY":
        ledger.append(
            ledger[0].model_copy(
                update={
                    "ref_id": f"{ref_id}-retry",
                    "order_date": order_date + timedelta(days=1),
                },
                deep=True,
            )
        )
    elif case_type == "NON_TRANSACTION_BANK_LINE":
        ledger = []
        settlements = []
        bank[0].ref_id = None
        bank[0].narration = _NON_TRANSACTION_NARRATIONS[
            index % len(_NON_TRANSACTION_NARRATIONS)
        ]
        is_transaction = False
        true_match_group = None
        exception_reason = "NON_TRANSACTION_EXCLUDED"
    elif case_type == "MISSING_REF_ID":
        ledger[0].ref_id = None
        settlements[0].ref_id = None
        bank[0].ref_id = None
        bank[0].amount += 25
        true_match_group = None
        exception_reason = "MISSING_REF_ID"
    elif case_type == "CURRENCY_MISMATCH":
        ledger[0].currency = "USD"
        bank[0].ref_id = None
        bank[0].amount += 25
        true_match_group = None
        exception_reason = "CURRENCY_MISMATCH"
    elif case_type == "REFUND_REVERSAL":
        refund = ledger[0].model_copy(
            update={
                "order_id": f"refund-{case_id}",
                "ref_id": f"{ref_id}-refund",
                "amount": -gross_amount,
            }
        )
        ledger.append(refund)
        bank.append(
            BankEntry(
                bank_txn_id=f"bank-{case_id}-refund",
                ref_id=f"{ref_id}-refund",
                amount=-gross_amount,
                value_date=order_date + timedelta(days=2),
                narration=f"REFUND {ref_id}",
            )
        )
        refund_of = ref_id
    elif case_type == "AMBIGUOUS_REMAINDER":
        # Outside deterministic amount tolerance, but close enough for LLM review.
        bank[0].ref_id = None
        bank[0].amount = round(net_amount + 1.37, 2)
    elif case_type == "TAX_SHORT_DEDUCTION":
        tax_mismatch = "SHORT_DEDUCTION"
    elif case_type == "TAX_MISSING_CHALLAN":
        tax_mismatch = "MISSING_CHALLAN"
    elif case_type == "TAX_WRONG_SECTION":
        tax_mismatch = "WRONG_SECTION"
    elif case_type == "HARD_NEGATIVE":
        true_match_group = None
        exception_reason = "AMOUNT_MISMATCH_UNEXPLAINED"

    tax_26as = [_tax_26as_entry(case_id, row) for row in settlements]
    if case_type == "TAX_SHORT_DEDUCTION":
        settlements[0].tds_deducted = round(tax_26as[0].tds_expected * 0.5, 2)
    elif case_type == "TAX_MISSING_CHALLAN":
        settlements[0].challan_number = None
    elif case_type == "TAX_WRONG_SECTION":
        settlements[0].tds_section = (
            "194C" if tax_26as[0].tds_section_expected != "194C" else "194J"
        )

    truth = GroundTruthEntry(
        case_id=case_id,
        split=split,
        case_type=case_type,
        ledger_ids=[row.order_id for row in ledger],
        settlement_ids=[row.settlement_id for row in settlements],
        bank_txn_ids=[row.bank_txn_id for row in bank],
        is_transaction=is_transaction if bank else None,
        true_match_group=true_match_group,
        true_exception_reason=exception_reason,
        true_tax_mismatch=tax_mismatch,
        refund_of=refund_of,
    )
    return _CaseRows(
        ledger=ledger,
        settlements=settlements,
        bank=bank,
        tax_26as=tax_26as,
        truth=truth,
    )


def _link_hard_negatives(cases: list[_CaseRows], rng: random.Random) -> None:
    candidates = [
        rows
        for rows in cases
        if rows.truth.case_type != "HARD_NEGATIVE" and rows.settlements
    ]
    for rows in cases:
        if rows.truth.case_type != "HARD_NEGATIVE":
            continue

        target = rng.choice(candidates)
        target_settlement = target.settlements[0]
        hard_negative_bank = rows.bank[0]
        # Inside Stage 3's 0.5 tolerance by design: unrelatedness must prevent the match.
        hard_negative_bank.amount = round(target_settlement.net_amount + 0.25, 2)
        hard_negative_bank.value_date = target_settlement.settlement_date
        hard_negative_bank.ref_id = (
            f"unrelated-{rows.truth.case_id}-{target.truth.case_id}"
        )
        rows.truth.confusable_with = target.truth.case_id


def _split_rows(
    case_id: str,
    order_date: date,
    payment_method: str,
    gross_amount: float,
    index: int,
) -> tuple[list[LedgerEntry], list[SettlementEntry]]:
    part_count = 2 + index % 3
    base_amount = round(gross_amount / part_count, 2)
    amounts = [base_amount] * part_count
    amounts[-1] = round(gross_amount - sum(amounts[:-1]), 2)
    ledger: list[LedgerEntry] = []
    settlements: list[SettlementEntry] = []

    for part, amount in enumerate(amounts, start=1):
        fee, gst_on_fee, net_amount = calculate_settlement_amounts(
            amount, payment_method
        )
        tds_rate, tds_section = _TDS_RULES[payment_method]
        ledger.append(
            LedgerEntry(
                order_id=f"order-{case_id}-{part}",
                ref_id=f"ref-{case_id}-{part}",
                amount=amount,
                currency="INR",
                payment_method=payment_method,
                order_date=order_date,
                customer_id=f"customer-{index % 20:02d}",
            )
        )
        settlements.append(
            SettlementEntry(
                settlement_id=f"settlement-{case_id}-{part}",
                ref_id=f"ref-{case_id}-{part}",
                gross_amount=amount,
                fee=fee,
                gst_on_fee=gst_on_fee,
                net_amount=net_amount,
                payment_method=payment_method,
                settlement_date=order_date + timedelta(days=1),
                tds_deducted=round(amount * tds_rate, 2),
                tds_section=tds_section,
                challan_number=f"CHALLAN-{case_id}-{part}",
            )
        )
    return ledger, settlements


def _tax_26as_entry(case_id: str, settlement: SettlementEntry) -> Tax26ASEntry:
    rate, section = _TDS_RULES[settlement.payment_method]
    return Tax26ASEntry(
        case_id=case_id,
        ref_id=settlement.ref_id,
        tds_expected=round(settlement.gross_amount * rate, 2),
        tds_section_expected=section,
        challan_number=settlement.challan_number,
    )


def _write_csv(
    path: Path, rows: list[BaseModel], exclude: set[str] | None = None
) -> None:
    serialized = [row.model_dump(mode="json", exclude=exclude) for row in rows]
    if not serialized:
        return

    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=serialized[0].keys())
        writer.writeheader()
        for row in serialized:
            writer.writerow(
                {
                    key: json.dumps(value) if isinstance(value, list) else value
                    for key, value in row.items()
                }
            )
