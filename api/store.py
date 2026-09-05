from datetime import datetime, timezone
import json
from typing import Callable, Protocol
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from api.models import RunRecord, RunType
from data.schemas import BankEntry, LedgerEntry, SettlementEntry, Tax26ASEntry
from matching.evaluation import EvaluationMetrics
from matching.dedup import DuplicateLink
from matching.pipeline import PipelineInput, PipelineRunResult
from matching.scope_filter import ScopeExclusion
from matching.llm_match import LLMDecisionRecord
from data.schemas import ReconciliationResult
from tax.gst_tds_enrichment import TaxFinding


class DuplicateHoldoutError(ValueError):
    pass


class InvalidRunStateError(ValueError):
    pass


class RunNotFoundError(KeyError):
    pass


class RunStore(Protocol):
    def create_run(
        self,
        *,
        run_type: RunType,
        label: str,
        code_version: str,
        pipeline_input: PipelineInput,
    ) -> RunRecord: ...

    def mark_running(self, run_id: str) -> RunRecord: ...

    def complete_run(
        self,
        run_id: str,
        *,
        output: PipelineRunResult,
        evaluation: EvaluationMetrics | None,
        duration_ms: float,
    ) -> RunRecord: ...

    def fail_run(self, run_id: str, error: str) -> RunRecord: ...

    def get(self, run_id: str) -> RunRecord: ...

    def get_completed(self, run_id: str) -> RunRecord: ...

    def list_completed(self) -> list[RunRecord]: ...


class InMemoryRunStore:
    def __init__(self) -> None:
        self._runs: dict[str, RunRecord] = {}

    def create_run(
        self,
        *,
        run_type: RunType,
        label: str,
        code_version: str,
        pipeline_input: PipelineInput,
    ) -> RunRecord:
        if run_type == "holdout" and any(
            row.run_type == "holdout" for row in self._runs.values()
        ):
            raise DuplicateHoldoutError("a holdout run already exists")
        row = RunRecord(
            id=str(uuid4()),
            run_type=run_type,
            label=label,
            status="queued",
            code_version=code_version,
            created_at=datetime.now(timezone.utc),
            pipeline_input=pipeline_input,
        )
        self._runs[row.id] = row
        return row.model_copy(deep=True)

    def mark_running(self, run_id: str) -> RunRecord:
        return self._transition(run_id, "queued", status="running")

    def complete_run(
        self,
        run_id: str,
        *,
        output: PipelineRunResult,
        evaluation: EvaluationMetrics | None,
        duration_ms: float,
    ) -> RunRecord:
        return self._transition(
            run_id,
            "running",
            status="completed",
            output=output,
            evaluation=evaluation,
            duration_ms=duration_ms,
            completed_at=datetime.now(timezone.utc),
        )

    def fail_run(self, run_id: str, error: str) -> RunRecord:
        return self._transition(
            run_id,
            "running",
            status="failed",
            error=error,
            completed_at=datetime.now(timezone.utc),
        )

    def get(self, run_id: str) -> RunRecord:
        try:
            return self._runs[run_id].model_copy(deep=True)
        except KeyError as exc:
            raise RunNotFoundError(run_id) from exc

    def get_completed(self, run_id: str) -> RunRecord:
        row = self.get(run_id)
        if row.status != "completed":
            raise InvalidRunStateError("run is not completed")
        return row

    def list_completed(self) -> list[RunRecord]:
        return sorted(
            (row.model_copy(deep=True) for row in self._runs.values() if row.status == "completed"),
            key=lambda row: (row.run_type != "holdout", -row.created_at.timestamp()),
        )

    def _transition(self, run_id: str, expected: str, **changes: object) -> RunRecord:
        row = self.get(run_id)
        if row.status != expected:
            raise InvalidRunStateError(f"expected {expected}, got {row.status}")
        updated = row.model_copy(update=changes, deep=True)
        self._runs[run_id] = updated
        return updated.model_copy(deep=True)


RequestFunction = Callable[[str, str, object | None], list[dict[str, object]]]


