from collections import Counter
from datetime import date, timedelta
import os
from time import perf_counter, sleep
from typing import Callable, TYPE_CHECKING

from pydantic import BaseModel, Field, field_validator, model_validator

from data.schemas import (
    BankEntry,
    LedgerEntry,
    ReconciliationResult,
    SettlementEntry,
)
from matching.settlement_windows import settlement_window_days

if TYPE_CHECKING:
    from observability.llm_tracker import LLMTracker


MODEL_NAME = "gemini-2.5-flash"
PROMPT_VERSION = "v2"
DecisionFunction = Callable[
    ["LLMCandidate"], tuple["GeminiMatchDecision", int, int]
]


class GeminiMatchDecision(BaseModel):
    matched: bool
    matched_ids: list[str] | None
    confidence: float = Field(ge=0, le=1)
    reason_category: str | None
    reasoning: str

    @field_validator("reasoning")
    @classmethod
    def reasoning_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reasoning must not be blank")
        return value

    @model_validator(mode="after")
    def decision_must_be_consistent(self) -> "GeminiMatchDecision":
        if self.matched and self.matched_ids is None:
            raise ValueError("matched decisions require matched_ids")
        if not self.matched and self.matched_ids is not None:
            raise ValueError("unmatched decisions cannot include matched_ids")
        if not self.matched and not (self.reason_category or "").strip():
            raise ValueError("unmatched decisions require a reason_category")
        return self


class LLMCandidate(BaseModel):
    ledger_order_id: str
    ledger_ref_id: str
    ledger_amount: float
    ledger_payment_method: str
    ledger_order_date: date
    settlement_id: str
    settlement_ref_id: str
    settlement_gross_amount: float
    settlement_net_amount: float
    settlement_payment_method: str
    settlement_date: date
    bank_txn_id: str
    bank_amount: float
    bank_value_date: date
    bank_narration: str
    amount_difference: float
    date_difference_days: int

    @property
    def record_ids(self) -> list[str]:
        return [self.ledger_order_id, self.settlement_id, self.bank_txn_id]


class LLMDecisionRecord(BaseModel):
    record_ids: list[str]
    matched: bool
    confidence: float = Field(ge=0, le=1)
    reason_category: str | None
    reasoning: str
    status: str
    cache_hit: bool = False
    latency_ms: float = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0

    @classmethod
    def from_model(
        cls,
        candidate: LLMCandidate,
        decision: GeminiMatchDecision,
        **metrics: object,
    ) -> "LLMDecisionRecord":
        if decision.matched and decision.matched_ids != candidate.record_ids:
            return cls(
                record_ids=candidate.record_ids,
                matched=False,
                confidence=0,
                reason_category="INVALID_MODEL_RESPONSE",
                reasoning="Gemini returned identifiers outside the submitted candidate.",
                status="invalid",
                **metrics,
            )
        return cls(
            record_ids=candidate.record_ids,
            matched=decision.matched,
            confidence=decision.confidence,
            reason_category=decision.reason_category,
            reasoning=decision.reasoning,
            status="evaluated",
            **metrics,
        )


class CandidateBuildResult(BaseModel):
    candidates: list[LLMCandidate]
    decisions: list[LLMDecisionRecord]


class LLMMatchResult(BaseModel):
    matches: list[ReconciliationResult]
    decisions: list[LLMDecisionRecord]
    remaining_ledger_entries: list[LedgerEntry]
    remaining_settlement_entries: list[SettlementEntry]
    remaining_bank_entries: list[BankEntry]


def _local_decision(
    record_ids: list[str], reason_category: str, reasoning: str
) -> LLMDecisionRecord:
    return LLMDecisionRecord(
        record_ids=record_ids,
        matched=False,
        confidence=0,
        reason_category=reason_category,
        reasoning=reasoning,
        status="not_sent",
    )


