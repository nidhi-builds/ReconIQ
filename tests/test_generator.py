import csv
from collections import Counter

import pytest

from data.generator import CASE_TYPES, generate_dataset, write_dataset


def test_generator_meets_partition_and_case_coverage_gate() -> None:
    dataset = generate_dataset(seed=42)

    assert len(dataset.design.ground_truth) == 85
    assert len(dataset.holdout.ground_truth) == 35

    design_counts = Counter(row.case_type for row in dataset.design.ground_truth)
    holdout_counts = Counter(row.case_type for row in dataset.holdout.ground_truth)

    assert set(design_counts) == set(CASE_TYPES)
    assert set(holdout_counts) == set(CASE_TYPES)
    assert min(design_counts.values()) >= 4
    assert min(holdout_counts.values()) >= 2


def test_generator_is_deterministic_and_partitions_are_disjoint() -> None:
    first = generate_dataset(seed=7)
    second = generate_dataset(seed=7)

    assert first == second
    assert {row.case_id for row in first.design.ground_truth}.isdisjoint(
        row.case_id for row in first.holdout.ground_truth
    )


def test_generator_injects_split_duplicate_nontransaction_and_hard_negative_cases() -> None:
    dataset = generate_dataset(seed=42)
    truth_by_type = {
        case_type: [
            row
            for partition in (dataset.design, dataset.holdout)
            for row in partition.ground_truth
            if row.case_type == case_type
        ]
        for case_type in CASE_TYPES
    }

    assert all(2 <= len(row.ledger_ids) <= 4 for row in truth_by_type["SPLIT_SETTLEMENT"])
    assert all(len(row.bank_txn_ids) == 1 for row in truth_by_type["SPLIT_SETTLEMENT"])
    assert all(len(row.ledger_ids) == 2 for row in truth_by_type["DUPLICATE_LEDGER_ENTRY"])
    assert all(row.is_transaction is False for row in truth_by_type["NON_TRANSACTION_BANK_LINE"])
    assert all(row.true_match_group is None for row in truth_by_type["HARD_NEGATIVE"])


def test_csv_export_keeps_ground_truth_out_of_matcher_inputs(tmp_path) -> None:
    write_dataset(generate_dataset(seed=42), tmp_path)

    for split in ("design", "holdout"):
        with (tmp_path / split / "bank.csv").open(newline="", encoding="utf-8") as file:
            bank_headers = next(csv.reader(file))
        with (tmp_path / split / "ground_truth.csv").open(
            newline="", encoding="utf-8"
        ) as file:
            truth_headers = next(csv.reader(file))
        with (tmp_path / split / "tax_26as.csv").open(
            newline="", encoding="utf-8"
        ) as file:
            tax_headers = next(csv.reader(file))

        assert "is_transaction" not in bank_headers
        assert "true_match_group" not in bank_headers
        assert "true_exception_reason" not in bank_headers
        assert "is_transaction" in truth_headers
        assert "true_match_group" in truth_headers
        assert "true_exception_reason" in truth_headers
        assert "tds_expected" in tax_headers
        assert "tds_expected" not in bank_headers


def test_tax_mismatches_are_derivable_from_settlement_and_26as_data() -> None:
    dataset = generate_dataset(seed=42)

    for partition in (dataset.design, dataset.holdout):
        settlements = {row.settlement_id: row for row in partition.settlements}
        tax_by_case = {row.case_id: row for row in partition.tax_26as}

        for truth in partition.ground_truth:
            if truth.case_type not in {
                "TAX_SHORT_DEDUCTION",
                "TAX_MISSING_CHALLAN",
                "TAX_WRONG_SECTION",
            }:
                continue

            settlement = settlements[truth.settlement_ids[0]]
            tax = tax_by_case[truth.case_id]
            if truth.case_type == "TAX_SHORT_DEDUCTION":
                assert settlement.tds_deducted < tax.tds_expected
            elif truth.case_type == "TAX_MISSING_CHALLAN":
                assert settlement.challan_number is None
                assert tax.challan_number is not None
            else:
                assert settlement.tds_section != tax.tds_section_expected
                assert settlement.tds_deducted == tax.tds_expected


