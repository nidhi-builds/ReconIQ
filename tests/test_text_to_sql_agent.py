from datetime import timedelta
import json
import os

import pytest
from dotenv import load_dotenv

from data.schemas import ReconciliationResult, SettlementEntry
from qa.text_to_sql_agent import (
    AnswerSynthesis,
    QAProvider,
    QueryTimeoutError,
    SQLGeneration,
    UnsafeQueryError,
    _generate_with_nvidia,
    answer_question,
    build_snapshot,
    execute_readonly,
    make_gemini_provider,
    make_nvidia_provider,
)
from tax.gst_tds_enrichment import TaxFinding


def result(
    *record_ids: str,
    matched: bool = True,
    confidence: float | None = None,
    method: str | None = None,
    exception_reason: str | None = None,
) -> ReconciliationResult:
    return ReconciliationResult(
        record_ids=list(record_ids),
        matched=matched,
        confidence=confidence if confidence is not None else 1 if matched else 0,
        method=method or ("exact_ref" if matched else "exception_rules"),
        exception_reason=(
            exception_reason
            if exception_reason is not None
            else None
            if matched
            else "MISSING_REF_ID"
        ),
        reasoning="Source records reconciled." if matched else "Reference is missing.",
    )


def settlement(
    label: str = "1",
    *,
    amount: float = 1_000,
    net_amount: float | None = None,
    fee: float = 16.95,
    gst_on_fee: float = 3.05,
    payment_method: str = "upi",
) -> SettlementEntry:
    return SettlementEntry(
        settlement_id=f"settlement-{label}",
        ref_id=f"ref-{label}",
        gross_amount=amount,
        fee=fee,
        gst_on_fee=gst_on_fee,
        net_amount=net_amount if net_amount is not None else amount - fee - gst_on_fee,
        payment_method=payment_method,
        settlement_date="2026-01-02",
        tds_deducted=10,
        tds_section="194H",
        challan_number="CH-1",
    )


def tax_finding(
    label: str = "1",
    *,
    expected: float = 10,
    actual: float = 10,
    reason: str | None = None,
) -> TaxFinding:
    return TaxFinding(
        record_ids=[f"order-{label}", f"settlement-{label}", f"bank-{label}"],
        settlement_id=f"settlement-{label}",
        ref_id=f"ref-{label}",
        gst_category="PAYMENT_GATEWAY_SERVICE",
        tds_section="194H",
        expected_tds=expected,
        actual_tds=actual,
        status="MISMATCH" if reason else "CLEAR",
        mismatch_reason=reason,
        reasoning="Tax values agree.",
    )


def test_snapshot_exposes_only_reconciliation_and_tax_rows() -> None:
    connection = build_snapshot(
        [result("order-1", "settlement-1", "bank-1")],
        [settlement()],
        [tax_finding()],
    )

    rows = execute_readonly(
        connection,
        "SELECT method, gross_amount, payment_method FROM reconciliation_results",
    )
    tax_rows = execute_readonly(
        connection,
        "SELECT status, mismatch_reason FROM tax_enrichment",
    )

    assert rows[0].values == {
        "method": "exact_ref",
        "gross_amount": 1_000.0,
        "payment_method": "upi",
    }
    assert tax_rows[0].values == {"status": "CLEAR", "mismatch_reason": None}


def test_snapshot_aggregates_split_settlements_by_membership() -> None:
    connection = build_snapshot(
        [
            result(
                "order-1",
                "order-2",
                "settlement-2",
                "settlement-1",
                "bank-1",
            )
        ],
        [settlement("1", amount=600), settlement("2", amount=400)],
        [],
    )

    row = execute_readonly(
        connection,
        "SELECT settlement_count, gross_amount, net_amount "
        "FROM reconciliation_results",
    )[0]

    assert row.values == {
        "settlement_count": 2,
        "gross_amount": 1_000.0,
        "net_amount": 960.0,
    }


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM reconciliation_results",
        "PRAGMA table_info(reconciliation_results)",
        "SELECT name FROM sqlite_master",
        "SELECT * FROM secret_table",
        "SELECT * FROM reconciliation_results; DROP TABLE reconciliation_results",
    ],
)
def test_readonly_executor_rejects_unsafe_or_unknown_sql(sql: str) -> None:
    connection = build_snapshot([], [], [])

    with pytest.raises(UnsafeQueryError):
        execute_readonly(connection, sql)

    assert execute_readonly(
        connection, "SELECT COUNT(*) AS count FROM reconciliation_results"
    )[0].values == {"count": 0}