def _candidate(
    ledger: LedgerEntry,
    settlement: SettlementEntry,
    bank: BankEntry,
) -> LLMCandidate:
    return LLMCandidate(
        ledger_order_id=ledger.order_id,
        ledger_ref_id=ledger.ref_id or "",
        ledger_amount=ledger.amount,
        ledger_payment_method=ledger.payment_method,
        ledger_order_date=ledger.order_date,
        settlement_id=settlement.settlement_id,
        settlement_ref_id=settlement.ref_id or "",
        settlement_gross_amount=settlement.gross_amount,
        settlement_net_amount=settlement.net_amount,
        settlement_payment_method=settlement.payment_method,
        settlement_date=settlement.settlement_date,
        bank_txn_id=bank.bank_txn_id,
        bank_amount=bank.amount,
        bank_value_date=bank.value_date,
        bank_narration=bank.narration,
        amount_difference=round(abs(bank.amount - settlement.net_amount), 2),
        date_difference_days=(bank.value_date - settlement.settlement_date).days,
    )


def build_llm_candidates(
    ledger_entries: list[LedgerEntry],
    settlement_entries: list[SettlementEntry],
    bank_entries: list[BankEntry],
) -> CandidateBuildResult:
    ledger_refs = Counter(
        row.ref_id for row in ledger_entries if row.ref_id and row.ref_id.strip()
    )
    settlement_refs = Counter(
        row.ref_id
        for row in settlement_entries
        if row.ref_id and row.ref_id.strip()
    )
    settlement_by_ref = {
        row.ref_id: row
        for row in settlement_entries
        if row.ref_id and settlement_refs[row.ref_id] == 1
    }
    candidates: list[LLMCandidate] = []
    decisions: list[LLMDecisionRecord] = []

    for ledger in ledger_entries:
        ref_id = ledger.ref_id
        if not ref_id or not ref_id.strip() or ledger_refs[ref_id] != 1:
            continue
        settlement = settlement_by_ref.get(ref_id)
        if settlement is None:
            continue
        pair_ids = [ledger.order_id, settlement.settlement_id]
        if ledger.currency != "INR":
            decisions.append(
                _local_decision(
                    pair_ids,
                    "CURRENCY_MISMATCH",
                    "Non-INR records are outside the reconciliation policy.",
                )
            )
            continue
        if (
            ledger.amount <= 0
            or settlement.gross_amount <= 0
            or ledger.payment_method != settlement.payment_method
            or ledger.amount != settlement.gross_amount
        ):
            decisions.append(
                _local_decision(
                    pair_ids,
                    "INCONSISTENT_SOURCE_PAIR",
                    "Ledger and settlement records do not establish the same sale.",
                )
            )
            continue
        try:
            window_days = settlement_window_days(ledger.payment_method)
        except KeyError:
            decisions.append(
                _local_decision(
                    pair_ids,
                    "NO_CANDIDATE",
                    "The payment method has no configured settlement window.",
                )
            )
            continue

        eligible: list[tuple[int, LLMCandidate]] = []
        for bank_index, bank in enumerate(bank_entries):
            if bank.ref_id is not None and bank.ref_id.strip():
                continue
            amount_difference = round(abs(bank.amount - settlement.net_amount), 2)
            if not 0.5 <= amount_difference <= 2.0:
                continue
            candidate = _candidate(ledger, settlement, bank)
            if not (
                settlement.settlement_date
                <= bank.value_date
                <= settlement.settlement_date + timedelta(days=window_days)
            ):
                decisions.append(
                    _local_decision(
                        candidate.record_ids,
                        "TIMING_WINDOW_EXCEEDED",
                        "The bank entry falls outside the payment-method window.",
                    )
                )
                continue
            eligible.append((bank_index, candidate))

        evidence_counts = Counter(
            (row.bank_amount, row.bank_value_date, row.bank_narration)
            for _, row in eligible
        )
        indistinguishable = [
            row
            for _, row in eligible
            if evidence_counts[
                (row.bank_amount, row.bank_value_date, row.bank_narration)
            ]
            > 1
        ]
        if indistinguishable:
            decisions.append(
                _local_decision(
                    [*pair_ids, *[row.bank_txn_id for row in indistinguishable]],
                    "AMBIGUOUS_CANDIDATES",
                    "Bank candidates have indistinguishable evidence.",
                )
            )
            indistinguishable_ids = {
                row.bank_txn_id for row in indistinguishable
            }
            eligible = [
                item
                for item in eligible
                if item[1].bank_txn_id not in indistinguishable_ids
            ]

        ranked = sorted(
            eligible,
            key=lambda item: (
                item[1].amount_difference,
                item[1].date_difference_days,
                item[0],
            ),
        )[:2]
        candidates.extend(item[1] for item in ranked)

    covered_ids = {
        record_id
        for item in [*candidates, *decisions]
        for record_id in item.record_ids
    }
    input_ids = [
        *[row.order_id for row in ledger_entries],
        *[row.settlement_id for row in settlement_entries],
        *[row.bank_txn_id for row in bank_entries],
    ]
    for record_id in input_ids:
        if record_id not in covered_ids:
            decisions.append(
                _local_decision(
                    [record_id],
                    "NO_CANDIDATE",
                    "No eligible candidate could be constructed.",
                )
            )

    return CandidateBuildResult(candidates=candidates, decisions=decisions)


