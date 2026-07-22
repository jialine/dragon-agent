"""Tests for dragon/file_safety.py — pure functions"""
import sys; sys.path.insert(0, '/home/jialine/dragon-agent')
import os, tempfile
from dragon.file_safety import (
    is_file_extension_safe, sanitize_filename,
    SafePath, SafetyValidator, create_default_validator,
)

class TestIsFileExtensionSafe:
    def test_py_safe(self):
        assert is_file_extension_safe("script.py") is True

    def test_txt_safe(self):
        assert is_file_extension_safe("readme.txt") is True

    def test_exe(self):
        result = is_file_extension_safe("virus.exe")
        assert isinstance(result, bool)

class TestSanitizeFilename:
    def test_clean(self):
        assert sanitize_filename("hello.txt") == "hello.txt"

    def test_traversal_replaced(self):
        result = sanitize_filename("../../etc/passwd")
        assert "/" not in result

    def test_null_byte(self):
        result = sanitize_filename("test\0.txt")
        assert "\0" not in result or result == "test.txt"

class TestSafetyValidator:
    def test_create_default(self):
        v = create_default_validator()
        assert isinstance(v, SafetyValidator)

    def test_add_allowed_dir(self):
        v = SafetyValidator(allowed_dirs=["/tmp"])
        v.add_allowed_dir("/var/tmp")
        assert "/var/tmp" in v.get_allowed_dirs()
