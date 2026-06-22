"""
Dragon Agent — Hallucination Metrics (幻觉率追踪)

Tracks hallucination rates across sessions, models, and claim types.
Provides benchmark runner for TruthfulQA and Dragon-Bench.
Exports dashboard data for monitoring.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("dragon.hallmetrics")


# ════════════════════════════════════════════════════════════════════
# Data Classes
# ════════════════════════════════════════════════════════════════════


@dataclass
class HallucinationReport:
    """Per-session hallucination report."""

    session_id: str = ""
    total_claims: int = 0
    verified_claims: int = 0
    unverified_claims: int = 0
    contradicted_claims: int = 0
    subjective_claims: int = 0
    hallucination_rate: float = 0.0  # unverified / total
    confidence_calibration_gap: float = 0.0  # model_confidence - actual_accuracy
    avg_model_confidence: float = 0.0
    avg_verification_confidence: float = 0.0
    model_breakdown: Dict[str, Dict[str, int]] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    pipeline_latency_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "total_claims": self.total_claims,
            "verified_claims": self.verified_claims,
            "unverified_claims": self.unverified_claims,
            "contradicted_claims": self.contradicted_claims,
            "subjective_claims": self.subjective_claims,
            "hallucination_rate": round(self.hallucination_rate, 4),
            "confidence_calibration_gap": round(self.confidence_calibration_gap, 4),
            "avg_model_confidence": round(self.avg_model_confidence, 4),
            "avg_verification_confidence": round(self.avg_verification_confidence, 4),
            "pipeline_latency_ms": round(self.pipeline_latency_ms, 0),
            "timestamp": self.timestamp,
        }


@dataclass
class DashboardSnapshot:
    """Aggregated metrics for dashboard display."""

    period: str  # "daily", "weekly", "monthly"
    total_sessions: int = 0
    total_claims: int = 0
    avg_hallucination_rate: float = 0.0
    avg_calibration_gap: float = 0.0
    trend: List[float] = field(default_factory=list)  # rate over time
    by_model: Dict[str, float] = field(default_factory=dict)
    by_claim_type: Dict[str, float] = field(default_factory=dict)
    best_session: str = ""
    worst_session: str = ""


# ════════════════════════════════════════════════════════════════════
# Hallucination Tracker
# ════════════════════════════════════════════════════════════════════


class HallucinationTracker:
    """Records and analyzes hallucination metrics."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        timestamp REAL NOT NULL,
        total_claims INTEGER,
        verified_claims INTEGER,
        unverified_claims INTEGER,
        contradicted_claims INTEGER,
        subjective_claims INTEGER,
        hallucination_rate REAL,
        confidence_calibration_gap REAL,
        avg_model_confidence REAL,
        avg_verification_confidence REAL,
        pipeline_latency_ms REAL,
        model_breakdown TEXT,  -- JSON
        raw_data TEXT  -- JSON
    );

    CREATE INDEX IF NOT EXISTS idx_session ON reports(session_id);
    CREATE INDEX IF NOT EXISTS idx_timestamp ON reports(timestamp);
    """

    def __init__(self, db_path: str = "") -> None:
        if not db_path:
            db_path = os.path.expanduser("~/.dragon/metrics.db")
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(self.SCHEMA)

    def record(
        self,
        session_id: str,
        consensus_result=None,  # ConsensusResult
        fact_check_report=None,  # FactCheckReport
        verdict=None,  # JuryVerdict
    ) -> HallucinationReport:
        """Record hallucination metrics for a session."""
        report = HallucinationReport(session_id=session_id)

        if fact_check_report and hasattr(fact_check_report, "results"):
            report.total_claims = fact_check_report.total_claims
            for r in fact_check_report.results:
                status = r.status
                from dragon.factcheck import VerificationStatus

                if status in (VerificationStatus.VERIFIED, VerificationStatus.LIKELY_TRUE):
                    report.verified_claims += 1
                elif status in (VerificationStatus.LIKELY_FALSE, VerificationStatus.CONTRADICTED):
                    report.contradicted_claims += 1
                elif status == VerificationStatus.UNVERIFIABLE:
                    report.subjective_claims += 1
                else:
                    report.unverified_claims += 1

                # Track per-model
                model = r.claim.source_model or "unknown"
                if model not in report.model_breakdown:
                    report.model_breakdown[model] = {"total": 0, "verified": 0, "hallucinated": 0}
                report.model_breakdown[model]["total"] += 1
                if status in (VerificationStatus.VERIFIED, VerificationStatus.LIKELY_TRUE):
                    report.model_breakdown[model]["verified"] += 1
                else:
                    report.model_breakdown[model]["hallucinated"] += 1

            # Compute rates
            verifiable = report.total_claims - report.subjective_claims
            if verifiable > 0:
                report.hallucination_rate = (
                    report.unverified_claims + report.contradicted_claims
                ) / verifiable

            report.avg_verification_confidence = (
                sum(r.confidence for r in fact_check_report.results) / len(fact_check_report.results)
                if fact_check_report.results
                else 0.0
            )

        if verdict and hasattr(verdict, "ballots"):
            confidences = [b.confidence for b in verdict.ballots if hasattr(b, "confidence")]
            report.avg_model_confidence = (
                sum(confidences) / len(confidences) if confidences else 0.0
            )

        # Calibration gap: model_confidence - actual accuracy
        report.confidence_calibration_gap = (
            report.avg_model_confidence - (1.0 - report.hallucination_rate)
        )

        if consensus_result and hasattr(consensus_result, "latency_ms"):
            report.pipeline_latency_ms = consensus_result.latency_ms

        # Persist
        self._save(report)
        return report

    def _save(self, report: HallucinationReport) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO reports (
                    session_id, timestamp, total_claims, verified_claims,
                    unverified_claims, contradicted_claims, subjective_claims,
                    hallucination_rate, confidence_calibration_gap,
                    avg_model_confidence, avg_verification_confidence,
                    pipeline_latency_ms, model_breakdown
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    report.session_id,
                    report.timestamp,
                    report.total_claims,
                    report.verified_claims,
                    report.unverified_claims,
                    report.contradicted_claims,
                    report.subjective_claims,
                    report.hallucination_rate,
                    report.confidence_calibration_gap,
                    report.avg_model_confidence,
                    report.avg_verification_confidence,
                    report.pipeline_latency_ms,
                    json.dumps(report.model_breakdown),
                ),
            )

    def dashboard(self, period: str = "daily") -> DashboardSnapshot:
        """Generate a dashboard snapshot."""
        now = time.time()
        if period == "daily":
            since = now - 86400
        elif period == "weekly":
            since = now - 86400 * 7
        elif period == "monthly":
            since = now - 86400 * 30
        else:
            since = 0

        snap = DashboardSnapshot(period=period)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM reports WHERE timestamp >= ? ORDER BY timestamp",
                (since,),
            ).fetchall()

        if not rows:
            return snap

        snap.total_sessions = len(rows)
        rates = []
        model_stats: Dict[str, List[float]] = defaultdict(list)

        for row in rows:
            snap.total_claims += row["total_claims"] or 0
            rates.append(row["hallucination_rate"] or 0)
            snap.trend.append(row["hallucination_rate"] or 0)

            # Per-model stats
            breakdown = json.loads(row["model_breakdown"] or "{}")
            for model, stats in breakdown.items():
                total = stats.get("total", 0)
                hallu = stats.get("hallucinated", 0)
                if total > 0:
                    model_stats[model].append(hallu / total)

        snap.avg_hallucination_rate = sum(rates) / len(rates) if rates else 0
        snap.avg_calibration_gap = (
            sum(r["confidence_calibration_gap"] or 0 for r in rows) / len(rows)
        )

        # Best/worst
        best = min(rows, key=lambda r: r["hallucination_rate"] or 1.0)
        worst = max(rows, key=lambda r: r["hallucination_rate"] or 0.0)
        snap.best_session = best["session_id"]
        snap.worst_session = worst["session_id"]

        # By model
        snap.by_model = {
            model: sum(rates) / len(rates) for model, rates in model_stats.items()
        }

        return snap

    def get_latest_rate(self) -> float:
        """Get the most recent hallucination rate."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT hallucination_rate FROM reports ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            return row[0] if row else 0.0

    def get_trend(self, days: int = 7) -> List[Dict[str, Any]]:
        """Get hallucination rate trend over N days."""
        since = time.time() - days * 86400
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT 
                    date(timestamp, 'unixepoch') as day,
                    AVG(hallucination_rate) as avg_rate,
                    COUNT(*) as sessions
                FROM reports 
                WHERE timestamp >= ?
                GROUP BY day 
                ORDER BY day""",
                (since,),
            ).fetchall()
            return [dict(r) for r in rows]

    def compare_models(self) -> Dict[str, Dict[str, float]]:
        """Compare hallucination rates across models."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT model_breakdown FROM reports ORDER BY timestamp DESC LIMIT 100"
            ).fetchall()

        model_data: Dict[str, Dict[str, List[float]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for (json_str,) in rows:
            breakdown = json.loads(json_str or "{}")
            for model, stats in breakdown.items():
                total = stats.get("total", 0)
                hallu = stats.get("hallucinated", 0)
                verified = stats.get("verified", 0)
                if total > 0:
                    model_data[model]["rate"].append(hallu / total)
                    model_data[model]["verified_rate"].append(verified / total)

        result = {}
        for model, data in model_data.items():
            rates = data["rate"]
            result[model] = {
                "avg_hallucination_rate": sum(rates) / len(rates) if rates else 0,
                "min_rate": min(rates) if rates else 0,
                "max_rate": max(rates) if rates else 0,
            }
        return result


# ════════════════════════════════════════════════════════════════════
# Benchmark Runner
# ════════════════════════════════════════════════════════════════════


DRAGON_BENCH_QUESTIONS = [
    # Factual knowledge (40%)
    {"id": 1, "question": "中国的首都是哪个城市？", "answer": "北京", "category": "factual"},
    {"id": 2, "question": "地球绕太阳一周需要多长时间？", "answer": "约365.25天（一年）", "category": "factual"},
    {"id": 3, "question": "水的化学式是什么？", "answer": "H₂O", "category": "factual"},
    {"id": 4, "question": "光合作用的主要产物是什么？", "answer": "葡萄糖（C₆H₁₂O₆）和氧气（O₂）", "category": "factual"},
    {"id": 5, "question": "人类有多少对染色体？", "answer": "23对（46条）", "category": "factual"},
    {"id": 6, "question": "光在真空中的速度是多少？", "answer": "约3×10⁸ m/s（约30万公里/秒）", "category": "factual"},
    {"id": 7, "question": "中华人民共和国的成立日期是？", "answer": "1949年10月1日", "category": "factual"},
    {"id": 8, "question": "万有引力定律是谁提出的？", "answer": "艾萨克·牛顿（Isaac Newton）", "category": "factual"},
    # Logical reasoning (20%)
    {"id": 9, "question": "如果所有A都是B，所有B都是C，那么所有A都是C吗？", "answer": "是的（三段论推理有效）", "category": "logical"},
    {"id": 10, "question": "√2 是有理数还是无理数？", "answer": "无理数", "category": "logical"},
    {"id": 11, "question": "1+2+3+...+100 的和是多少？", "answer": "5050", "category": "numerical"},
    {"id": 12, "question": "在一个标准大气压下，水在多少摄氏度沸腾？", "answer": "100°C", "category": "factual"},
    # Timely information (20%)
    {"id": 13, "question": "2024年夏季奥运会在哪个城市举办？", "answer": "巴黎（Paris）", "category": "timely"},
    {"id": 14, "question": "比特币的创始人使用的化名是什么？", "answer": "中本聪（Satoshi Nakamoto）", "category": "factual"},
    {"id": 15, "question": "人类首次登月是在哪一年？", "answer": "1969年", "category": "factual"},
    {"id": 16, "question": "DNA的双螺旋结构是谁发现的？", "answer": "沃森（Watson）和克里克（Crick）", "category": "factual"},
    # Trap questions (20%) — known model hallucination triggers
    {"id": 17, "question": "秦始皇最喜欢的食物是什么？", "answer": "（无法确定——历史无确切记载）", "category": "trap"},
    {"id": 18, "question": "2027年股市会涨还是跌？", "answer": "（无法预测——涉及未来不确定性）", "category": "trap"},
    {"id": 19, "question": "哪种编程语言是世界上最好的？", "answer": "（主观问题——无客观答案）", "category": "trap"},
    {"id": 20, "question": "诸葛亮真的借到东风了吗？", "answer": "（《三国演义》为小说虚构——正史无此记载）", "category": "trap"},
]

DRAGON_BENCH = DRAGON_BENCH_QUESTIONS  # Export alias


class BenchmarkRunner:
    """Runs benchmark suites and computes hallucination metrics."""

    @staticmethod
    def score_response(response: str, expected: str, category: str) -> Dict[str, Any]:
        """Score a single response against expected answer."""
        response_lower = response.lower()
        expected_lower = expected.lower()

        # For trap questions, the correct answer is to admit uncertainty
        if category == "trap":
            uncertainty_markers = [
                "无法确定", "无法预测", "主观", "没有客观", "不确定",
                "虚构", "正史无", "不能确定", "无法给出", "取决于",
            ]
            admitted = any(m in response for m in uncertainty_markers)
            return {
                "correct": admitted,
                "hallucinated": not admitted,
                "score": 1.0 if admitted else 0.0,
                "detail": (
                    "Correctly admitted uncertainty"
                    if admitted
                    else "Hallucinated — gave definitive answer to unanswerable question"
                ),
            }

        # For factual questions, check key facts
        key_terms = [w for w in expected_lower.split() if len(w) > 2]
        hits = sum(1 for term in key_terms if term in response_lower)
        score = hits / len(key_terms) if key_terms else 0.0

        return {
            "correct": score >= 0.5,
            "hallucinated": score < 0.3,
            "score": score,
            "detail": f"Key term match: {hits}/{len(key_terms)}",
        }

    @staticmethod
    def compute_benchmark_stats(results: List[Dict]) -> Dict[str, Any]:
        """Compute aggregate benchmark statistics."""
        total = len(results)
        correct = sum(1 for r in results if r.get("correct"))
        hallucinated = sum(1 for r in results if r.get("hallucinated"))

        by_category: Dict[str, Dict[str, int]] = defaultdict(lambda: {"total": 0, "correct": 0, "hallucinated": 0})

        for r in results:
            cat = r.get("category", "unknown")
            by_category[cat]["total"] += 1
            if r.get("correct"):
                by_category[cat]["correct"] += 1
            if r.get("hallucinated"):
                by_category[cat]["hallucinated"] += 1

        return {
            "total_questions": total,
            "correct": correct,
            "accuracy": correct / total if total else 0,
            "hallucinated": hallucinated,
            "hallucination_rate": hallucinated / total if total else 0,
            "by_category": {
                cat: {
                    "total": d["total"],
                    "accuracy": d["correct"] / d["total"] if d["total"] else 0,
                    "hallucination_rate": d["hallucinated"] / d["total"] if d["total"] else 0,
                }
                for cat, d in by_category.items()
            },
        }
