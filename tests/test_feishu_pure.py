"""Tests for dragon/feishu.py — pure functions"""
import sys; sys.path.insert(0, '/home/jialine/dragon-agent')
import pytest
from dragon.feishu import handle_url_verification, verify_hmac_signature

class TestHandleUrlVerification:
    def test_valid_challenge(self):
        body = {"challenge": "test123", "token": "abc", "type": "url_verification"}
        result = handle_url_verification(body)
        assert result["challenge"] == "test123"

    def test_no_challenge(self):
        result = handle_url_verification({"type": "other"})
        assert "challenge" not in result

class TestVerifyHmacSignature:
    def test_valid_signature(self):
        import hmac, hashlib
        secret = "test-secret"
        timestamp = "1234567890"
        nonce = "abc123"
        body = b'{"test": true}'
        payload = f"{timestamp}{nonce}".encode() + body
        expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        assert verify_hmac_signature(secret, timestamp, nonce, body, expected) is True

    def test_invalid_signature(self):
        assert verify_hmac_signature("secret", "123", "abc", b"body", "badsig") is False

    def test_empty_secret(self):
        result = verify_hmac_signature("", "1", "2", b"x", "y")
        assert isinstance(result, bool)
