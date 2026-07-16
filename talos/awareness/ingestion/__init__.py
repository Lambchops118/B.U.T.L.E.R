"""Deterministic ingestion pipeline (C2/C3): MQTT → canonical events → PostgreSQL."""

from talos.awareness.ingestion.pipeline import InboundMessage, IngestionMetrics, IngestionPipeline

__all__ = ["InboundMessage", "IngestionMetrics", "IngestionPipeline"]