def test_readonly_executor_caps_output_at_fifty_rows() -> None:
    rows = [
        result(f"order-{index}", f"settlement-{index}", f"bank-{index}")
        for index in range(60)
    ]
    settlements = [settlement(str(index)) for index in range(60)]
    connection = build_snapshot(rows, settlements, [])

    output = execute_readonly(
        connection,
        "SELECT result_id FROM reconciliation_results ORDER BY result_id",
    )

    assert len(output) == 50


def test_readonly_executor_aborts_expensive_queries() -> None:
    connection = build_snapshot([], [], [])
    sql = """
        WITH RECURSIVE counter(value) AS (
            SELECT 1
            UNION ALL
            SELECT value + 1 FROM counter WHERE value < 100000000
        )
        SELECT SUM(value) AS total FROM counter
    """

    with pytest.raises(QueryTimeoutError):
        execute_readonly(connection, sql, max_runtime=timedelta(milliseconds=1))


def test_separate_snapshots_cannot_see_another_runs_rows() -> None:
    first = build_snapshot(
        [result("order-1", "settlement-1", "bank-1")], [settlement()], []
    )
    second = build_snapshot(
        [result("order-2", "settlement-2", "bank-2")], [settlement("2")], []
    )

    first_ids = execute_readonly(
        first, "SELECT record_ids FROM reconciliation_results"
    )
    second_ids = execute_readonly(
        second, "SELECT record_ids FROM reconciliation_results"
    )

    assert "order-1" in first_ids[0].values["record_ids"]
    assert "order-1" not in second_ids[0].values["record_ids"]


def qa_provider(
    name: str,
    *,
    sql: str = "SELECT COUNT(*) AS count FROM reconciliation_results",
    answer: str = "There is one result.",
    sql_error: Exception | None = None,
    answer_error: Exception | None = None,
    calls: list[str] | None = None,
    input_cost: float = 0,
    output_cost: float = 0,
) -> QAProvider:
    def generate(_prompt: str) -> tuple[SQLGeneration, int, int]:
        if calls is not None:
            calls.append(f"{name}:sql")
        if sql_error:
            raise sql_error
        return SQLGeneration(sql=sql, reasoning="Count matching rows."), 100, 10

    def synthesize(_prompt: str) -> tuple[AnswerSynthesis, int, int]:
        if calls is not None:
            calls.append(f"{name}:answer")
        if answer_error:
            raise answer_error
        return AnswerSynthesis(answer=answer), 50, 5

    return QAProvider(
        name=name,
        model=f"{name}-model",
        generate_sql=generate,
        synthesize_answer=synthesize,
        input_cost_per_million=input_cost,
        output_cost_per_million=output_cost,
    )


def qa_inputs() -> tuple[
    list[ReconciliationResult], list[SettlementEntry], list[TaxFinding]
]:
    return (
        [result("order-1", "settlement-1", "bank-1")],
        [settlement()],
        [tax_finding()],
    )


def test_answer_question_uses_gemini_for_both_phases() -> None:
    calls: list[str] = []
    response = answer_question(
        "How many results are there?",
        *qa_inputs(),
        primary=qa_provider("gemini", calls=calls),
    )

    assert response.status == "ANSWERED"
    assert response.answer == "There is one result."
    assert response.rows[0].values == {"count": 1}
    assert response.sql_provider == "gemini"
    assert response.answer_provider == "gemini"
    assert response.fallback_used is False
    assert calls == ["gemini:sql", "gemini:answer"]


