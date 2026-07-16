"""TALOS awareness subsystem.

Deterministic presence, event-processing, state, history, alerting, and memory
backend that runs as its own process (``python -m talos.awareness``) alongside
the main TALOS agent. See ``talos/awareness/README.md`` and the repo-root
``DISCOVERY.md`` for architecture and phase status.

This package intentionally does not import from other ``talos`` subpackages so
it can run in its own Python 3.12 virtual environment.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