def finalize_llm_decisions(
    ledger_entries: list[LedgerEntry],
    settlement_entries: list[SettlementEntry],
    bank_entries: list[BankEntry],
    decisions: list[LLMDecisionRecord],
) -> LLMMatchResult:
    claims = [
        decision
        for decision in decisions
        if decision.status == "evaluated"
        and decision.matched
        and decision.confidence >= 0.5
    ]
    claim_counts = Counter(record_id for claim in claims for record_id in claim.record_ids)
    accepted = []
    for claim in claims:
        if any(claim_counts[record_id] > 1 for record_id in claim.record_ids):
            claim.status = "conflict"
            claim.matched = False
            claim.confidence = 0
            claim.reason_category = "CONFLICTING_CLAIMS"
            claim.reasoning = "The candidate conflicts with another accepted claim."
        else:
            accepted.append(claim)

    ledger_order = {row.order_id: index for index, row in enumerate(ledger_entries)}
    accepted.sort(key=lambda claim: ledger_order[claim.record_ids[0]])
    matched_ids = {record_id for claim in accepted for record_id in claim.record_ids}
    matches = [
        ReconciliationResult(
            record_ids=claim.record_ids,
            matched=True,
            confidence=claim.confidence,
            method="llm_remainder",
            exception_reason=None,
            reasoning=claim.reasoning,
        )
        for claim in accepted
    ]
    return LLMMatchResult(
        matches=matches,
        decisions=decisions,
        remaining_ledger_entries=[
            row for row in ledger_entries if row.order_id not in matched_ids
        ],
        remaining_settlement_entries=[
            row
            for row in settlement_entries
            if row.settlement_id not in matched_ids
        ],
        remaining_bank_entries=[
            row for row in bank_entries if row.bank_txn_id not in matched_ids
        ],
    )


