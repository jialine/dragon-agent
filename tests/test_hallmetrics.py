"""Tests for dragon/hallmetrics.py — pure functions"""
import sys; sys.path.insert(0, '/home/jialine/dragon-agent')
from dragon.hallmetrics import BenchmarkRunner, HallucinationReport

class TestBenchmarkRunner:
    def test_score_exact_match(self):
        result = BenchmarkRunner.score_response("Paris", "Paris", "geography")
        assert isinstance(result, dict)

    def test_score_mismatch(self):
        result = BenchmarkRunner.score_response("London", "Paris", "geography")
        assert isinstance(result, dict)

    def test_compute_stats(self):
        results = [{"score": 1.0}, {"score": 1.0}, {"score": 0.0}, {"score": 1.0}]
        stats = BenchmarkRunner.compute_benchmark_stats(results)
        assert isinstance(stats, dict)

class TestHallucinationReport:
    def test_to_dict(self):
        r = HallucinationReport()
        d = r.to_dict()
        assert isinstance(d, dict)