def test_sql_generation_fallback_keeps_nvidia_for_answer() -> None:
    calls: list[str] = []
    response = answer_question(
        "How many results are there?",
        *qa_inputs(),
        primary=qa_provider("gemini", sql_error=TimeoutError(), calls=calls),
        fallback=qa_provider("nvidia", calls=calls),
    )

    assert response.status == "ANSWERED"
    assert response.sql_provider == "nvidia"
    assert response.answer_provider == "nvidia"
    assert response.fallback_used is True
    assert calls == ["gemini:sql", "nvidia:sql", "nvidia:answer"]


def test_answer_question_does_not_mutate_source_records() -> None:
    results, settlements, findings = qa_inputs()
    before = [
        [row.model_dump_json() for row in records]
        for records in (results, settlements, findings)
    ]

    answer_question(
        "How many results are there?",
        results,
        settlements,
        findings,
        primary=qa_provider("gemini"),
    )

    assert [
        [row.model_dump_json() for row in records]
        for records in (results, settlements, findings)
    ] == before


def test_only_answer_phase_falls_back_after_valid_gemini_sql() -> None:
    calls: list[str] = []
    response = answer_question(
        "How many results are there?",
        *qa_inputs(),
        primary=qa_provider("gemini", answer_error=TimeoutError(), calls=calls),
        fallback=qa_provider("nvidia", calls=calls),
    )

    assert response.status == "ANSWERED"
    assert response.sql_provider == "gemini"
    assert response.answer_provider == "nvidia"
    assert calls == ["gemini:sql", "gemini:answer", "nvidia:answer"]


@pytest.mark.parametrize(
    "unsafe_sql",
    [
        "DROP TABLE reconciliation_results",
        "SELECT * FROM reconciliation_results; DELETE FROM reconciliation_results",
    ],
)
def test_unsafe_sql_refuses_without_fallback_or_answer_call(
    unsafe_sql: str,
) -> None:
    calls: list[str] = []
    response = answer_question(
        "Delete the results.",
        *qa_inputs(),
        primary=qa_provider("gemini", sql=unsafe_sql, calls=calls),
        fallback=qa_provider("nvidia", calls=calls),
    )

    assert response.status == "REFUSED"
    assert response.generated_sql is None
    assert response.rows == []
    assert calls == []
    assert response.metrics == []


def test_runtime_abort_refuses_without_fallback() -> None:
    calls: list[str] = []
    expensive = """
        WITH RECURSIVE counter(value) AS (
            SELECT 1 UNION ALL SELECT value + 1 FROM counter
        ) SELECT SUM(value) AS total FROM counter
    """
    response = answer_question(
        "Run forever.",
        *qa_inputs(),
        primary=qa_provider("gemini", sql=expensive, calls=calls),
        fallback=qa_provider("nvidia", calls=calls),
        max_runtime=timedelta(milliseconds=1),
    )

    assert response.status == "REFUSED"
    assert calls == ["gemini:sql"]
    assert response.metrics[0].failure_reason == "QUERY_TIMEOUT"


def test_syntax_error_retries_sql_with_fallback() -> None:
    calls: list[str] = []
    response = answer_question(
        "How many results are there?",
        *qa_inputs(),
        primary=qa_provider("gemini", sql="SELECT FROM", calls=calls),
        fallback=qa_provider("nvidia", calls=calls),
    )

    assert response.status == "ANSWERED"
    assert response.sql_provider == "nvidia"
    assert calls == ["gemini:sql", "nvidia:sql", "nvidia:answer"]
    assert response.metrics[0].failure_reason == "INVALID_SQL"


def test_empty_rows_use_deterministic_answer_without_synthesis() -> None:
    calls: list[str] = []
    response = answer_question(
        "Show impossible records.",
        *qa_inputs(),
        primary=qa_provider(
            "gemini",
            sql="SELECT result_id FROM reconciliation_results WHERE confidence < 0",
            calls=calls,
        ),
    )

    assert response.status == "ANSWERED"
    assert response.answer == "No matching records were found."
    assert response.answer_provider is None
    assert calls == ["gemini:sql"]