def test_non_tax_mismatch_settlements_agree_with_26as_data() -> None:
    dataset = generate_dataset(seed=42)
    tax_case_types = {
        "TAX_SHORT_DEDUCTION",
        "TAX_MISSING_CHALLAN",
        "TAX_WRONG_SECTION",
    }

    for partition in (dataset.design, dataset.holdout):
        truth_by_case = {row.case_id: row for row in partition.ground_truth}
        settlement_by_id = {row.settlement_id: row for row in partition.settlements}
        tax_by_case = {
            case_id: [row for row in partition.tax_26as if row.case_id == case_id]
            for case_id in truth_by_case
        }

        for case_id, truth in truth_by_case.items():
            if truth.case_type in tax_case_types:
                continue
            for settlement_id, tax in zip(
                truth.settlement_ids, tax_by_case[case_id], strict=True
            ):
                settlement = settlement_by_id[settlement_id]
                assert settlement.tds_deducted == tax.tds_expected
                assert settlement.tds_section == tax.tds_section_expected
                assert settlement.challan_number == tax.challan_number


def test_hard_negatives_are_close_to_distinct_real_transactions() -> None:
    dataset = generate_dataset(seed=42)

    for partition in (dataset.design, dataset.holdout):
        truth_by_case = {row.case_id: row for row in partition.ground_truth}
        settlement_by_id = {row.settlement_id: row for row in partition.settlements}
        bank_by_id = {row.bank_txn_id: row for row in partition.bank}
        real_ref_ids = {
            row.ref_id
            for row in [*partition.ledger, *partition.settlements]
            if row.ref_id
        }
        bank_ref_counts = Counter(row.ref_id for row in partition.bank if row.ref_id)

        for truth in partition.ground_truth:
            if truth.case_type != "HARD_NEGATIVE":
                continue

            target = truth_by_case[truth.confusable_with]
            target_settlement = settlement_by_id[target.settlement_ids[0]]
            hard_negative_bank = bank_by_id[truth.bank_txn_ids[0]]
            difference = abs(hard_negative_bank.amount - target_settlement.net_amount)

            assert target.case_id != truth.case_id
            assert target.split == truth.split
            assert difference == pytest.approx(0.25)
            assert difference < 0.5
            assert abs(
                (hard_negative_bank.value_date - target_settlement.settlement_date).days
            ) <= 1
            assert hard_negative_bank.ref_id not in {
                target_settlement.ref_id,
                f"ref-{truth.case_id}",
            }
            assert hard_negative_bank.ref_id not in real_ref_ids
            assert bank_ref_counts[hard_negative_bank.ref_id] == 1


def test_refund_cases_include_original_and_linked_reversal_legs() -> None:
    dataset = generate_dataset(seed=42)

    for partition in (dataset.design, dataset.holdout):
        ledger_by_id = {row.order_id: row for row in partition.ledger}
        settlement_by_id = {row.settlement_id: row for row in partition.settlements}
        bank_by_id = {row.bank_txn_id: row for row in partition.bank}

        for truth in partition.ground_truth:
            if truth.case_type != "REFUND_REVERSAL":
                continue

            ledger = [ledger_by_id[row_id] for row_id in truth.ledger_ids]
            settlements = [settlement_by_id[row_id] for row_id in truth.settlement_ids]
            bank = [bank_by_id[row_id] for row_id in truth.bank_txn_ids]

            assert len(ledger) == 2
            assert len(settlements) == 1
            assert len(bank) == 2
            assert len([row for row in bank if row.amount > 0]) == 1
            assert len([row for row in bank if row.amount < 0]) == 1
            assert bank[0].amount == settlements[0].net_amount
            assert settlements[0].net_amount == pytest.approx(
                settlements[0].gross_amount
                - settlements[0].fee
                - settlements[0].gst_on_fee
            )
            assert truth.refund_of == settlements[0].ref_id
            assert bank[1].ref_id == f"{truth.refund_of}-refund"


