import os
from datetime import date, timedelta
from pathlib import Path
from time import time_ns

import pytest
from dotenv import load_dotenv
from pydantic import ValidationError

from data.generator import generate_dataset
from data.schemas import BankEntry, LedgerEntry, SettlementEntry
from matching.dedup import deduplicate_ledger
from matching.exact_match import match_exact_references
from matching.fee_adjusted_match import match_fee_adjusted
from matching.llm_match import (
    GeminiMatchDecision,
    LLMDecisionRecord,
    PROMPT_VERSION,
    build_llm_candidates,
    finalize_llm_decisions,
    match_llm_remainder,
)
from matching.refund_match import ConfirmedReference, match_refund_reversals
from matching.scope_filter import filter_non_transactions
from matching.split_settlement import match_split_settlements
from observability.llm_tracker import LLMTracker


def ledger(
    label: str = "1",
    *,
    currency: str = "INR",
    amount: float = 1_000.0,
) -> LedgerEntry:
    return LedgerEntry(
        order_id=f"order-{label}",
        ref_id=f"ref-{label}",
        amount=amount,
        currency=currency,
        payment_method="upi",
        order_date=date(2026, 1, 1),
        customer_id="private-customer",
    )


def settlement(
    label: str = "1", *, gross_amount: float = 1_000.0
) -> SettlementEntry:
    return SettlementEntry(
        settlement_id=f"settlement-{label}",
        ref_id=f"ref-{label}",
        gross_amount=gross_amount,
        fee=16.95,
        gst_on_fee=3.05,
        net_amount=980.0,
        payment_method="upi",
        settlement_date=date(2026, 1, 2),
        tds_deducted=10.0,
        tds_section="194H",
        challan_number="CHALLAN-1",
    )


def bank(
    label: str,
    *,
    amount: float = 981.37,
    days_late: int = 0,
    ref_id: str | None = None,
    narration: str | None = None,
) -> BankEntry:
    return BankEntry(
        bank_txn_id=f"bank-{label}",
        ref_id=ref_id,
        amount=amount,
        value_date=date(2026, 1, 2) + timedelta(days=days_late),
        narration=narration or f"RZP STLMNT ref-{label}",
    )


def design_stage_five_remainder():
    design = generate_dataset(seed=42).design
    ledgers = deduplicate_ledger(design.ledger).retained_entries
    banks = filter_non_transactions(design.bank).remaining_entries
    exact = match_exact_references(ledgers, design.settlements, banks)
    fee_adjusted = match_fee_adjusted(
        exact.remaining_ledger_entries,
        exact.remaining_settlement_entries,
        exact.remaining_bank_entries,
    )
    split = match_split_settlements(
        fee_adjusted.remaining_ledger_entries,
        fee_adjusted.remaining_settlement_entries,
        fee_adjusted.remaining_bank_entries,
    )
    ledger_by_id = {row.order_id: row for row in ledgers}
    confirmations = [
        ConfirmedReference(
            ref_id=ledger_by_id[match.record_ids[0]].ref_id,
            original_amount=ledger_by_id[match.record_ids[0]].amount,
            match=match,
        )
        for match in exact.matches
        if ledger_by_id[match.record_ids[0]].ref_id is not None
    ]
    refund = match_refund_reversals(
        split.remaining_ledger_entries,
        split.remaining_bank_entries,
        confirmations,
    )
    return (
        design,
        refund.remaining_ledger_entries,
        split.remaining_settlement_entries,
        refund.remaining_bank_entries,
    )


def hard_negative_bank_ids_by_variant(design):
    truth_by_case = {truth.case_id: truth for truth in design.ground_truth}
    settlement_by_id = {row.settlement_id: row for row in design.settlements}
    bank_by_id = {row.bank_txn_id: row for row in design.bank}
    variants = {"semantic": set(), "filter": set()}
    for truth in design.ground_truth:
        if truth.case_type != "HARD_NEGATIVE":
            continue
        target = truth_by_case[truth.confusable_with]
        difference = abs(
            bank_by_id[truth.bank_txn_ids[0]].amount
            - settlement_by_id[target.settlement_ids[0]].net_amount
        )
        variant = "semantic" if 0.5 <= difference <= 2.0 else "filter"
        variants[variant].add(truth.bank_txn_ids[0])
    return variants


