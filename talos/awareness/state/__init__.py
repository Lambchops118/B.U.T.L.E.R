"""Current-state authority, freshness, and transition management (C5, Phase 3)."""

from talos.awareness.state.classification import EventEffects, StateUpdate, TelemetryPoint, classify
from talos.awareness.state.manager import StateManager

__all__ = ["EventEffects", "StateUpdate", "TelemetryPoint", "classify", "StateManager"]
