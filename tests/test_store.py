from datetime import date
from copy import deepcopy
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from api.store import (
    DuplicateHoldoutError,
    InMemoryRunStore,
    InvalidRunStateError,
    SupabaseRunStore,
    deserialize_sources,
    serialize_sources,
)
from data.generator import generate_dataset
from matching.evaluation import EvaluationMetrics
from matching.pipeline import PipelineInput, PipelineMetrics, PipelineRunResult, SourceCounts


def pipeline_input() -> PipelineInput:
    design = generate_dataset(seed=42).design
    return PipelineInput(
        ledger=design.ledger[:1],
        settlements=design.settlements[:1],
        bank=design.bank[:1],
        tax_26as=design.tax_26as[:1],
    )


def empty_output() -> PipelineRunResult:
    return PipelineRunResult(
        results=[],
        duplicates=[],
        exclusions=[],
        llm_decisions=[],
        tax_findings=[],
        metrics=PipelineMetrics(
            source_counts=SourceCounts(ledger=1, settlements=1, bank=1, tax_26as=1),
            matched_groups=0,
            exception_groups=0,
            match_rate=0,
            stage_counts={},
        ),
    )


def empty_evaluation() -> EvaluationMetrics:
    return EvaluationMetrics(
        match_precision=0,
        match_recall=0,
        match_rate=0,
        hard_negative_precision=1,
        tax_accuracy=1,
        exception_metrics={},
    )


def test_store_lifecycle_only_lists_completed_runs() -> None:
    store = InMemoryRunStore()
    queued = store.create_run(
        run_type="design",
        label="Design Run - development set",
        code_version="abc123",
        pipeline_input=pipeline_input(),
    )

    assert queued.status == "queued"
    assert store.list_completed() == []

    running = store.mark_running(queued.id)
    assert running.status == "running"
    assert store.list_completed() == []

    completed = store.complete_run(
        queued.id,
        output=empty_output(),
        evaluation=empty_evaluation(),
        duration_ms=125,
    )
    assert completed.status == "completed"
    assert store.get_completed(queued.id) == completed
    assert store.list_completed() == [completed]

    with pytest.raises(InvalidRunStateError):
        store.complete_run(
            queued.id,
            output=empty_output(),
            evaluation=empty_evaluation(),
            duration_ms=125,
        )


def test_store_rejects_a_second_holdout_even_if_first_failed() -> None:
    store = InMemoryRunStore()
    first = store.create_run(
        run_type="holdout",
        label="Final Holdout Run - untouched",
        code_version="abc123",
        pipeline_input=pipeline_input(),
    )
    store.mark_running(first.id)
    failed = store.fail_run(first.id, "provider unavailable")

    assert failed.status == "failed"
    assert store.list_completed() == []
    with pytest.raises(DuplicateHoldoutError):
        store.create_run(
            run_type="holdout",
            label="Final Holdout Run - untouched",
            code_version="def456",
            pipeline_input=pipeline_input(),
        )


def test_source_json_round_trip_preserves_dates_amounts_and_types() -> None:
    original = pipeline_input()

    restored = deserialize_sources(serialize_sources("run-1", original))

    assert restored == original
    assert isinstance(restored.ledger[0].order_date, date)
    assert isinstance(restored.settlements[0].gross_amount, float)
    assert isinstance(restored.bank[0].value_date, date)
    assert isinstance(restored.tax_26as[0].tds_expected, float)


def test_supabase_create_writes_run_then_source_rows() -> None:
    calls = []

    def request(method: str, path: str, payload=None):
        calls.append((method, path, payload))
        return [payload] if path == "runs" else []

    store = SupabaseRunStore("https://project.supabase.co", "secret", request=request)

    created = store.create_run(
        run_type="design",
        label="Design Run - development set",
        code_version="abc123",
        pipeline_input=pipeline_input(),
    )

    assert created.status == "queued"
    assert calls[0][0:2] == ("POST", "runs")
    assert calls[1][0:2] == ("POST", "source_records")
    assert all(row["run_id"] == created.id for row in calls[1][2])


def test_migration_enforces_single_holdout_and_cascading_run_storage() -> None:
    sql = Path("db/migrations/001_runs.sql").read_text()

    assert "CREATE UNIQUE INDEX one_active_holdout" in sql
    assert "WHERE run_type = 'holdout'" in sql
    assert "ON DELETE CASCADE" in sql
    for table in (
        "runs",
        "source_records",
        "reconciliation_results",
        "llm_decisions",
        "tax_findings",
        "evaluation_metrics",
    ):
        assert f"CREATE TABLE {table}" in sql


def test_supabase_store_reconstructs_a_completed_typed_run() -> None:
    tables: dict[str, list[dict]] = {}

    def request(method: str, path: str, payload=None):
        table, _, query = path.partition("?")
        rows = tables.setdefault(table, [])
        if method == "POST":
            inserted = deepcopy(payload if isinstance(payload, list) else [payload])
            rows.extend(inserted)
            return deepcopy(inserted)
        params = parse_qs(query)
        selected = rows
        for field in ("id", "run_id", "status"):
            if field in params and params[field][0].startswith("eq."):
                value = params[field][0].removeprefix("eq.")
                selected = [row for row in selected if str(row.get(field)) == value]
        if method == "PATCH":
            for row in selected:
                row.update(deepcopy(payload))
            return deepcopy(selected)
        if params.get("order") == ["ordinal"]:
            selected = sorted(selected, key=lambda row: row["ordinal"])
        return deepcopy(selected)

    store = SupabaseRunStore("https://project.supabase.co", "secret", request=request)
    source = pipeline_input()
    created = store.create_run(
        run_type="design",
        label="Design Run - development set",
        code_version="abc123",
        pipeline_input=source,
    )
    store.mark_running(created.id)

    completed = store.complete_run(
        created.id,
        output=empty_output(),
        evaluation=empty_evaluation(),
        duration_ms=125,
    )

    assert completed.pipeline_input == source
    assert completed.output == empty_output()
    assert completed.evaluation == empty_evaluation()
    assert store.get_completed(created.id) == completed


def test_supabase_source_write_failure_marks_the_created_run_failed() -> None:
    rows = []

    def request(method: str, path: str, payload=None):
        if method == "POST" and path == "runs":
            rows.append(deepcopy(payload))
            return [payload]
        if method == "POST" and path == "source_records":
            raise ConnectionError("network interrupted")
        if method == "PATCH" and path.startswith("runs?"):
            rows[0].update(payload)
            return deepcopy(rows)
        return []

    store = SupabaseRunStore("https://project.supabase.co", "secret", request=request)

    with pytest.raises(ConnectionError):
        store.create_run(
            run_type="design",
            label="Design Run - development set",
            code_version="abc123",
            pipeline_input=pipeline_input(),
        )

    assert rows[0]["status"] == "failed"