def test_candidate_builder_sends_narration_and_only_top_two_ranked_banks() -> None:
    banks = [
        bank("third", amount=981.80),
        bank("second", amount=981.50),
        bank("first", amount=981.20, narration="PARTIAL REF ref-1"),
    ]

    result = build_llm_candidates([ledger()], [settlement()], banks)

    assert [candidate.bank_txn_id for candidate in result.candidates] == [
        "bank-first",
        "bank-second",
    ]
    payload = result.candidates[0].model_dump(mode="json")
    assert payload["bank_narration"] == "PARTIAL REF ref-1"
    assert "private-customer" not in str(payload)
    assert "currency" not in payload


def test_candidate_builder_applies_pair_and_bank_policy_filters() -> None:
    valid_bank = bank("valid")

    assert build_llm_candidates(
        [ledger(currency="USD")], [settlement()], [valid_bank]
    ).candidates == []
    assert build_llm_candidates(
        [ledger(amount=999.0)], [settlement()], [valid_bank]
    ).candidates == []
    assert build_llm_candidates(
        [ledger()], [settlement()], [bank("referenced", ref_id="other-ref")]
    ).candidates == []


@pytest.mark.parametrize(
    ("amount", "included"),
    [(980.49, False), (980.50, True), (982.00, True), (982.01, False)],
)
def test_candidate_builder_applies_inclusive_fuzzy_band(
    amount: float, included: bool
) -> None:
    result = build_llm_candidates(
        [ledger()], [settlement()], [bank("candidate", amount=amount)]
    )

    assert bool(result.candidates) is included


def test_candidate_builder_drops_only_the_out_of_window_candidate() -> None:
    in_window = bank("in-window", days_late=2)
    out_of_window = bank("late", days_late=3)

    result = build_llm_candidates(
        [ledger()], [settlement()], [out_of_window, in_window]
    )

    assert [candidate.bank_txn_id for candidate in result.candidates] == [
        "bank-in-window"
    ]
    assert any(
        decision.reason_category == "TIMING_WINDOW_EXCEEDED"
        and decision.record_ids[-1] == "bank-late"
        for decision in result.decisions
    )


def test_candidate_builder_refuses_indistinguishable_banks() -> None:
    first = bank("a", narration="SAME EVIDENCE")
    second = bank("b", narration="SAME EVIDENCE")

    result = build_llm_candidates([ledger()], [settlement()], [first, second])

    assert result.candidates == []
    assert len(result.decisions) == 1
    assert result.decisions[0].record_ids == [
        "order-1",
        "settlement-1",
        "bank-a",
        "bank-b",
    ]
    assert result.decisions[0].reason_category == "AMBIGUOUS_CANDIDATES"


def test_candidate_builder_checks_indistinguishable_banks_before_top_two_cutoff() -> None:
    distinct = bank("distinct", amount=980.50, narration="DISTINCT EVIDENCE")
    first_twin = bank("twin-a", amount=981.00, narration="SAME EVIDENCE")
    second_twin = bank("twin-b", amount=981.00, narration="SAME EVIDENCE")

    result = build_llm_candidates(
        [ledger()], [settlement()], [distinct, first_twin, second_twin]
    )

    assert [candidate.bank_txn_id for candidate in result.candidates] == [
        "bank-distinct"
    ]
    assert any(
        decision.reason_category == "AMBIGUOUS_CANDIDATES"
        and decision.record_ids
        == ["order-1", "settlement-1", "bank-twin-a", "bank-twin-b"]
        for decision in result.decisions
    )


def test_finalizer_accepts_only_exact_ids_at_or_above_confidence_floor() -> None:
    entries = ([ledger()], [settlement()], [bank("1")])
    candidate = build_llm_candidates(*entries).candidates[0]
    accepted = LLMDecisionRecord.from_model(
        candidate,
        GeminiMatchDecision(
            matched=True,
            matched_ids=candidate.record_ids,
            confidence=0.5,
            reason_category=None,
            reasoning="Narration and transaction details identify the payment.",
        ),
    )

    result = finalize_llm_decisions(*entries, [accepted])

    assert len(result.matches) == 1
    assert result.matches[0].record_ids == candidate.record_ids
    assert result.matches[0].method == "llm_remainder"
    assert result.remaining_ledger_entries == []
    assert result.remaining_settlement_entries == []
    assert result.remaining_bank_entries == []