def test_nontransaction_narrations_cover_the_planned_examples() -> None:
    dataset = generate_dataset(seed=42)
    narrations = set()

    for partition in (dataset.design, dataset.holdout):
        bank_by_id = {row.bank_txn_id: row for row in partition.bank}
        for truth in partition.ground_truth:
            if truth.case_type == "NON_TRANSACTION_BANK_LINE":
                narrations.add(bank_by_id[truth.bank_txn_ids[0]].narration)

    assert narrations == {
        "LOAN DISBURSEMENT",
        "GST REFUND",
        "INTERNAL TRANSFER",
    }


def test_generator_applies_distinct_payment_method_fee_rates() -> None:
    expected_rates = {
        "upi": 0.01,
        "card": 0.02,
        "netbanking": 0.015,
        "wallet": 0.018,
        "emi": 0.025,
    }
    dataset = generate_dataset(seed=42)

    assert len({round(1_000 * rate, 2) for rate in expected_rates.values()}) == 5
    for partition in (dataset.design, dataset.holdout):
        assert {row.payment_method for row in partition.settlements} == set(
            expected_rates
        )
        for row in partition.settlements:
            assert row.fee == pytest.approx(
                round(row.gross_amount * expected_rates[row.payment_method], 2)
            )


def test_generator_applies_payment_method_settlement_windows() -> None:
    expected_windows = {
        "upi": 2,
        "wallet": 3,
        "netbanking": 4,
        "card": 5,
        "emi": 6,
    }
    dataset = generate_dataset(seed=42)

    for partition in (dataset.design, dataset.holdout):
        settlement_by_id = {
            row.settlement_id: row for row in partition.settlements
        }
        bank_by_id = {row.bank_txn_id: row for row in partition.bank}
        for truth in partition.ground_truth:
            if truth.case_type not in {
                "TIMING_LAG_WITHIN_WINDOW",
                "TIMING_LAG_EXCEEDED",
            }:
                continue
            settlement = settlement_by_id[truth.settlement_ids[0]]
            bank = bank_by_id[truth.bank_txn_ids[0]]
            lag_days = (bank.value_date - settlement.settlement_date).days
            window_days = expected_windows[settlement.payment_method]

            if truth.case_type == "TIMING_LAG_WITHIN_WINDOW":
                assert lag_days == window_days - 1
            else:
                assert lag_days == window_days + 1


def test_duplicate_rows_share_the_dedup_key_but_are_distinguishable() -> None:
    dataset = generate_dataset(seed=42)

    for partition in (dataset.design, dataset.holdout):
        for truth in partition.ground_truth:
            if truth.case_type != "DUPLICATE_LEDGER_ENTRY":
                continue

            duplicates = [
                row for row in partition.ledger if row.order_id == truth.ledger_ids[0]
            ]
            assert len(duplicates) == 2
            assert duplicates[0].order_id == duplicates[1].order_id
            assert duplicates[0].amount == duplicates[1].amount
            assert duplicates[0] != duplicates[1]
            assert abs((duplicates[1].order_date - duplicates[0].order_date).days) == 1


def test_ambiguous_remainders_are_outside_deterministic_amount_tolerance() -> None:
    dataset = generate_dataset(seed=42)

    for partition in (dataset.design, dataset.holdout):
        settlement_by_id = {row.settlement_id: row for row in partition.settlements}
        bank_by_id = {row.bank_txn_id: row for row in partition.bank}
        for truth in partition.ground_truth:
            if truth.case_type != "AMBIGUOUS_REMAINDER":
                continue

            settlement = settlement_by_id[truth.settlement_ids[0]]
            bank = bank_by_id[truth.bank_txn_ids[0]]
            difference = abs(bank.amount - settlement.net_amount)
            assert 0.5 < difference < 2.0
