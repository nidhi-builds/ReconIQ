from pathlib import Path

from matching.llm_match import GeminiMatchDecision, LLMCandidate
from observability.llm_tracker import LLMTracker, estimate_gemini_flash_cost


def candidate(narration: str = "RZP STLMNT ref-1") -> LLMCandidate:
    return LLMCandidate.model_validate(
        {
            "ledger_order_id": "order-1",
            "ledger_ref_id": "ref-1",
            "ledger_amount": 1000,
            "ledger_payment_method": "upi",
            "ledger_order_date": "2026-01-01",
            "settlement_id": "settlement-1",
            "settlement_ref_id": "ref-1",
            "settlement_gross_amount": 1000,
            "settlement_net_amount": 980,
            "settlement_payment_method": "upi",
            "settlement_date": "2026-01-02",
            "bank_txn_id": "bank-1",
            "bank_amount": 981.37,
            "bank_value_date": "2026-01-02",
            "bank_narration": narration,
            "amount_difference": 1.37,
            "date_difference_days": 0,
        }
    )


def decision() -> GeminiMatchDecision:
    return GeminiMatchDecision(
        matched=True,
        matched_ids=["order-1", "settlement-1", "bank-1"],
        confidence=0.91,
        reason_category=None,
        reasoning="The narration contains the shortened settlement reference.",
    )


def test_cache_persists_valid_decisions_across_tracker_instances(
    tmp_path: Path,
) -> None:
    path = tmp_path / "llm.sqlite3"
    tracker = LLMTracker(path)
    tracker.store_decision("gemini-2.5-flash", "v1", candidate(), decision())

    reopened = LLMTracker(path)

    assert reopened.get_decision(
        "gemini-2.5-flash", "v1", candidate()
    ) == decision()


def test_cache_key_changes_with_model_prompt_or_candidate(tmp_path: Path) -> None:
    tracker = LLMTracker(tmp_path / "llm.sqlite3")
    tracker.store_decision("gemini-2.5-flash", "v1", candidate(), decision())

    assert tracker.get_decision("other-model", "v1", candidate()) is None
    assert tracker.get_decision("gemini-2.5-flash", "v2", candidate()) is None
    assert tracker.get_decision(
        "gemini-2.5-flash", "v1", candidate("DIFFERENT NARRATION")
    ) is None


def test_call_log_reports_usage_latency_cache_and_errors(tmp_path: Path) -> None:
    tracker = LLMTracker(tmp_path / "llm.sqlite3")

    tracker.log_call(
        model="gemini-2.5-flash",
        prompt_version="v1",
        candidate=candidate(),
        decision=decision(),
        cache_hit=False,
        latency_ms=125,
        input_tokens=80,
        output_tokens=20,
        estimated_cost_usd=0,
    )
    tracker.log_call(
        model="gemini-2.5-flash",
        prompt_version="v1",
        candidate=candidate(),
        decision=None,
        cache_hit=False,
        latency_ms=500,
        error="LLM_UNAVAILABLE",
    )

    metrics = tracker.metrics()

    assert metrics.total_calls == 2
    assert metrics.cache_hits == 0
    assert metrics.failed_calls == 1
    assert metrics.input_tokens == 80
    assert metrics.output_tokens == 20
    assert metrics.average_latency_ms == 312.5
    assert metrics.estimated_cost_usd == 0


def test_cost_estimate_distinguishes_free_and_paid_usage() -> None:
    assert estimate_gemini_flash_cost(1_000_000, 1_000_000, paid=False) == 0
    assert estimate_gemini_flash_cost(1_000_000, 1_000_000, paid=True) == 2.8


def test_metrics_report_cache_confidence_and_cost_per_resolved_match(
    tmp_path: Path,
) -> None:
    tracker = LLMTracker(tmp_path / "llm.sqlite3")
    tracker.log_call(
        model="gemini-2.5-flash",
        prompt_version="v2",
        candidate=candidate(),
        decision=decision(),
        cache_hit=False,
        latency_ms=100,
        estimated_cost_usd=0.003,
    )
    tracker.log_call(
        model="gemini-2.5-flash",
        prompt_version="v2",
        candidate=candidate("UNCERTAIN"),
        decision=GeminiMatchDecision(
            matched=False,
            matched_ids=None,
            confidence=0.3,
            reason_category="UNCERTAIN",
            reasoning="The narration is insufficient.",
        ),
        cache_hit=True,
        latency_ms=0,
    )

    metrics = tracker.metrics(resolved_count=1)

    assert metrics.cache_hit_rate == 0.5
    assert metrics.confidence_distribution == {
        "below_0_5": 1,
        "from_0_5_to_0_79": 0,
        "from_0_8_to_1": 1,
    }
    assert metrics.cost_per_resolved_match_usd == 0.003
