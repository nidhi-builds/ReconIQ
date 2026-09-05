import csv
from collections.abc import Callable
from io import StringIO
from time import perf_counter

from pydantic import BaseModel, ValidationError

from data.generator import generate_dataset
from data.schemas import BankEntry, LedgerEntry, SettlementEntry, Tax26ASEntry
from matching.evaluation import evaluate_run
from matching.pipeline import PipelineInput, PipelineRunResult
from api.models import RunRecord, RunType
from api.store import RunStore


PipelineRunner = Callable[[PipelineInput], PipelineRunResult]


class RunService:
    def __init__(
        self,
        store: RunStore,
        run_pipeline: PipelineRunner,
        code_version: Callable[[], str],
    ) -> None:
        self.store = store
        self.run_pipeline = run_pipeline
        self.code_version = code_version

    def create_partition(self, run_type: RunType) -> RunRecord:
        partition = getattr(generate_dataset(seed=42), run_type)
        pipeline_input = PipelineInput(
            ledger=partition.ledger,
            settlements=partition.settlements,
            bank=partition.bank,
            tax_26as=partition.tax_26as,
        )
        label = (
            "Final Holdout Run - untouched"
            if run_type == "holdout"
            else "Design Run - development set"
        )
        return self._execute(run_type, label, pipeline_input, partition.ground_truth)

    def create_upload(
        self,
        *,
        ledger_csv: str,
        settlement_csv: str,
        bank_csv: str,
        tax_26as_csv: str = "",
    ) -> RunRecord:
        return self._execute(
            "upload",
            "Uploaded reconciliation run",
            PipelineInput(
                ledger=_read_csv(ledger_csv, LedgerEntry, "ledger"),
                settlements=_read_csv(settlement_csv, SettlementEntry, "settlement"),
                bank=_read_csv(bank_csv, BankEntry, "bank"),
                tax_26as=_read_csv(tax_26as_csv, Tax26ASEntry, "tax_26as", required=False),
            ),
            None,
        )

    def _execute(
        self,
        run_type: RunType,
        label: str,
        pipeline_input: PipelineInput,
        ground_truth=None,
    ) -> RunRecord:
        run = self.store.create_run(
            run_type=run_type,
            label=label,
            code_version=self.code_version(),
            pipeline_input=pipeline_input,
        )
        self.store.mark_running(run.id)
        started = perf_counter()
        try:
            output = self.run_pipeline(pipeline_input)
            evaluation = evaluate_run(output, ground_truth) if ground_truth is not None else None
            return self.store.complete_run(
                run.id,
                output=output,
                evaluation=evaluation,
                duration_ms=(perf_counter() - started) * 1_000,
            )
        except Exception as exc:
            self.store.fail_run(run.id, type(exc).__name__)
            raise


def _read_csv(text: str, model: type[BaseModel], source: str, *, required: bool = True) -> list:
    if not text.strip():
        if required:
            raise ValueError(f"{source} CSV is required")
        return []
    try:
        rows = list(csv.DictReader(StringIO(text)))
        if not rows and required:
            raise ValueError(f"{source} CSV must contain at least one row")
        return [model.model_validate(row) for row in rows]
    except (csv.Error, ValidationError, ValueError) as exc:
        raise ValueError(f"Invalid {source} CSV: {exc}") from exc