class SupabaseRunStore:
    def __init__(
        self,
        url: str,
        service_role_key: str,
        *,
        request: RequestFunction | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.service_role_key = service_role_key
        self._request = request or self._request_supabase

    def create_run(
        self,
        *,
        run_type: RunType,
        label: str,
        code_version: str,
        pipeline_input: PipelineInput,
    ) -> RunRecord:
        run_id = str(uuid4())
        created_at = datetime.now(timezone.utc)
        payload = {
            "id": run_id,
            "run_type": run_type,
            "label": label,
            "status": "queued",
            "code_version": code_version,
            "created_at": created_at.isoformat(),
        }
        try:
            self._request("POST", "runs", payload)
        except HTTPError as exc:
            if exc.code == 409 and run_type == "holdout":
                raise DuplicateHoldoutError("a holdout run already exists") from exc
            raise
        sources = serialize_sources(run_id, pipeline_input)
        try:
            if sources:
                self._request("POST", "source_records", sources)
        except Exception:
            try:
                self._patch_status(
                    run_id,
                    "queued",
                    {
                        "status": "failed",
                        "error": "SOURCE_PERSISTENCE_FAILED",
                        "completed_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
            except Exception:
                pass
            raise
        return RunRecord(
            **payload,
            pipeline_input=pipeline_input,
        )

    def mark_running(self, run_id: str) -> RunRecord:
        self._patch_status(run_id, "queued", {"status": "running"})
        return self.get(run_id)

    def complete_run(
        self,
        run_id: str,
        *,
        output: PipelineRunResult,
        evaluation: EvaluationMetrics | None,
        duration_ms: float,
    ) -> RunRecord:
        if self.get(run_id).status != "running":
            raise InvalidRunStateError("run is not running")
        self._insert_payloads(run_id, "reconciliation_results", output.results)
        self._insert_payloads(run_id, "llm_decisions", output.llm_decisions)
        self._insert_payloads(run_id, "tax_findings", output.tax_findings)
        if evaluation is not None:
            self._request(
                "POST",
                "evaluation_metrics",
                {"run_id": run_id, "payload": evaluation.model_dump(mode="json")},
            )
        self._patch_status(
            run_id,
            "running",
            {
                "status": "completed",
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "duration_ms": duration_ms,
                "pipeline_metrics": output.metrics.model_dump(mode="json"),
                "duplicates": [row.model_dump(mode="json") for row in output.duplicates],
                "exclusions": [row.model_dump(mode="json") for row in output.exclusions],
            },
        )
        return self.get(run_id)

    def fail_run(self, run_id: str, error: str) -> RunRecord:
        self._patch_status(
            run_id,
            "running",
            {
                "status": "failed",
                "error": error,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return self.get(run_id)

    def get(self, run_id: str) -> RunRecord:
        rows = self._request("GET", f"runs?id=eq.{run_id}&select=*", None)
        if not rows:
            raise RunNotFoundError(run_id)
        row = rows[0]
        source_rows = self._request(
            "GET",
            f"source_records?run_id=eq.{run_id}&select=source_type,ordinal,payload",
            None,
        )
        output = None
        evaluation = None
        if row["status"] == "completed":
            output = PipelineRunResult(
                results=self._read_payloads(run_id, "reconciliation_results", ReconciliationResult),
                duplicates=[DuplicateLink.model_validate(item) for item in row.get("duplicates") or []],
                exclusions=[ScopeExclusion.model_validate(item) for item in row.get("exclusions") or []],
                llm_decisions=self._read_payloads(run_id, "llm_decisions", LLMDecisionRecord),
                tax_findings=self._read_payloads(run_id, "tax_findings", TaxFinding),
                metrics=row["pipeline_metrics"],
            )
            evaluation_rows = self._request(
                "GET",
                f"evaluation_metrics?run_id=eq.{run_id}&select=payload",
                None,
            )
            evaluation = (
                EvaluationMetrics.model_validate(evaluation_rows[0]["payload"])
                if evaluation_rows
                else None
            )
        return RunRecord(
            id=str(row["id"]),
            run_type=row["run_type"],
            label=str(row["label"]),
            status=row["status"],
            code_version=str(row["code_version"]),
            created_at=row["created_at"],
            completed_at=row.get("completed_at"),
            duration_ms=row.get("duration_ms"),
            error=row.get("error"),
            pipeline_input=deserialize_sources(source_rows),
            output=output,
            evaluation=evaluation,
        )

    def get_completed(self, run_id: str) -> RunRecord:
        row = self.get(run_id)
        if row.status != "completed":
            raise InvalidRunStateError("run is not completed")
        return row

    def list_completed(self) -> list[RunRecord]:
        rows = self._request(
            "GET",
            "runs?status=eq.completed&run_type=in.(design,holdout,upload)&select=id",
            None,
        )
        completed = [self.get(str(row["id"])) for row in rows]
        return sorted(
            completed,
            key=lambda row: (row.run_type != "holdout", -row.created_at.timestamp()),
        )

    def _patch_status(
        self, run_id: str, expected: str, changes: dict[str, object]
    ) -> None:
        rows = self._request(
            "PATCH",
            f"runs?id=eq.{run_id}&status=eq.{expected}",
            changes,
        )
        if not rows:
            raise InvalidRunStateError(f"run is not {expected}")

    def _insert_payloads(self, run_id: str, table: str, rows: list) -> None:
        if rows:
            self._request(
                "POST",
                table,
                [
                    {
                        "run_id": run_id,
                        "ordinal": index,
                        "payload": row.model_dump(mode="json"),
                    }
                    for index, row in enumerate(rows)
                ],
            )

    def _read_payloads(self, run_id: str, table: str, model: type) -> list:
        rows = self._request(
            "GET",
            f"{table}?run_id=eq.{run_id}&select=payload&order=ordinal",
            None,
        )
        return [model.model_validate(row["payload"]) for row in rows]

    def _request_supabase(
        self, method: str, path: str, payload: object | None
    ) -> list[dict[str, object]]:
        body = json.dumps(payload).encode() if payload is not None else None
        request = Request(
            f"{self.url}/rest/v1/{path}",
            data=body,
            method=method,
            headers={
                "apikey": self.service_role_key,
                "Authorization": f"Bearer {self.service_role_key}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
        )
        with urlopen(request, timeout=30) as response:
            content = response.read()
        return json.loads(content) if content else []


_SOURCE_MODELS = {
    "ledger": LedgerEntry,
    "settlements": SettlementEntry,
    "bank": BankEntry,
    "tax_26as": Tax26ASEntry,
}


def serialize_sources(run_id: str, source: PipelineInput) -> list[dict[str, object]]:
    return [
        {
            "run_id": run_id,
            "source_type": source_type,
            "ordinal": ordinal,
            "payload": row.model_dump(mode="json"),
        }
        for source_type in _SOURCE_MODELS
        for ordinal, row in enumerate(getattr(source, source_type))
    ]


def deserialize_sources(rows: list[dict[str, object]]) -> PipelineInput:
    grouped = {source_type: [] for source_type in _SOURCE_MODELS}
    for row in sorted(rows, key=lambda item: (str(item["source_type"]), int(item["ordinal"]))):
        source_type = str(row["source_type"])
        model = _SOURCE_MODELS.get(source_type)
        if model is None:
            raise ValueError(f"unknown source type: {source_type}")
        grouped[source_type].append(model.model_validate(row["payload"]))
    return PipelineInput(**grouped)
