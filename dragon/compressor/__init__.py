"""
Dragon Context Compressor — Deep token compression for long conversations.

Subpackages:
    estimator    — Token estimation via tiktoken or heuristic fallback.
    compressor   — Multi-strategy compression with feedback loop & stats.
"""

from dragon.compressor.compressor import (
    ContextCompressor,
    CompressedContext,
    CompressorStats,
    CompressionStrategy,
)
from dragon.compressor.estimator import TokenEstimator

__all__ = [
    "ContextCompressor",
    "CompressedContext",
    "CompressorStats",
    "CompressionStrategy",
    "TokenEstimator",
]