def test_finalizer_rejects_low_confidence_and_invented_ids() -> None:
    entries = ([ledger()], [settlement()], [bank("1")])
    candidate = build_llm_candidates(*entries).candidates[0]
    decisions = [
        LLMDecisionRecord.from_model(
            candidate,
            GeminiMatchDecision(
                matched=True,
                matched_ids=candidate.record_ids,
                confidence=0.49,
                reason_category="LOW_CONFIDENCE",
                reasoning="Some evidence agrees, but not enough.",
            ),
        ),
        LLMDecisionRecord.from_model(
            candidate,
            GeminiMatchDecision(
                matched=True,
                matched_ids=["invented", *candidate.record_ids[1:]],
                confidence=0.99,
                reason_category=None,
                reasoning="The response contains an invalid identifier.",
            ),
        ),
    ]

    result = finalize_llm_decisions(*entries, decisions)

    assert result.matches == []
    assert result.remaining_ledger_entries == list(entries[0])
    assert result.remaining_settlement_entries == list(entries[1])
    assert result.remaining_bank_entries == list(entries[2])
    assert decisions[1].status == "invalid"
    assert decisions[1].confidence == 0


@pytest.mark.parametrize(
    "values",
    [
        {
            "matched": False,
            "matched_ids": ["order-1", "settlement-1", "bank-1"],
            "confidence": 0.2,
            "reason_category": "UNCERTAIN",
            "reasoning": "Evidence is insufficient.",
        },
        {
            "matched": True,
            "matched_ids": None,
            "confidence": 0.9,
            "reason_category": None,
            "reasoning": "Evidence agrees.",
        },
        {
            "matched": False,
            "matched_ids": None,
            "confidence": 0.2,
            "reason_category": None,
            "reasoning": "Evidence is insufficient.",
        },
        {
            "matched": False,
            "matched_ids": None,
            "confidence": 0.2,
            "reason_category": "UNCERTAIN",
            "reasoning": "   ",
        },
    ],
)
def test_gemini_decision_rejects_internally_inconsistent_output(values) -> None:
    with pytest.raises(ValidationError):
        GeminiMatchDecision.model_validate(values)


def test_finalizer_rejects_all_globally_conflicting_claims() -> None:
    ledgers = [ledger("1"), ledger("2")]
    settlements = [settlement("1"), settlement("2")]
    shared_bank = bank("shared")
    candidates = build_llm_candidates(ledgers, settlements, [shared_bank]).candidates
    decisions = [
        LLMDecisionRecord.from_model(
            candidate,
            GeminiMatchDecision(
                matched=True,
                matched_ids=candidate.record_ids,
                confidence=0.9,
                reason_category=None,
                reasoning="Candidate appears consistent.",
            ),
        )
        for candidate in candidates
    ]

    result = finalize_llm_decisions(
        ledgers, settlements, [shared_bank], decisions
    )

    assert len(decisions) == 2
    assert result.matches == []
    assert all(decision.status == "conflict" for decision in decisions)
    assert all(decision.confidence == 0 for decision in decisions)


def test_matcher_reuses_cached_real_response_without_another_call(
    tmp_path: Path,
) -> None:
    tracker = LLMTracker(tmp_path / "llm.sqlite3")
    calls = 0

    def decide(candidate):
        nonlocal calls
        calls += 1
        return (
            GeminiMatchDecision(
                matched=True,
                matched_ids=candidate.record_ids,
                confidence=0.9,
                reason_category=None,
                reasoning="The narration identifies the same reference.",
            ),
            50,
            20,
        )

    first = match_llm_remainder(
        [ledger()], [settlement()], [bank("1")], tracker=tracker, decide=decide
    )
    second = match_llm_remainder(
        [ledger()], [settlement()], [bank("1")], tracker=tracker, decide=decide
    )

    assert calls == 1
    assert len(first.matches) == len(second.matches) == 1
    assert first.decisions[0].cache_hit is False
    assert second.decisions[0].cache_hit is True
    assert first.decisions[0].latency_ms > 0
    assert second.decisions[0].latency_ms == 0
    assert first.decisions[0].estimated_cost_usd == 0.000065


