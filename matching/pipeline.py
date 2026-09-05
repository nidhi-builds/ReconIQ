from collections import Counter

from pydantic import BaseModel, Field

from data.schemas import BankEntry, LedgerEntry, ReconciliationResult, SettlementEntry, Tax26ASEntry
from matching.dedup import DuplicateLink, deduplicate_ledger
from matching.exact_match import match_exact_references
from matching.exceptions import build_exception_evidence, categorize_exceptions
from matching.fee_adjusted_match import match_fee_adjusted
from matching.llm_match import DecisionFunction, LLMDecisionRecord, match_llm_remainder
from matching.refund_match import ConfirmedReference, match_refund_reversals
from matching.scope_filter import ScopeExclusion, filter_non_transactions
from matching.split_settlement import match_split_settlements
from observability.llm_tracker import LLMTracker
from tax.gst_tds_enrichment import TaxFinding, enrich_tax_lines


class PipelineInput(BaseModel):
    ledger: list[LedgerEntry]
    settlements: list[SettlementEntry]
    bank: list[BankEntry]
    tax_26as: list[Tax26ASEntry] = Field(default_factory=list)


class SourceCounts(BaseModel):
    ledger: int
    settlements: int
    bank: int
    tax_26as: int


class PipelineMetrics(BaseModel):
    source_counts: SourceCounts
    matched_groups: int
    exception_groups: int
    match_rate: float
    stage_counts: dict[str, int]


class PipelineRunResult(BaseModel):
    results: list[ReconciliationResult]
    duplicates: list[DuplicateLink]
    exclusions: list[ScopeExclusion]
    llm_decisions: list[LLMDecisionRecord]
    tax_findings: list[TaxFinding]
    metrics: PipelineMetrics


def run_reconciliation(
    pipeline_input: PipelineInput,
    *,
    tracker: LLMTracker,
    decide: DecisionFunction | None = None,
) -> PipelineRunResult:
    dedup = deduplicate_ledger(pipeline_input.ledger)
    scope = filter_non_transactions(pipeline_input.bank)
    exact = match_exact_references(
        dedup.retained_entries,
        pipeline_input.settlements,
        scope.remaining_entries,
    )
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

    ledger_by_id = {row.order_id: row for row in dedup.retained_entries}
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
    llm = match_llm_remainder(
        refund.remaining_ledger_entries,
        split.remaining_settlement_entries,
        refund.remaining_bank_entries,
        tracker=tracker,
        decide=decide,
    )
    exceptions = categorize_exceptions(
        build_exception_evidence(
            llm.remaining_ledger_entries,
            llm.remaining_settlement_entries,
            llm.remaining_bank_entries,
        )
    )
    matches = [
        *exact.matches,
        *fee.matches,
        *split.matches,
        *refund.matches,
        *llm.matches,
    ]
    results = [*matches, *exceptions]
    tax_findings = enrich_tax_lines(
        matches,
        pipeline_input.settlements,
        pipeline_input.tax_26as,
    )
    stage_counts = dict(Counter(row.method for row in results))

    return PipelineRunResult(
        results=results,
        duplicates=dedup.duplicates,
        exclusions=scope.exclusions,
        llm_decisions=llm.decisions,
        tax_findings=tax_findings,
        metrics=PipelineMetrics(
            source_counts=SourceCounts(
                ledger=len(pipeline_input.ledger),
                settlements=len(pipeline_input.settlements),
                bank=len(pipeline_input.bank),
                tax_26as=len(pipeline_input.tax_26as),
            ),
            matched_groups=len(matches),
            exception_groups=len(exceptions),
            match_rate=len(matches) / len(results) if results else 0,
            stage_counts=stage_counts,
        ),
    )
