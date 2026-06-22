"""
Dragon Agent — Confidence Calibration (置信度校准)

Calibrates model self-reported confidence scores to match actual accuracy.

Algorithm:
  - Platt Scaling (sigmoid): sample < 1000 points
  - Isotonic Regression: sample >= 1000 points
  - Expected Calibration Error (ECE)

Integrates with FactChecker — each verification result feeds calibration data.
ConsensusBuilder consumes calibrated confidence values.

References:
  - Guo et al. "On Calibration of Modern Neural Networks" (2017)
  - Platt, "Probabilistic Outputs for SVMs" (1999)
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger("dragon.confidence")


# ════════════════════════════════════════════════════════════════════
# Data Classes
# ════════════════════════════════════════════════════════════════════


@dataclass
class CalibrationPoint:
    """A single data point for calibration: model confidence + actual outcome."""

    model_confidence: float       # 0.0 - 1.0
    actual_correct: bool          # Whether the claim was verified correct
    claim_text: str = ""
    model_name: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class CalibrationResult:
    """Result of calibrating a single confidence score."""

    raw_confidence: float
    calibrated: float
    method: str                   # "platt" | "isotonic" | "none"
    expected_accuracy: float      # Estimated real accuracy at this confidence
    calibration_gap: float        # raw - expected_accuracy
    sample_count: int             # Number of calibration samples used


@dataclass
class CalibrationStats:
    """Overall calibration statistics."""

    total_samples: int
    method: str
    ece: float                    # Expected Calibration Error (lower is better)
    mce: float                    # Maximum Calibration Error
    avg_gap: float                # Average raw - expected gap
    platt_a: float | None = None  # Platt scaling parameter A
    platt_b: float | None = None  # Platt scaling parameter B
    bins: list[dict] = field(default_factory=list)  # ECE bin details


# ════════════════════════════════════════════════════════════════════
# Confidence Calibrator
# ════════════════════════════════════════════════════════════════════


class ConfidenceCalibrator:
    """
    Calibrates model confidence scores using Platt Scaling or
    Isotonic Regression.

    Usage::

        calibrator = ConfidenceCalibrator()

        # Feed calibration data over time
        calibrator.record(model_confidence=0.95, actual_correct=True, ...)

        # When enough data, fit the model
        calibrator.fit()

        # Calibrate a new confidence score
        result = calibrator.calibrate(0.92)
        print(result.calibrated)  # e.g., 0.84

    Cold-start: returns raw confidence until 100 samples accumulated.
    """

    # Thresholds
    MIN_SAMPLES_PLATT = 100       # Minimum samples for Platt scaling
    MIN_SAMPLES_ISOTONIC = 1000   # Minimum samples for isotonic regression
    ECE_NUM_BINS = 10             # Bins for ECE calculation

    def __init__(self, model_path: str = "~/.dragon/calibration/"):
        self._model_path = Path(os.path.expanduser(model_path))
        self._model_path.mkdir(parents=True, exist_ok=True)

        # Calibration data
        self._points: list[CalibrationPoint] = []
        self._fitted: bool = False
        self._method: str = "none"

        # Platt scaling parameters (sigmoid: 1/(1+exp(-(A*logit(c) + B))))
        self._platt_a: float | None = None
        self._platt_b: float | None = None

        # Isotonic regression model (from sklearn)
        self._isotonic_model = None

        # Load existing model if available
        self.load()

    # ── Data Recording ──────────────────────────────────────────────

    def record(
        self,
        model_confidence: float,
        actual_correct: bool,
        claim_text: str = "",
        model_name: str = "",
    ) -> None:
        """
        Record one calibration data point.

        Call this after each FactChecker verification.
        Accumulates data over time for periodic re-fitting.

        Args:
            model_confidence: Self-reported confidence from the model (0.0-1.0).
            actual_correct: Whether the claim was verified as correct.
            claim_text: The text of the claim (for audit trail).
            model_name: Which model generated the claim.
        """
        model_confidence = max(0.0, min(1.0, model_confidence))
        point = CalibrationPoint(
            model_confidence=model_confidence,
            actual_correct=actual_correct,
            claim_text=claim_text,
            model_name=model_name,
        )
        self._points.append(point)
        self._fitted = False  # Need re-fit

        logger.debug(
            "Calibration point recorded: conf=%.3f correct=%s (total=%d)",
            model_confidence, actual_correct, len(self._points),
        )

    # ── Fitting ─────────────────────────────────────────────────────

    def fit(self, method: str = "auto") -> None:
        """
        Fit the calibration model on accumulated data.

        Args:
            method: "auto" (choose based on sample count),
                    "platt", "isotonic", or "none".
        """
        n = len(self._points)
        if n == 0:
            logger.warning("No calibration data — fit skipped")
            return

        if method == "auto":
            if n >= self.MIN_SAMPLES_ISOTONIC:
                method = "isotonic"
            elif n >= self.MIN_SAMPLES_PLATT:
                method = "platt"
            else:
                logger.info(
                    "Insufficient samples for calibration (%d < %d) — using raw confidence",
                    n, self.MIN_SAMPLES_PLATT,
                )
                self._method = "none"
                self._fitted = True
                return

        if method == "platt":
            self._fit_platt()
        elif method == "isotonic":
            self._fit_isotonic()
        elif method == "none":
            pass
        else:
            raise ValueError(f"Unknown calibration method: {method}")

        self._method = method
        self._fitted = True
        self.save()

        stats = self.stats()
        logger.info(
            "Calibrator fitted: method=%s samples=%d ECE=%.4f",
            self._method, n, stats.ece,
        )

    def _fit_platt(self) -> None:
        """Fit Platt Scaling (logistic regression on logit-transformed confidence)."""
        from sklearn.linear_model import LogisticRegression

        confidences = np.array([p.model_confidence for p in self._points])
        labels = np.array([1 if p.actual_correct else 0 for p in self._points])

        # Clip to avoid log(0) / log(1)
        eps = 1e-12
        confidences = np.clip(confidences, eps, 1.0 - eps)

        # Logit transform
        logits = np.log(confidences / (1.0 - confidences)).reshape(-1, 1)

        # Fit logistic regression
        lr = LogisticRegression(solver="lbfgs")
        lr.fit(logits, labels)

        self._platt_a = float(lr.coef_[0][0])  # type: ignore[index]
        self._platt_b = float(lr.intercept_[0])  # type: ignore[index]
        self._isotonic_model = None

        logger.debug("Platt fitted: A=%.4f B=%.4f", self._platt_a, self._platt_b)

    def _fit_isotonic(self) -> None:
        """Fit Isotonic Regression (non-parametric, monotonic)."""
        from sklearn.isotonic import IsotonicRegression

        confidences = np.array([p.model_confidence for p in self._points])
        labels = np.array([1.0 if p.actual_correct else 0.0 for p in self._points])

        self._isotonic_model = IsotonicRegression(
            out_of_bounds="clip",
            increasing=True,
        )
        self._isotonic_model.fit(confidences, labels)
        self._platt_a = None
        self._platt_b = None

        logger.debug("Isotonic regression fitted (n=%d)", len(self._points))

    # ── Calibration ────────────────────────────────────────────────

    def calibrate(self, raw_confidence: float) -> CalibrationResult:
        """
        Calibrate a model's self-reported confidence score.

        Args:
            raw_confidence: Model-reported confidence (0.0-1.0).

        Returns:
            CalibrationResult with calibrated confidence and metadata.
        """
        raw_confidence = max(0.0, min(1.0, raw_confidence))

        if not self._fitted or self._method == "none":
            return CalibrationResult(
                raw_confidence=raw_confidence,
                calibrated=raw_confidence,
                method="none",
                expected_accuracy=raw_confidence,
                calibration_gap=0.0,
                sample_count=len(self._points),
            )

        if self._method == "platt" and self._platt_a is not None:
            calibrated, expected_acc = self._calibrate_platt(raw_confidence)
        elif self._method == "isotonic" and self._isotonic_model is not None:
            calibrated, expected_acc = self._calibrate_isotonic(raw_confidence)
        else:
            calibrated, expected_acc = raw_confidence, raw_confidence

        return CalibrationResult(
            raw_confidence=raw_confidence,
            calibrated=calibrated,
            method=self._method,
            expected_accuracy=expected_acc,
            calibration_gap=raw_confidence - expected_acc,
            sample_count=len(self._points),
        )

    def _calibrate_platt(self, confidence: float) -> tuple[float, float]:
        """Apply Platt scaling: sigmoid(A * logit(c) + B)."""
        eps = 1e-12
        c = np.clip(confidence, eps, 1.0 - eps)
        logit = np.log(c / (1.0 - c))
        z = self._platt_a * logit + self._platt_b
        calibrated = float(1.0 / (1.0 + np.exp(-z)))
        return calibrated, calibrated

    def _calibrate_isotonic(self, confidence: float) -> tuple[float, float]:
        """Apply isotonic regression prediction."""
        pred = float(self._isotonic_model.predict([[confidence]])[0])
        return pred, pred

    # ── Statistics ─────────────────────────────────────────────────

    def stats(self) -> CalibrationStats:
        """
        Compute calibration statistics including ECE.

        ECE (Expected Calibration Error) measures how well calibrated
        the model is. Lower ECE is better. ECE = 0 means perfect calibration.
        """
        n = len(self._points)
        if n == 0:
            return CalibrationStats(
                total_samples=0,
                method="none",
                ece=0.0,
                mce=0.0,
                avg_gap=0.0,
            )

        confidences = np.array([p.model_confidence for p in self._points])
        actuals = np.array([1.0 if p.actual_correct else 0.0 for p in self._points])

        # ECE calculation
        bin_boundaries = np.linspace(0.0, 1.0, self.ECE_NUM_BINS + 1)
        ece = 0.0
        mce = 0.0
        bin_details = []

        for i in range(self.ECE_NUM_BINS):
            in_bin = (confidences >= bin_boundaries[i]) & (confidences < bin_boundaries[i + 1])
            # Include the right edge in the last bin
            if i == self.ECE_NUM_BINS - 1:
                in_bin = (confidences >= bin_boundaries[i]) & (confidences <= bin_boundaries[i + 1])

            bin_count = int(np.sum(in_bin))
            if bin_count > 0:
                bin_conf = float(np.mean(confidences[in_bin]))
                bin_acc = float(np.mean(actuals[in_bin]))
                bin_weight = bin_count / n
                bin_error = abs(bin_acc - bin_conf)
                ece += bin_weight * bin_error
                mce = max(mce, bin_error)
                bin_details.append({
                    "bin": i,
                    "range": f"[{bin_boundaries[i]:.1f}, {bin_boundaries[i+1]:.1f})",
                    "count": bin_count,
                    "avg_confidence": round(bin_conf, 4),
                    "avg_accuracy": round(bin_acc, 4),
                    "gap": round(bin_error, 4),
                })
            else:
                bin_details.append({
                    "bin": i,
                    "range": f"[{bin_boundaries[i]:.1f}, {bin_boundaries[i+1]:.1f})",
                    "count": 0,
                })

        avg_gap = float(np.mean(confidences - actuals))

        return CalibrationStats(
            total_samples=n,
            method=self._method,
            ece=round(ece, 4),
            mce=round(mce, 4),
            avg_gap=round(avg_gap, 4),
            platt_a=round(self._platt_a, 4) if self._platt_a is not None else None,
            platt_b=round(self._platt_b, 4) if self._platt_b is not None else None,
            bins=bin_details,
        )

    def expected_calibration_error(self) -> float:
        """Convenience method: return ECE as a float."""
        return self.stats().ece

    # ── Persistence ────────────────────────────────────────────────

    def save(self) -> None:
        """
        Save calibration parameters to disk.

        Only saves the fitted parameters (A, B) and summary, not the raw data.
        Raw calibration points are NOT persisted — they are re-accumulated
        after restart.
        """
        if not self._fitted or self._method == "none":
            return

        data = {
            "method": self._method,
            "platt_a": self._platt_a,
            "platt_b": self._platt_b,
            "sample_count": len(self._points),
        }
        path = self._model_path / "params.json"
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

        logger.debug("Calibration params saved to %s", path)

    def load(self) -> None:
        """
        Load calibration parameters from disk.

        Restores previously fitted model so calibration works immediately
        after restart.
        """
        path = self._model_path / "params.json"
        if not path.exists():
            logger.debug("No calibration params found at %s", path)
            return

        try:
            with open(path) as f:
                data = json.load(f)

            method = data.get("method", "none")
            if method == "none":
                return

            if method == "platt" and data.get("platt_a") is not None:
                self._platt_a = float(data["platt_a"])
                self._platt_b = float(data["platt_b"])
                self._method = "platt"
                self._fitted = True
                logger.info(
                    "Loaded Platt calibration: A=%.4f B=%.4f (from %d samples)",
                    self._platt_a, self._platt_b, data.get("sample_count", 0),
                )
            elif method == "isotonic":
                # Isotonic model cannot be serialized as JSON —
                # need sample data to re-fit. Mark as not fitted.
                logger.warning(
                    "Isotonic model cannot be restored from params.json. "
                    "Re-accumulate data and call fit()."
                )
                self._method = "none"
                self._fitted = False

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning("Failed to load calibration params: %s", e)
            self._method = "none"
            self._fitted = False

    # ── Utility ─────────────────────────────────────────────────────

    @property
    def sample_count(self) -> int:
        """Number of calibration data points accumulated."""
        return len(self._points)

    @property
    def method(self) -> str:
        """Current calibration method."""
        return self._method

    @property
    def is_ready(self) -> bool:
        """Whether the calibrator has been fitted and can be used."""
        return self._fitted and self._method != "none"


# ════════════════════════════════════════════════════════════════════
# Module-level convenience
# ════════════════════════════════════════════════════════════════════

_default_calibrator: Optional[ConfidenceCalibrator] = None


def get_calibrator() -> ConfidenceCalibrator:
    """Get or create the default global calibrator instance."""
    global _default_calibrator
    if _default_calibrator is None:
        _default_calibrator = ConfidenceCalibrator()
    return _default_calibrator