def test_both_provider_outages_return_unavailable() -> None:
    response = answer_question(
        "How many results are there?",
        *qa_inputs(),
        primary=qa_provider("gemini", sql_error=TimeoutError()),
        fallback=qa_provider("nvidia", sql_error=TimeoutError()),
    )

    assert response.status == "QA_UNAVAILABLE"
    assert response.answer == "The question service is currently unavailable."
    assert len(response.metrics) == 2


def test_metrics_capture_tokens_cost_and_fallback_reason() -> None:
    response = answer_question(
        "How many results are there?",
        *qa_inputs(),
        primary=qa_provider("gemini", sql_error=ValueError("bad json")),
        fallback=qa_provider(
            "nvidia", input_cost=2, output_cost=4
        ),
    )

    assert response.metrics[0].failure_reason == "PROVIDER_ERROR"
    assert response.metrics[1].fallback_reason == "PROVIDER_ERROR"
    assert response.metrics[1].estimated_cost_usd == pytest.approx(0.00024)
    assert response.metrics[2].estimated_cost_usd == pytest.approx(0.00012)


def test_sql_prompt_defines_exact_domain_values() -> None:
    prompts: list[str] = []

    def generate(prompt: str) -> tuple[SQLGeneration, int, int]:
        prompts.append(prompt)
        return SQLGeneration(sql="SELECT 1 AS value", reasoning="Return one."), 0, 0

    provider = QAProvider(
        name="test",
        model="test",
        generate_sql=generate,
        synthesize_answer=lambda _: (AnswerSynthesis(answer="One."), 0, 0),
    )

    answer_question("Count exact matches.", *qa_inputs(), primary=provider)

    assert "exact_ref" in prompts[0]
    assert "split_settlement" in prompts[0]
    assert "SHORT_DEDUCTION" in prompts[0]
    assert "payment_method values are lowercase" in prompts[0]


@pytest.mark.parametrize(
    ("factory", "expected_name", "expected_model"),
    [
        (make_gemini_provider, "google", "gemini-2.5-flash"),
        (
            make_nvidia_provider,
            "nvidia",
            "nvidia/nemotron-3.5-lightning-30b-a3b",
        ),
    ],
)
def test_live_provider_factories_pin_models_and_validate_both_schemas(
    factory, expected_name: str, expected_model: str, monkeypatch
) -> None:
    calls: list[tuple[str, type]] = []

    def fake_generate(
        _api_key: str, model: str, _prompt: str, schema: type
    ) -> tuple[object, int, int]:
        calls.append((model, schema))
        payload = (
            {"sql": "SELECT 1 AS value", "reasoning": "Return one."}
            if schema is SQLGeneration
            else {"answer": "One."}
        )
        return schema.model_validate(payload), 10, 2

    helper = "_generate_with_gemini" if expected_name == "google" else "_generate_with_nvidia"
    monkeypatch.setattr(f"qa.text_to_sql_agent.{helper}", fake_generate)
    provider = factory("secret")

    sql, _, _ = provider.generate_sql("sql prompt")
    answer, _, _ = provider.synthesize_answer("answer prompt")

    assert provider.name == expected_name
    assert provider.model == expected_model
    assert sql.sql == "SELECT 1 AS value"
    assert answer.answer == "One."
    assert calls == [
        (expected_model, SQLGeneration),
        (expected_model, AnswerSynthesis),
    ]


