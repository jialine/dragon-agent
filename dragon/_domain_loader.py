# -*- coding: utf-8 -*-
"""
Load compiled domain constants from .pyc only.
The source _domains.py must NOT exist — this enforces binary-only distribution.
"""
import importlib.util
import importlib.machinery
import os

_PYC_PATH = os.path.join(os.path.dirname(__file__), "__pycache__", "_domains.cpython-312.pyc")

if not os.path.exists(_PYC_PATH):
    # Try to find any _domains*.pyc
    cache_dir = os.path.join(os.path.dirname(__file__), "__pycache__")
    if os.path.isdir(cache_dir):
        candidates = [f for f in os.listdir(cache_dir) if f.startswith("_domains") and f.endswith(".pyc")]
        if candidates:
            _PYC_PATH = os.path.join(cache_dir, candidates[0])
    if not os.path.exists(_PYC_PATH):
        raise RuntimeError(
            "Domain constants not found. The _domains.pyc file is required. "
            "Reinstall Dragon Agent from the official source."
        )

_loader = importlib.machinery.SourcelessFileLoader("_domains", _PYC_PATH)
_spec = importlib.util.spec_from_loader("_domains", _loader)
_domains = importlib.util.module_from_spec(_spec)
_loader.exec_module(_domains)

API_BASE_URL = _domains.API_BASE_URL
OSS_BASE_URL = _domains.OSS_BASE_URL
OSS_FALLBACK_URL = _domains.OSS_FALLBACK_URL
OFFICIAL_DOMAINS = _domains.OFFICIAL_DOMAINS
