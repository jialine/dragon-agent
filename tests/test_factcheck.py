"""Tests for dragon/factcheck.py — pure functions"""
import sys; sys.path.insert(0, '/home/jialine/dragon-agent')
from dragon.factcheck import ClaimExtractor, FactClaim, ClaimType, VerificationStatus

class TestClaimExtractor:
    def test_extract_claims(self):
        claims = ClaimExtractor.extract("The Earth is round. Water boils at 100C.")
        assert isinstance(claims, list)

    def test_extract_empty(self):
        claims = ClaimExtractor.extract("")
        assert isinstance(claims, list)

class TestFactClaim:
    def test_creation(self):
        fc = FactClaim(text="Paris is the capital of France", claim_type=ClaimType.FACTUAL)
        assert fc.text == "Paris is the capital of France"
        assert fc.claim_type == ClaimType.FACTUAL

class TestClaimType:
    def test_has_values(self):
        assert len(list(ClaimType)) > 0

class TestVerificationStatus:
    def test_has_values(self):
        assert len(list(VerificationStatus)) > 0
