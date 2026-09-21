"""Current-state authority, freshness, and transition management (C5, Phase 3)."""

from butler.awareness.state.classification import EventEffects, StateUpdate, TelemetryPoint, classify
from butler.awareness.state.manager import StateManager

__all__ = ["EventEffects", "StateUpdate", "TelemetryPoint", "classify", "StateManager"]
