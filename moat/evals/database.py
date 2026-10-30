"""Immutable, profile-aware adapters for the two frozen SQLite snapshots."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from urllib.parse import quote

from .constants import COMMITTEE_MODEL, COMMITTEE_PROTOCOL_VERSION, PROFILE_COLUMNS
from .errors import PreflightError


class ImmutableDatabase:
    """A read-only SQLite connection opened with ``immutable=1``.

    The adapter never imports the production connection helper because that
    helper enables WAL and migrations, which are forbidden for eval snapshots.
    """

    def __init__(self, path: Path, profile: str):
        self.path = path.resolve()
        self.profile = profile
        self._conn: sqlite3.Connection | None = None

    def __enter__(self) -> "ImmutableDatabase":
        if self.profile not in PROFILE_COLUMNS:
            raise PreflightError("unknown database capability profile")
        if not self.path.is_file():
            raise PreflightError("database snapshot is missing")
        if self.path.name == "moat.db":
            raise PreflightError("live data/moat.db is forbidden as an eval input")
        if self.path.stat().st_mode & 0o222:
            raise PreflightError("database snapshot is not sealed read-only")
        sidecars = [Path(str(self.path) + suffix) for suffix in ("-wal", "-shm")]
        if any(sidecar.exists() for sidecar in sidecars):
            raise PreflightError("database snapshot has a WAL/SHM sidecar")
        uri = f"file:{quote(str(self.path), safe='/')}?mode=ro&immutable=1"
        try:
            self._conn = sqlite3.connect(uri, uri=True)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA query_only = ON")
            self._conn.execute("PRAGMA trusted_schema = OFF")
        except sqlite3.Error as exc:
            raise PreflightError("database snapshot could not be opened immutably") from exc
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("immutable database adapter is not open")
        return self._conn

    def validate_schema(self) -> None:
        expected = PROFILE_COLUMNS[self.profile]
        for table, columns in expected.items():
            actual = tuple(row["name"] for row in self.conn.execute(f'PRAGMA table_info("{table}")'))
            if actual != tuple(columns):
                raise PreflightError(f"database schema does not match {self.profile} at table {table}")

    def run_row(self, run_id: str):
        return self.conn.execute(
            "SELECT run_id, stage_reached, status FROM pipeline_runs WHERE run_id = ?", (run_id,)
        ).fetchone()

    def active_companies(self) -> list[dict]:
        return [dict(row) for row in self.conn.execute(
            "SELECT ticker, sector, currency FROM companies WHERE is_active = 1 ORDER BY ticker"
        )]

    def quality_rows(self, run_id: str) -> list[dict]:
        return [dict(row) for row in self.conn.execute(
            "SELECT q.ticker, c.sector, c.currency, q.passed_screen, q.composite_score, "
            "q.metrics_assessed, q.metrics_passed FROM quality_scores q "
            "JOIN companies c ON c.ticker = q.ticker WHERE q.run_id = ? ORDER BY q.ticker",
            (run_id,),
        )]

    def quant_rows(self, run_id: str) -> list[dict]:
        return [dict(row) for row in self.conn.execute(
            "SELECT ticker, metric, status, overall_pass FROM quant_scores WHERE run_id = ? ORDER BY ticker, metric",
            (run_id,),
        )]

    def valuation_count(self, run_id: str) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM valuations WHERE run_id = ?", (run_id,)).fetchone()[0]

    def committee_rows(self, run_id: str) -> list[dict]:
        return [dict(row) for row in self.conn.execute(
            "SELECT ticker, status, bear_case_severity, cache_key, reused_from_run_id, "
            "valuation_run_id, quality_run_id FROM committee_verdicts WHERE run_id = ? ORDER BY ticker",
            (run_id,),
        )]

    def committee_source(self, run_id: str, ticker: str):
        return self.conn.execute(
            "SELECT run_id, cache_key, reused_from_run_id FROM committee_verdicts WHERE run_id = ? AND ticker = ?",
            (run_id, ticker),
        ).fetchone()

    def can_recompute_committee_bundle(self) -> bool:
        required = {
            "ai_analysis": {"ticker", "analysis_type", "cache_key", "is_current"},
            "fundamentals_annual": {"ticker", "confidence"},
        }
        for table, columns in required.items():
            actual = {row["name"] for row in self.conn.execute(f'PRAGMA table_info("{table}")')}
            if not columns <= actual:
                return False
        return True

    def recompute_committee_bundle_key(self, ticker: str, valuation_run_id: str, quality_run_id: str) -> str | None:
        """Reproduce the profile-specific historical committee cache bundle."""
        ai_rows = self.conn.execute(
            "SELECT analysis_type, cache_key FROM ai_analysis WHERE ticker = ? AND is_current = 1 ORDER BY analysis_type",
            (ticker,),
        ).fetchall()
        if len(ai_rows) != 4 or len({row["analysis_type"] for row in ai_rows}) != 4:
            return None
        valuation_rows = [dict(row) for row in self.conn.execute(
            "SELECT method, scenario, intrinsic_value_low, intrinsic_value_high, current_price, "
            "margin_of_safety_pct, key_assumptions FROM valuations WHERE run_id = ? AND ticker = ?",
            (valuation_run_id, ticker),
        )]
        quant_rows = [dict(row) for row in self.conn.execute(
            "SELECT metric, value, status, sector_percentile, sector_peer_group FROM quant_scores "
            "WHERE run_id = ? AND ticker = ? ORDER BY metric",
            (quality_run_id, ticker),
        )]
        quality = self.conn.execute(
            "SELECT composite_score, metrics_assessed, metrics_passed FROM quality_scores WHERE run_id = ? AND ticker = ?",
            (quality_run_id, ticker),
        ).fetchone()
        company = self.conn.execute("SELECT name, sector FROM companies WHERE ticker = ?", (ticker,)).fetchone()
        if not valuation_rows or not quant_rows or quality is None or company is None:
            return None
        confidence_rows = self.conn.execute(
            "SELECT confidence FROM fundamentals_annual WHERE ticker = ?", (ticker,)
        ).fetchall()
        rank = {"low": 0, "medium": 1, "high": 2}
        confidences = [row["confidence"] for row in confidence_rows if row["confidence"]]
        data_confidence = min(confidences, key=lambda value: rank.get(value, -1)) if confidences else "low"
        valuation_summary = sorted(
            (row["method"], row["scenario"], row["intrinsic_value_low"], row["intrinsic_value_high"],
             row["current_price"], row["margin_of_safety_pct"], row["key_assumptions"])
            for row in valuation_rows
        )
        if self.profile == "candidate-v1":
            quant_summary = sorted(
                (row["metric"], row["value"], row["status"], row["sector_percentile"], row["sector_peer_group"])
                for row in quant_rows
            )
            company_summary = (company["name"], company["sector"])
        else:
            quant_summary = sorted(
                (row["metric"], row["value"], row["status"], row["sector_percentile"])
                for row in quant_rows
            )
            company_summary = None
        payload = {
            "ai_cache_keys": {row["analysis_type"]: row["cache_key"] for row in ai_rows},
            "valuation": valuation_summary,
            "quant": quant_summary,
            "quality": (quality["composite_score"], quality["metrics_assessed"], quality["metrics_passed"]),
            "data_confidence": data_confidence,
            "model_id": COMMITTEE_MODEL,
            "protocol_version": COMMITTEE_PROTOCOL_VERSION,
        }
        if self.profile == "candidate-v1":
            payload["company"] = company_summary
        encoded = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def fundamentals_history(self) -> dict[str, list[dict]]:
        rows = self.conn.execute(
            "SELECT * FROM fundamentals_annual ORDER BY ticker, fiscal_year"
        )
        out: dict[str, list[dict]] = {}
        for row in rows:
            out.setdefault(row["ticker"], []).append(dict(row))
        return out
