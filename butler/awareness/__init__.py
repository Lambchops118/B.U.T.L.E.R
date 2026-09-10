"""Butler awareness subsystem.

Deterministic presence, event-processing, state, history, alerting, and memory
backend that runs as its own process (``python -m butler.awareness``) alongside
the main Butler agent. See ``butler/awareness/README.md`` and the repo-root
``DISCOVERY.md`` for architecture and phase status.

This package intentionally does not import from other ``butler`` subpackages so
it can run in its own Python 3.12 virtual environment.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
