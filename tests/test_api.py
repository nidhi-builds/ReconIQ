from fastapi.testclient import TestClient

from api.main import create_app
from api.store import InMemoryRunStore
from data.generator import generate_dataset
from data.schemas import ReconciliationResult
from matching.llm_match import LLMDecisionRecord
from matching.pipeline import PipelineMetrics, PipelineRunResult, SourceCounts
from qa.text_to_sql_agent import AnswerSynthesis, QAProvider, SQLGeneration
from tax.gst_tds_enrichment import TaxFinding


def pipeline_output() -> PipelineRunResult:
    design = generate_dataset(seed=42).design
    return PipelineRunResult(
        results=[
            ReconciliationResult(
                record_ids=[
                    design.ledger[0].order_id,
                    design.settlements[0].settlement_id,
                    design.bank[0].bank_txn_id,
                ],
                matched=True,
                confidence=1,
                method="exact_ref",
                exception_reason=None,
                reasoning=None,
            )
        ],
        duplicates=[],
        exclusions=[],
        llm_decisions=[
            LLMDecisionRecord(
                record_ids=["order-1"],
                matched=False,
                confidence=0,
                reason_category="NO_CANDIDATE",
                reasoning="No candidate.",
                status="not_sent",
            )
        ],
        tax_findings=[
            TaxFinding(
                record_ids=[design.settlements[0].settlement_id],
                settlement_id=design.settlements[0].settlement_id,
                ref_id=design.settlements[0].ref_id,
                gst_category="PAYMENT_GATEWAY_SERVICE",
                tds_section="194H",
                expected_tds=1,
                actual_tds=1,
                status="CLEAR",
                mismatch_reason=None,
                reasoning="Clear.",
            )
        ],
        metrics=PipelineMetrics(
            source_counts=SourceCounts(ledger=1, settlements=1, bank=1, tax_26as=1),
            matched_groups=1,
            exception_groups=0,
            match_rate=1,
            stage_counts={"exact_ref": 1},
        ),
    )


def qa_provider() -> QAProvider:
    return QAProvider(
        name="test",
        model="fixed",
        generate_sql=lambda _: (
            SQLGeneration(
                sql="SELECT COUNT(*) AS count FROM reconciliation_results",
                reasoning="Count rows.",
            ),
            0,
            0,
        ),
        synthesize_answer=lambda _: (AnswerSynthesis(answer="There are no results."), 0, 0),
    )


def client(store: InMemoryRunStore | None = None) -> TestClient:
    return TestClient(
        create_app(
            store=store or InMemoryRunStore(),
            run_pipeline=lambda _pipeline_input: pipeline_output(),
            code_version=lambda: "abc123",
            qa_provider=qa_provider(),
        )
    )


def test_design_run_is_created_persisted_and_served_by_run_id() -> None:
    api = client()

    response = api.post("/api/v1/runs/design")

    assert response.status_code == 201
    run_id = response.json()["id"]
    assert response.json()["status"] == "completed"
    assert api.get("/api/v1/runs").json()[0]["id"] == run_id
    assert api.get(f"/api/v1/runs/{run_id}").status_code == 200
    page = api.get(f"/api/v1/runs/{run_id}/results").json()
    assert page["total"] == 1
    assert page["items"][0]["id"] == "result-0001"
    assert api.get(f"/api/v1/runs/{run_id}/results?status=matched").json()["total"] == 1
    assert api.get(f"/api/v1/runs/{run_id}/results?status=exception").json() == {
        "items": [],
        "total": 0,
        "offset": 0,
        "limit": 50,
    }
    assert api.get(f"/api/v1/runs/{run_id}/results/result-0001").json()["matched"] is True
    assert api.get(f"/api/v1/runs/{run_id}/metrics").status_code == 200
    assert api.get(f"/api/v1/runs/{run_id}/llm-decisions").json()["total"] == 1
    assert api.get(f"/api/v1/runs/{run_id}/tax-results").json()["total"] == 1