def test_matcher_does_not_cache_response_with_invented_ids(tmp_path: Path) -> None:
    tracker = LLMTracker(tmp_path / "llm.sqlite3")
    calls = 0

    def decide(candidate):
        nonlocal calls
        calls += 1
        return (
            GeminiMatchDecision(
                matched=True,
                matched_ids=["invented", *candidate.record_ids[1:]],
                confidence=0.99,
                reason_category=None,
                reasoning="The model returned an identifier outside the candidate.",
            ),
            50,
            20,
        )

    first = match_llm_remainder(
        [ledger()], [settlement()], [bank("1")], tracker=tracker, decide=decide
    )
    second = match_llm_remainder(
        [ledger()], [settlement()], [bank("1")], tracker=tracker, decide=decide
    )

    assert calls == 2
    assert first.decisions[0].status == second.decisions[0].status == "invalid"
    candidate = build_llm_candidates(
        [ledger()], [settlement()], [bank("1")]
    ).candidates[0]
    assert tracker.get_decision(
        "gemini-2.5-flash", PROMPT_VERSION, candidate
    ) is None


def test_matcher_retries_three_times_and_does_not_cache_outage(
    tmp_path: Path, monkeypatch
) -> None:
    tracker = LLMTracker(tmp_path / "llm.sqlite3")
    attempts = 0

    def unavailable(candidate):
        nonlocal attempts
        attempts += 1
        raise TimeoutError("provider timed out")

    monkeypatch.setattr("matching.llm_match.sleep", lambda _: None)
    result = match_llm_remainder(
        [ledger()],
        [settlement()],
        [bank("1")],
        tracker=tracker,
        decide=unavailable,
    )

    assert attempts == 3
    assert result.matches == []
    assert result.decisions[0].reason_category == "LLM_UNAVAILABLE"
    assert result.decisions[0].reasoning
    candidate = build_llm_candidates(
        [ledger()], [settlement()], [bank("1")]
    ).candidates[0]
    assert tracker.get_decision(
        "gemini-2.5-flash", PROMPT_VERSION, candidate
    ) is None
    assert tracker.metrics().failed_calls == 3


def test_design_candidates_include_only_declared_ambiguous_and_semantic_cases() -> None:
    design, ledgers, settlements, banks = design_stage_five_remainder()
    built = build_llm_candidates(ledgers, settlements, banks)
    truth_by_case = {truth.case_id: truth for truth in design.ground_truth}
    truth_by_ledger = {
        ledger_id: truth
        for truth in design.ground_truth
        for ledger_id in truth.ledger_ids
    }
    truth_by_bank = {
        bank_id: truth
        for truth in design.ground_truth
        for bank_id in truth.bank_txn_ids
    }
    variants = hard_negative_bank_ids_by_variant(design)
    semantic_bank_ids = variants["semantic"]
    filter_bank_ids = variants["filter"]

    assert built.candidates
    for candidate in built.candidates:
        ledger_truth = truth_by_ledger[candidate.ledger_order_id]
        bank_truth = truth_by_bank[candidate.bank_txn_id]
        if bank_truth.case_type == "HARD_NEGATIVE":
            assert candidate.bank_txn_id in semantic_bank_ids
            assert bank_truth.confusable_with == ledger_truth.case_id
        else:
            assert ledger_truth.case_type == "AMBIGUOUS_REMAINDER"
            assert bank_truth.case_id in {
                ledger_truth.case_id,
                ledger_truth.confusable_with,
            }

    candidates_by_semantic_bank = {
        bank_id: [
            candidate
            for candidate in built.candidates
            if candidate.bank_txn_id == bank_id
        ]
        for bank_id in semantic_bank_ids
    }
    assert all(
        len(candidates) == 1
        and truth_by_ledger[candidates[0].ledger_order_id].case_id
        == truth_by_bank[bank_id].confusable_with
        for bank_id, candidates in candidates_by_semantic_bank.items()
    )
    assert filter_bank_ids.isdisjoint(
        candidate.bank_txn_id for candidate in built.candidates
    )

    candidate_ledger_ids = {
        candidate.ledger_order_id for candidate in built.candidates
    }
    expected_ledger_ids = {
        truth.ledger_ids[0]
        for truth in design.ground_truth
        if truth.case_type == "AMBIGUOUS_REMAINDER"
    }
    expected_ledger_ids.update(
        truth_by_case[truth.confusable_with].ledger_ids[0]
        for truth in design.ground_truth
        if truth.case_type == "HARD_NEGATIVE"
        and truth.bank_txn_ids[0] in semantic_bank_ids
    )
    assert candidate_ledger_ids == expected_ledger_ids

    covered_ids = {
        record_id
        for item in [*built.candidates, *built.decisions]
        for record_id in item.record_ids
    }
    assert covered_ids == {
        *[row.order_id for row in ledgers],
        *[row.settlement_id for row in settlements],
        *[row.bank_txn_id for row in banks],
    }