def _ask_gemini(
    candidate: LLMCandidate, *, api_key: str, model: str
) -> tuple[GeminiMatchDecision, int, int]:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=(
            "Decide whether these ledger, gateway settlement, and bank records "
            "are the same transaction. The ledger amount is gross and has "
            "already been verified equal to settlement_gross_amount. Gateway "
            "fees explain why ledger_amount is larger than settlement_net_amount, "
            "so never compare ledger gross directly with the bank amount. The "
            "bank is expected near settlement_net_amount, and this candidate's "
            "0.50 to 2.00 difference is intentionally awaiting semantic review: "
            "accept it only when narration identifies the same reference and "
            "explains or plausibly accompanies the small difference. 'PARTIAL "
            "REF' means a truncated reference identifier, not a partial refund. "
            "An exact reference, an unambiguous shortened reference, or a single-"
            "character typo can identify the transaction; narration pointing to "
            "another reference must be rejected. Abstain when evidence remains "
            "insufficient or ambiguous. "
            "When matched, matched_ids must exactly equal the three supplied "
            "record IDs in their supplied order. When not matched, matched_ids "
            "must be null. Never invent an identifier.\n\nCandidate:\n"
            f"{candidate.model_dump_json(indent=2)}"
        ),
        config=types.GenerateContentConfig(
            temperature=0,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
            response_mime_type="application/json",
            response_schema=GeminiMatchDecision,
        ),
    )
    decision = (
        response.parsed
        if isinstance(response.parsed, GeminiMatchDecision)
        else GeminiMatchDecision.model_validate(response.parsed)
    )
    usage = response.usage_metadata
    return (
        decision,
        int(getattr(usage, "prompt_token_count", 0) or 0),
        int(getattr(usage, "candidates_token_count", 0) or 0),
    )


def match_llm_remainder(
    ledger_entries: list[LedgerEntry],
    settlement_entries: list[SettlementEntry],
    bank_entries: list[BankEntry],
    *,
    tracker: "LLMTracker",
    decide: DecisionFunction | None = None,
    api_key: str | None = None,
    model: str = MODEL_NAME,
    paid_tier: bool = True,
) -> LLMMatchResult:
    from observability.llm_tracker import estimate_gemini_flash_cost

    built = build_llm_candidates(
        ledger_entries, settlement_entries, bank_entries
    )
    decisions = list(built.decisions)
    if decide is None:
        key = api_key or os.getenv("GEMINI_API_KEY")
        if not key:
            raise ValueError("GEMINI_API_KEY is required for live Gemini matching")
        decide = lambda candidate: _ask_gemini(
            candidate, api_key=key, model=model
        )

    for candidate in built.candidates:
        cached = tracker.get_decision(model, PROMPT_VERSION, candidate)
        if cached is not None:
            record = LLMDecisionRecord.from_model(candidate, cached, cache_hit=True)
            decisions.append(record)
            tracker.log_call(
                model=model,
                prompt_version=PROMPT_VERSION,
                candidate=candidate,
                decision=cached,
                cache_hit=True,
                latency_ms=0,
            )
            continue

        for attempt in range(3):
            started = perf_counter()
            try:
                response, input_tokens, output_tokens = decide(candidate)
            except Exception:
                latency_ms = (perf_counter() - started) * 1_000
                tracker.log_call(
                    model=model,
                    prompt_version=PROMPT_VERSION,
                    candidate=candidate,
                    decision=None,
                    cache_hit=False,
                    latency_ms=latency_ms,
                    error="LLM_UNAVAILABLE",
                )
                if attempt < 2:
                    sleep(2**attempt)
                continue

            cost = estimate_gemini_flash_cost(
                input_tokens, output_tokens, paid=paid_tier
            )
            latency_ms = (perf_counter() - started) * 1_000
            record = LLMDecisionRecord.from_model(
                candidate,
                response,
                latency_ms=latency_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost_usd=cost,
            )
            if record.status != "invalid":
                tracker.store_decision(model, PROMPT_VERSION, candidate, response)
            tracker.log_call(
                model=model,
                prompt_version=PROMPT_VERSION,
                candidate=candidate,
                decision=response,
                cache_hit=False,
                latency_ms=latency_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost_usd=cost,
            )
            decisions.append(record)
            break
        else:
            decisions.append(
                _local_decision(
                    candidate.record_ids,
                    "LLM_UNAVAILABLE",
                    "Gemini did not respond after three attempts.",
                ).model_copy(update={"status": "unavailable"})
            )

    return finalize_llm_decisions(
        ledger_entries,
        settlement_entries,
        bank_entries,
        decisions,
    )
