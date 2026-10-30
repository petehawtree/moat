"""Errors whose messages are safe for logs.

Messages must describe rules and counts, never proprietary row values or
company identifiers.
"""


class EvalHarnessError(RuntimeError):
    """Base class for expected, disclosure-safe harness failures."""


class ManifestError(EvalHarnessError):
    """The execution manifest is malformed or does not bind the inputs."""


class PreflightError(EvalHarnessError):
    """An immutable input failed a frozen preflight rule."""