def test_incomplete_run_is_not_readable() -> None:
    store = InMemoryRunStore()
    queued = store.create_run(
        run_type="design",
        label="Design Run - development set",
        code_version="abc123",
        pipeline_input=create_pipeline_input(),
    )
    api = client(store)

    assert api.get("/api/v1/runs").json() == []
    assert api.get(f"/api/v1/runs/{queued.id}").status_code == 409
    assert api.post(f"/api/v1/runs/{queued.id}/questions", json={"question": "Count rows"}).status_code == 409


def test_second_holdout_returns_conflict() -> None:
    api = client()

    assert api.post("/api/v1/runs/holdout").status_code == 201
    response = api.post("/api/v1/runs/holdout")

    assert response.status_code == 409
    assert response.json()["detail"] == "A holdout run already exists."


def test_question_uses_only_the_completed_run_snapshot() -> None:
    api = client()
    run_id = api.post("/api/v1/runs/design").json()["id"]

    response = api.post(
        f"/api/v1/runs/{run_id}/questions",
        json={"question": "How many reconciliation results are there?"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ANSWERED"
    assert response.json()["rows"] == [{"count": 1}]


def test_health_and_unknown_run_errors_are_stable() -> None:
    api = client()

    assert api.get("/api/v1/health").json() == {"status": "ok"}
    assert api.get("/api/v1/runs/missing").status_code == 404


def test_default_app_uses_supabase_when_backend_credentials_exist(monkeypatch) -> None:
    marker = object()
    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "secret")
    monkeypatch.setattr("api.main.SupabaseRunStore", lambda _url, _key: marker)

    app = create_app()

    assert app.state.run_store is marker


def test_pipeline_failure_is_sanitized_and_never_listed() -> None:
    store = InMemoryRunStore()

    def fail(_pipeline_input):
        raise RuntimeError("secret provider detail")

    api = TestClient(
        create_app(
            store=store,
            run_pipeline=fail,
            code_version=lambda: "abc123",
            qa_provider=qa_provider(),
        ),
        raise_server_exceptions=False,
    )

    response = api.post("/api/v1/runs/design")

    assert response.status_code == 500
    assert response.json() == {"detail": "Run execution failed."}
    assert api.get("/api/v1/runs").json() == []


def test_csv_upload_creates_a_run_without_synthetic_accuracy_metrics() -> None:
    design = generate_dataset(seed=42).design

    def csv_for(rows):
        import csv
        from io import StringIO

        output = StringIO()
        writer = csv.DictWriter(output, fieldnames=rows[0].model_dump(mode="json").keys())
        writer.writeheader()
        writer.writerows(row.model_dump(mode="json") for row in rows)
        return output.getvalue()

    response = client().post(
        "/api/v1/runs/upload",
        json={
            "ledger_csv": csv_for(design.ledger[:1]),
            "settlement_csv": csv_for(design.settlements[:1]),
            "bank_csv": csv_for(design.bank[:1]),
            "tax_26as_csv": csv_for(design.tax_26as[:1]),
        },
    )

    assert response.status_code == 201
    assert response.json()["run_type"] == "upload"
    assert response.json()["summary"]["precision"] is None
    assert response.json()["summary"]["recall"] is None


def test_csv_upload_rejects_missing_required_source() -> None:
    response = client().post(
        "/api/v1/runs/upload",
        json={"ledger_csv": "order_id\norder-1\n", "settlement_csv": "", "bank_csv": ""},
    )

    assert response.status_code == 422


def test_local_dashboard_origin_is_allowed_for_uploads() -> None:
    response = client().options(
        "/api/v1/runs/upload",
        headers={
            "Origin": "http://127.0.0.1:3000",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 200


def create_pipeline_input():
    design = generate_dataset(seed=42).design
    from matching.pipeline import PipelineInput

    return PipelineInput(
        ledger=design.ledger,
        settlements=design.settlements,
        bank=design.bank,
        tax_26as=design.tax_26as,
    )