@pytest.mark.live
def test_live_gemini_passes_design_truth_gate() -> None:
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        pytest.fail("Set GEMINI_API_KEY in .env before running the live gate")

    design, ledgers, settlements, banks = design_stage_five_remainder()
    evaluation_path = Path(
        os.getenv(
            "LLM_EVALUATION_DB",
            f"observability/evaluations/stage6_{PROMPT_VERSION}_{time_ns()}.sqlite3",
        )
    )
    tracker = LLMTracker(evaluation_path)
    result = match_llm_remainder(
        ledgers,
        settlements,
        banks,
        tracker=tracker,
        api_key=api_key,
    )
    expected = {
        (truth.ledger_ids[0], truth.settlement_ids[0], truth.bank_txn_ids[0])
        for truth in design.ground_truth
        if truth.case_type == "AMBIGUOUS_REMAINDER"
    }
    actual = {tuple(match.record_ids) for match in result.matches}
    true_positives = len(actual & expected)
    precision = true_positives / len(actual) if actual else 0
    recall = true_positives / len(expected)
    hard_negative_bank_ids = {
        bank_id
        for truth in design.ground_truth
        if truth.case_type == "HARD_NEGATIVE"
        for bank_id in truth.bank_txn_ids
    }
    variants = hard_negative_bank_ids_by_variant(design)
    semantic_decisions = [
        decision
        for decision in result.decisions
        if variants["semantic"].intersection(decision.record_ids)
        and len(decision.record_ids) == 3
    ]
    filter_guard_candidates = [
        candidate
        for candidate in build_llm_candidates(ledgers, settlements, banks).candidates
        if candidate.bank_txn_id in variants["filter"]
    ]
    covered_ids = {
        record_id for decision in result.decisions for record_id in decision.record_ids
    }
    metrics = tracker.metrics(resolved_count=len(actual))

    print(
        {
            "precision": precision,
            "recall": recall,
            "accepted": len(actual),
            "abstained_or_rejected": len(expected) - true_positives,
            "api_log_entries": metrics.total_calls,
            "input_tokens": metrics.input_tokens,
            "output_tokens": metrics.output_tokens,
            "average_latency_ms": metrics.average_latency_ms,
            "estimated_cost_usd": metrics.estimated_cost_usd,
            "cache_hit_rate": metrics.cache_hit_rate,
            "confidence_distribution": metrics.confidence_distribution,
            "cost_per_resolved_match_usd": metrics.cost_per_resolved_match_usd,
            "raw_log": str(evaluation_path),
            "filter_guard_exclusion_rate": 1.0
            if not filter_guard_candidates
            else 0.0,
            "filter_guard_sample_size": len(variants["filter"]),
            "semantic_decoy_rejection_rate": sum(
                not decision.matched for decision in semantic_decisions
            )
            / len(variants["semantic"]),
            "semantic_decoy_sample_size": len(variants["semantic"]),
        }
    )
    assert precision == 1.0
    assert recall >= 0.8
    assert not hard_negative_bank_ids.intersection(
        record_id for match in result.matches for record_id in match.record_ids
    )
    assert filter_guard_candidates == []
    assert len(semantic_decisions) == len(variants["semantic"]) == 2
    assert all(not decision.matched for decision in semantic_decisions)
    assert all(match.confidence >= 0.5 for match in result.matches)
    assert all(decision.reasoning.strip() for decision in result.decisions)
    assert covered_ids == {
        *[row.order_id for row in ledgers],
        *[row.settlement_id for row in settlements],
        *[row.bank_txn_id for row in banks],
    }
    assert metrics.input_tokens > 0
    assert metrics.output_tokens > 0