def test_nvidia_prompt_requests_a_json_instance_not_the_schema(monkeypatch) -> None:
    class FakeResponse:
        def __init__(self, body: dict) -> None:
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(self.body).encode()

    def fake_urlopen(request, timeout: int):
        prompt = json.loads(request.data)["messages"][0]["content"]
        content = (
            {"sql": "SELECT 1 AS value", "reasoning": "Return one."}
            if "Return a JSON instance, not the schema definition" in prompt
            else SQLGeneration.model_json_schema()
        )
        return FakeResponse(
            {
                "choices": [{"message": {"content": json.dumps(content)}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }
        )

    monkeypatch.setattr("qa.text_to_sql_agent.urlopen", fake_urlopen)

    generated, input_tokens, output_tokens = _generate_with_nvidia(
        "secret", "model", "Return SQL selecting one.", SQLGeneration
    )

    assert generated.sql == "SELECT 1 AS value"
    assert (input_tokens, output_tokens) == (10, 5)


QA_QUESTIONS = [
    ("How many reconciliation results are there?", "SELECT COUNT(*) AS count FROM reconciliation_results", [{"count": 7}]),
    ("How many results are matched?", "SELECT COUNT(*) AS count FROM reconciliation_results WHERE matched = 1", [{"count": 4}]),
    ("How many results remain unresolved?", "SELECT COUNT(*) AS count FROM reconciliation_results WHERE matched = 0", [{"count": 3}]),
    ("What percentage of results are matched?", "SELECT ROUND(AVG(matched) * 100, 2) AS match_rate_percent FROM reconciliation_results", [{"match_rate_percent": 57.14}]),
    ("Show counts grouped by reconciliation method.", "SELECT method, COUNT(*) AS count FROM reconciliation_results GROUP BY method ORDER BY method", [{"method": "exact_ref", "count": 1}, {"method": "exception_rules", "count": 3}, {"method": "fee_adjusted", "count": 1}, {"method": "llm", "count": 1}, {"method": "split_settlement", "count": 1}]),
    ("Show unresolved counts grouped by exception reason.", "SELECT exception_reason, COUNT(*) AS count FROM reconciliation_results WHERE matched = 0 GROUP BY exception_reason ORDER BY exception_reason", [{"exception_reason": "AMOUNT_MISMATCH_UNEXPLAINED", "count": 1}, {"exception_reason": "MISSING_REF_ID", "count": 1}, {"exception_reason": "TIMING_LAG_EXCEEDED", "count": 1}]),
    ("How many exact reference matches are there?", "SELECT COUNT(*) AS count FROM reconciliation_results WHERE method = 'exact_ref'", [{"count": 1}]),
    ("How many matches were decided by the LLM?", "SELECT COUNT(*) AS count FROM reconciliation_results WHERE method = 'llm'", [{"count": 1}]),
    ("How many split-settlement match groups are there?", "SELECT COUNT(*) AS count FROM reconciliation_results WHERE method = 'split_settlement'", [{"count": 1}]),
    ("What is the average confidence of matched results?", "SELECT AVG(confidence) AS average_confidence FROM reconciliation_results WHERE matched = 1", [{"average_confidence": 0.945}]),
    ("What is the total matched gross amount?", "SELECT SUM(gross_amount) AS total_gross_amount FROM reconciliation_results WHERE matched = 1", [{"total_gross_amount": 4500.0}]),
    ("What is the total matched net amount?", "SELECT SUM(net_amount) AS total_net_amount FROM reconciliation_results WHERE matched = 1", [{"total_net_amount": 4410.0}]),
    ("What is the total gateway fee on matched results?", "SELECT SUM(fee) AS total_fee FROM reconciliation_results WHERE matched = 1", [{"total_fee": 76.28}]),
    ("What gross amount was matched through UPI?", "SELECT SUM(gross_amount) AS upi_gross_amount FROM reconciliation_results WHERE matched = 1 AND payment_method = 'upi'", [{"upi_gross_amount": 1500.0}]),
    ("Which payment method has the highest matched gross amount?", "SELECT payment_method FROM reconciliation_results WHERE matched = 1 GROUP BY payment_method ORDER BY SUM(gross_amount) DESC LIMIT 1", [{"payment_method": "card"}]),
    ("How many tax findings are there?", "SELECT COUNT(*) AS count FROM tax_enrichment", [{"count": 5}]),
    ("How many tax findings are mismatches?", "SELECT COUNT(*) AS count FROM tax_enrichment WHERE status = 'MISMATCH'", [{"count": 3}]),
    ("Show tax finding counts grouped by status.", "SELECT status, COUNT(*) AS count FROM tax_enrichment GROUP BY status ORDER BY status", [{"status": "CLEAR", "count": 2}, {"status": "MISMATCH", "count": 3}]),
    ("How many short deductions were detected?", "SELECT COUNT(*) AS count FROM tax_enrichment WHERE mismatch_reason = 'SHORT_DEDUCTION'", [{"count": 1}]),
    ("How many missing challans were detected?", "SELECT COUNT(*) AS count FROM tax_enrichment WHERE mismatch_reason = 'MISSING_CHALLAN'", [{"count": 1}]),
    ("How many wrong TDS sections were detected?", "SELECT COUNT(*) AS count FROM tax_enrichment WHERE mismatch_reason = 'WRONG_SECTION'", [{"count": 1}]),
    ("What is the total short-deduction amount?", "SELECT SUM(expected_tds - actual_tds) AS short_deduction FROM tax_enrichment WHERE mismatch_reason = 'SHORT_DEDUCTION'", [{"short_deduction": 5.0}]),
    ("Show EMI reconciliation results.", "SELECT result_id FROM reconciliation_results WHERE payment_method = 'emi'", []),
    ("Delete every reconciliation result.", "DELETE FROM reconciliation_results", "REFUSED"),
]


def qa_design_inputs() -> tuple[
    list[ReconciliationResult], list[SettlementEntry], list[TaxFinding]
]:
    results = [
        result("order-1", "settlement-1", "bank-1", method="exact_ref"),
        result("order-2", "settlement-2", "bank-2", confidence=0.98, method="fee_adjusted"),
        result("order-3a", "order-3b", "settlement-3b", "settlement-3a", "bank-3", confidence=0.95, method="split_settlement"),
        result("order-4", "settlement-4", "bank-4", confidence=0.85, method="llm"),
        result("order-5", matched=False, exception_reason="MISSING_REF_ID"),
        result("order-6", "settlement-6", matched=False, exception_reason="TIMING_LAG_EXCEEDED"),
        result("bank-7", matched=False, exception_reason="AMOUNT_MISMATCH_UNEXPLAINED"),
    ]
    settlements = [
        settlement("1", amount=1000, net_amount=980, payment_method="upi"),
        settlement("2", amount=2000, net_amount=1960, fee=33.9, gst_on_fee=6.1, payment_method="card"),
        settlement("3a", amount=600, net_amount=588, fee=10.17, gst_on_fee=1.83, payment_method="wallet"),
        settlement("3b", amount=400, net_amount=392, fee=6.78, gst_on_fee=1.22, payment_method="wallet"),
        settlement("4", amount=500, net_amount=490, fee=8.48, gst_on_fee=1.52, payment_method="upi"),
    ]
    split_ids = results[2].record_ids
    findings = [
        tax_finding("1"),
        tax_finding("2", expected=20, actual=15, reason="SHORT_DEDUCTION"),
        tax_finding("3a", expected=6, actual=6, reason="MISSING_CHALLAN").model_copy(update={"record_ids": split_ids}),
        tax_finding("3b", expected=4, actual=4).model_copy(update={"record_ids": split_ids}),
        tax_finding("4", expected=5, actual=5, reason="WRONG_SECTION"),
    ]
    return results, settlements, findings


def rows_match(actual: list[dict], expected: list[dict]) -> bool:
    if len(actual) != len(expected):
        return False

    def value_matches(left: object, right: object) -> bool:
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            return abs(float(left) - float(right)) < 0.01
        return left == right

    remaining = list(actual)
    for expected_row in expected:
        match_index = next(
            (
                index
                for index, actual_row in enumerate(remaining)
                if all(
                    value_matches(actual_row[key], expected_value)
                    if key in actual_row
                    else any(
                        value_matches(actual_value, expected_value)
                        for actual_value in actual_row.values()
                    )
                    for key, expected_value in expected_row.items()
                )
            ),
            None,
        )
        if match_index is None:
            return False
        remaining.pop(match_index)
    return True


def test_question_scorer_accepts_correct_rows_with_extra_evidence_columns() -> None:
    assert rows_match(
        [{"payment_method": "card", "total_gross_amount": 2_000.0}],
        [{"payment_method": "card"}],
    )


def test_twenty_four_question_design_gate_scores_returned_rows() -> None:
    sql_by_question = {question: sql for question, sql, _ in QA_QUESTIONS}

    def generate(prompt: str) -> tuple[SQLGeneration, int, int]:
        question = next(question for question in sql_by_question if question in prompt)
        return SQLGeneration(sql=sql_by_question[question], reasoning="Use the requested aggregate."), 0, 0

    provider = QAProvider(
        name="offline",
        model="fixed-sql",
        generate_sql=generate,
        synthesize_answer=lambda _: (AnswerSynthesis(answer="Grounded answer."), 0, 0),
    )
    correct = 0
    unsafe_rejections = 0
    for question, _, expected in QA_QUESTIONS:
        response = answer_question(question, *qa_design_inputs(), primary=provider)
        if expected == "REFUSED":
            correct += response.status == "REFUSED"
            unsafe_rejections += response.status == "REFUSED"
        else:
            correct += rows_match([row.values for row in response.rows], expected)

    assert len(QA_QUESTIONS) == 24
    assert correct / len(QA_QUESTIONS) >= 0.9
    assert unsafe_rejections == 1


@pytest.mark.live
@pytest.mark.parametrize("provider_name", ["gemini", "nvidia"])
def test_live_qa_provider_passes_twenty_four_question_gate(provider_name: str) -> None:
    load_dotenv()
    key_name = "GEMINI_API_KEY" if provider_name == "gemini" else "NVIDIA_API_KEY"
    api_key = os.getenv(key_name)
    if not api_key:
        pytest.fail(f"Set {key_name} in .env before running the live Q&A gate")
    live = make_gemini_provider(api_key) if provider_name == "gemini" else make_nvidia_provider(api_key)
    sql_only = QAProvider(
        name=live.name,
        model=live.model,
        generate_sql=live.generate_sql,
        synthesize_answer=lambda _: (AnswerSynthesis(answer="Grounded answer."), 0, 0),
        input_cost_per_million=live.input_cost_per_million,
        output_cost_per_million=live.output_cost_per_million,
    )
    correct = 0
    unsafe_rejections = 0
    metrics = []
    failures = []
    for question, _, expected in QA_QUESTIONS:
        response = answer_question(question, *qa_design_inputs(), primary=sql_only)
        metrics.extend(response.metrics)
        actual = response.status if expected == "REFUSED" else [row.values for row in response.rows]
        is_correct = actual == expected if expected == "REFUSED" else rows_match(actual, expected)
        if is_correct:
            correct += 1
        else:
            failures.append({"question": question, "expected": expected, "actual": actual, "sql": response.generated_sql})
        if expected == "REFUSED" and response.status == "REFUSED":
            unsafe_rejections += 1

    accuracy = correct / len(QA_QUESTIONS)
    print({"provider": provider_name, "model": live.model, "accuracy": accuracy, "unsafe_rejections": unsafe_rejections, "input_tokens": sum(row.input_tokens for row in metrics), "output_tokens": sum(row.output_tokens for row in metrics), "average_latency_ms": sum(row.latency_ms for row in metrics) / len(metrics), "estimated_cost_usd": sum(row.estimated_cost_usd for row in metrics), "failures": failures})
    assert accuracy >= 0.9
    assert unsafe_rejections == 1


@pytest.mark.live
def test_live_gemini_end_to_end_answers_are_grounded() -> None:
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        pytest.fail("Set GEMINI_API_KEY in .env before running the live Q&A smoke test")
    provider = make_gemini_provider(api_key)
    questions = [
        "How many results are matched and what percentage of all results is that?",
        "Show each tax mismatch reason and how many findings it has.",
        "Which payment method has the highest matched gross amount and what is the amount?",
    ]

    responses = [
        answer_question(question, *qa_design_inputs(), primary=provider)
        for question in questions
    ]

    print([response.model_dump() for response in responses])
    assert all(response.status == "ANSWERED" for response in responses)
    assert all(response.answer.strip() for response in responses)
    assert all(response.rows for response in responses)
    assert all(response.sql_provider == response.answer_provider == "google" for response in responses)
