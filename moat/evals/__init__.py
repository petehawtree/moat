"""Frozen v1 external-benchmark evaluation harness.

This package is deliberately separate from the production pipeline.  It reads
sealed snapshots and synthetic/private benchmark inputs; it never writes to a
pipeline database.
"""

from .errors import EvalHarnessError, ManifestError, PreflightError

__all__ = ["EvalHarnessError", "ManifestError", "PreflightError"]
