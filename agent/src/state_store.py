"""Small local state store for daily runs, history and human feedback.

SQLite is used for the first executable implementation so the workflow works
without provisioning PostgreSQL. The schema mirrors the V3 business objects
and can later be migrated to the existing PostgreSQL schema without changing
the pipeline contract.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path))
        self.connection.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS daily_runs (
                run_id TEXT PRIMARY KEY,
                run_key TEXT NOT NULL UNIQUE,
                account_id TEXT,
                data_date TEXT NOT NULL,
                pipeline_version TEXT NOT NULL,
                status TEXT NOT NULL,
                data_completeness TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                error_summary TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS entity_daily_metrics (
                object_type TEXT NOT NULL,
                object_id TEXT NOT NULL,
                data_date TEXT NOT NULL,
                run_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (object_type, object_id, data_date),
                FOREIGN KEY (run_id) REFERENCES daily_runs(run_id)
            );
            CREATE TABLE IF NOT EXISTS recommendations (
                recommendation_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                object_type TEXT NOT NULL,
                object_id TEXT NOT NULL,
                priority TEXT NOT NULL,
                action TEXT NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES daily_runs(run_id)
            );
            CREATE TABLE IF NOT EXISTS recommendation_feedback (
                feedback_id TEXT PRIMARY KEY,
                recommendation_id TEXT NOT NULL,
                execution_status TEXT NOT NULL,
                actual_action TEXT,
                actual_value TEXT,
                operator TEXT,
                observed_result_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY (recommendation_id) REFERENCES recommendations(recommendation_id)
            );
            """
        )
        self.connection.commit()

    def begin_run(self, run_key: str, data_date: str, account_id: str | None, pipeline_version: str) -> tuple[str, bool, str]:
        existing = self.connection.execute(
            "SELECT run_id, status FROM daily_runs WHERE run_key = ?", (run_key,)
        ).fetchone()
        if existing:
            if existing['status'] != 'success':
                self.connection.execute(
                    "UPDATE daily_runs SET status='running', started_at=?, finished_at=NULL, error_summary=NULL WHERE run_id=?",
                    (datetime.now(timezone.utc).isoformat(), existing['run_id']),
                )
                self.connection.commit()
            return str(existing["run_id"]), existing["status"] == "success", str(existing["status"])
        run_id = "run_" + uuid.uuid4().hex[:16]
        self.connection.execute(
            """INSERT INTO daily_runs
            (run_id, run_key, account_id, data_date, pipeline_version, status, started_at)
            VALUES (?, ?, ?, ?, ?, 'running', ?)""",
            (run_id, run_key, account_id, data_date, pipeline_version, datetime.now(timezone.utc).isoformat()),
        )
        self.connection.commit()
        return run_id, False, "running"

    def finish_run(self, run_id: str, status: str, data_completeness: str | None = None,
                   error_summary: str | None = None, metadata: dict[str, Any] | None = None) -> None:
        self.connection.execute(
            """UPDATE daily_runs SET status = ?, data_completeness = ?, finished_at = ?,
            error_summary = ?, metadata_json = ? WHERE run_id = ?""",
            (status, data_completeness, datetime.now(timezone.utc).isoformat(), error_summary,
             json.dumps(metadata or {}, ensure_ascii=False), run_id),
        )
        self.connection.commit()

    def save_report(self, run_id: str, report: dict[str, Any]) -> None:
        data_date = str(report["data_date"])
        rows: list[tuple[str, str, str, str, str]] = []
        yesterday = report.get("yesterday")
        if yesterday:
            rows.append(("account", "account", data_date, run_id, json.dumps(yesterday, ensure_ascii=False)))
        for object_type, items, id_key in (
            ("plan", report.get("daily_plan_metrics") or report.get("plan_summary", []), "unit_id"),
            ("material", report.get("daily_material_metrics") or report.get("material_summary", []), "material_id"),
        ):
            name_key = "unit_name" if object_type == "plan" else "material_name"
            summaries = report.get("plan_summary" if object_type == "plan" else "material_summary", [])
            names = {str(item.get(id_key)): item.get(name_key) for item in summaries}
            for item in items:
                item = {**item, name_key: item.get(name_key) or names.get(str(item.get(id_key)))}
                object_id = str(item.get(id_key) or "unknown")
                item_date = str(item.get("data_date") or data_date)
                rows.append((object_type, object_id, item_date, run_id, json.dumps(item, ensure_ascii=False)))
        self.connection.executemany(
            """INSERT INTO entity_daily_metrics
            (object_type, object_id, data_date, run_id, payload_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(object_type, object_id, data_date) DO UPDATE SET
                run_id = excluded.run_id, payload_json = excluded.payload_json""",
            rows,
        )
        self.connection.commit()

    def load_history(self, account_id: str, before_date: str) -> dict[str, list[dict[str, Any]]]:
        """Read successful runs for this account only, strictly before the current day.

        This never discovers files or imports a replay directory. The current
        report is merged in memory, so retries cannot duplicate a day's totals.
        """
        rows = self.connection.execute(
            """SELECT m.object_type, m.object_id, m.data_date, m.payload_json, r.metadata_json
            FROM entity_daily_metrics m JOIN daily_runs r ON r.run_id = m.run_id
            WHERE r.account_id = ? AND r.status = 'success' AND m.data_date < ?
            ORDER BY m.data_date, m.object_id""", (account_id, before_date),
        ).fetchall()
        result: dict[str, list[dict[str, Any]]] = {"account": [], "plan": [], "material": []}
        for row in rows:
            if json.loads(row["metadata_json"]).get("reconciliation_pass") is False:
                continue
            if row["object_type"] in result:
                item = json.loads(row["payload_json"])
                item["data_date"] = row["data_date"]
                result[row["object_type"]].append(item)
        return result

    def save_recommendations(self, run_id: str, recommendations: list[dict[str, Any]]) -> None:
        self.connection.executemany(
            """INSERT OR REPLACE INTO recommendations
            (recommendation_id, run_id, object_type, object_id, priority, action, status, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    item["recommendation_id"], run_id, str(item.get("object_type") or "unknown"),
                    str(item.get("object_id") or "unknown"), item["priority"], item["action"],
                    item["status"], json.dumps(item, ensure_ascii=False),
                )
                for item in recommendations
            ],
        )
        self.connection.commit()

    def record_feedback(self, recommendation_id: str, execution_status: str,
                        actual_action: str | None = None, actual_value: str | None = None,
                        operator: str | None = None, observed_result: dict[str, Any] | None = None) -> str:
        feedback_id = "feedback_" + uuid.uuid4().hex[:16]
        self.connection.execute(
            """INSERT INTO recommendation_feedback
            (feedback_id, recommendation_id, execution_status, actual_action, actual_value,
             operator, observed_result_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (feedback_id, recommendation_id, execution_status, actual_action, actual_value,
            operator, json.dumps(observed_result or {}, ensure_ascii=False, sort_keys=True), datetime.now(timezone.utc).isoformat()),
        )
        self.connection.commit()
        return feedback_id

    def record_feedback_if_new(self, recommendation_id: str, execution_status: str,
                               actual_action: str | None = None, actual_value: str | None = None,
                               operator: str | None = None,
                               observed_result: dict[str, Any] | None = None) -> str | None:
        """Persist a Shimo feedback snapshot once; safe on rerun of the same day."""
        observed_json = json.dumps(observed_result or {}, ensure_ascii=False, sort_keys=True)
        existing = self.connection.execute(
            """SELECT feedback_id FROM recommendation_feedback
            WHERE recommendation_id = ? AND execution_status = ?
              AND COALESCE(actual_action, '') = COALESCE(?, '')
              AND COALESCE(actual_value, '') = COALESCE(?, '')
              AND COALESCE(operator, '') = COALESCE(?, '')
              AND observed_result_json = ? LIMIT 1""",
            (recommendation_id, execution_status, actual_action, actual_value, operator, observed_json),
        ).fetchone()
        if existing:
            return None
        return self.record_feedback(
            recommendation_id, execution_status, actual_action, actual_value, operator,
            observed_result,
        )

    def list_feedback(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """SELECT feedback_id, recommendation_id, execution_status, actual_action,
            actual_value, operator, observed_result_json, created_at
            FROM recommendation_feedback ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["observed_result"] = json.loads(item.pop("observed_result_json") or "{}")
            result.append(item)
        return result

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "StateStore":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
