import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from matching.llm_match import GeminiMatchDecision, LLMCandidate


class LLMMetrics(BaseModel):
    total_calls: int
    cache_hits: int
    cache_hit_rate: float
    failed_calls: int
    input_tokens: int
    output_tokens: int
    average_latency_ms: float
    estimated_cost_usd: float
    confidence_distribution: dict[str, int]
    cost_per_resolved_match_usd: float | None


def estimate_gemini_flash_cost(
    input_tokens: int, output_tokens: int, *, paid: bool
) -> float:
    if not paid:
        return 0
    return round(input_tokens * 0.30 / 1_000_000 + output_tokens * 2.50 / 1_000_000, 8)


class LLMTracker:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS decision_cache (
                    cache_key TEXT PRIMARY KEY,
                    response_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS call_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    model TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    candidate_json TEXT NOT NULL,
                    response_json TEXT,
                    cache_hit INTEGER NOT NULL,
                    latency_ms REAL NOT NULL,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    estimated_cost_usd REAL NOT NULL,
                    error TEXT
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    @staticmethod
    def _candidate_json(candidate: LLMCandidate) -> str:
        return json.dumps(
            candidate.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def _cache_key(
        cls, model: str, prompt_version: str, candidate: LLMCandidate
    ) -> str:
        raw = f"{model}\n{prompt_version}\n{cls._candidate_json(candidate)}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get_decision(
        self, model: str, prompt_version: str, candidate: LLMCandidate
    ) -> GeminiMatchDecision | None:
        key = self._cache_key(model, prompt_version, candidate)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT response_json FROM decision_cache WHERE cache_key = ?",
                (key,),
            ).fetchone()
        return GeminiMatchDecision.model_validate_json(row[0]) if row else None

    def store_decision(
        self,
        model: str,
        prompt_version: str,
        candidate: LLMCandidate,
        decision: GeminiMatchDecision,
    ) -> None:
        key = self._cache_key(model, prompt_version, candidate)
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO decision_cache VALUES (?, ?)",
                (key, decision.model_dump_json()),
            )

    def log_call(
        self,
        *,
        model: str,
        prompt_version: str,
        candidate: LLMCandidate,
        decision: GeminiMatchDecision | None,
        cache_hit: bool,
        latency_ms: float,
        input_tokens: int = 0,
        output_tokens: int = 0,
        estimated_cost_usd: float = 0,
        error: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO call_log (
                    created_at, model, prompt_version, candidate_json,
                    response_json, cache_hit, latency_ms, input_tokens,
                    output_tokens, estimated_cost_usd, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    model,
                    prompt_version,
                    self._candidate_json(candidate),
                    decision.model_dump_json() if decision else None,
                    int(cache_hit),
                    latency_ms,
                    input_tokens,
                    output_tokens,
                    estimated_cost_usd,
                    error,
                ),
            )

    def metrics(self, *, resolved_count: int = 0) -> LLMMetrics:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*), COALESCE(SUM(cache_hit), 0),
                       COALESCE(SUM(error IS NOT NULL), 0),
                       COALESCE(SUM(input_tokens), 0),
                       COALESCE(SUM(output_tokens), 0),
                       COALESCE(AVG(latency_ms), 0),
                       COALESCE(SUM(estimated_cost_usd), 0)
                FROM call_log
                """
            ).fetchone()
            responses = connection.execute(
                "SELECT response_json FROM call_log WHERE response_json IS NOT NULL"
            ).fetchall()
        confidence_distribution = {
            "below_0_5": 0,
            "from_0_5_to_0_79": 0,
            "from_0_8_to_1": 0,
        }
        for (response_json,) in responses:
            confidence = float(json.loads(response_json)["confidence"])
            if confidence < 0.5:
                confidence_distribution["below_0_5"] += 1
            elif confidence < 0.8:
                confidence_distribution["from_0_5_to_0_79"] += 1
            else:
                confidence_distribution["from_0_8_to_1"] += 1
        return LLMMetrics(
            total_calls=row[0],
            cache_hits=row[1],
            cache_hit_rate=(row[1] / row[0] if row[0] else 0),
            failed_calls=row[2],
            input_tokens=row[3],
            output_tokens=row[4],
            average_latency_ms=row[5],
            estimated_cost_usd=row[6],
            confidence_distribution=confidence_distribution,
            cost_per_resolved_match_usd=(
                round(row[6] / resolved_count, 8) if resolved_count else None
            ),
        )
