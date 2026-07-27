"""Offline acceptance runner for synchronized barge-in fixture sessions."""

from __future__ import annotations

import argparse
import json
import wave
from dataclasses import asdict, dataclass
from pathlib import Path

from talos.voice.streaming.vad import (
    BargeInVadGate,
    SileroProbabilityVAD,
    VadGateConfig,
)


@dataclass(frozen=True)
class AcceptanceCaseResult:
    name: str
    category: str
    expected_candidate: bool
    candidate_detected: bool
    passed: bool
    max_probability: float
    utterance_count: int


def summarize_results(results: list[AcceptanceCaseResult]) -> dict[str, object]:
    positives = [item for item in results if item.expected_candidate]
    negatives = [item for item in results if not item.expected_candidate]
    recalled = sum(item.candidate_detected for item in positives)
    false_accepts = sum(item.candidate_detected for item in negatives)
    return {
        "cases": len(results),
        "passed": sum(item.passed for item in results),
        "failed": sum(not item.passed for item in results),
        "positive_cases": len(positives),
        "negative_cases": len(negatives),
        "interruption_recall": recalled / len(positives) if positives else None,
        "false_candidate_count": false_accepts,
    }


def _read_pcm16_mono(path: Path) -> bytes:
    with wave.open(str(path), "rb") as wav:
        if (
            wav.getframerate() != 16000
            or wav.getnchannels() != 1
            or wav.getsampwidth() != 2
        ):
            raise ValueError(f"{path} must be 16 kHz mono PCM16")
        return wav.readframes(wav.getnframes())


def evaluate_manifest(
    manifest_path: Path,
    *,
    probability_vad=None,
    config: VadGateConfig | None = None,
) -> dict[str, object]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    vad = probability_vad or SileroProbabilityVAD()
    results: list[AcceptanceCaseResult] = []
    for case in manifest.get("cases", []):
        capture_path = (manifest_path.parent / case["capture_wav"]).resolve()
        render_path = (manifest_path.parent / case["render_wav"]).resolve()
        alignment_path = (manifest_path.parent / case["events_jsonl"]).resolve()
        if not render_path.is_file() or not alignment_path.is_file():
            raise FileNotFoundError(
                f"{case['name']} is not a synchronized capture/render fixture"
            )
        pcm = _read_pcm16_mono(capture_path)
        utterances: list[tuple[bytes, dict[str, float]]] = []
        probabilities: list[float] = []

        def probability(frame: bytes) -> float:
            value = float(vad.probability(frame))
            probabilities.append(value)
            return value

        gate = BargeInVadGate(
            probability,
            lambda audio, evidence: utterances.append((audio, evidence)),
            config=config,
        )
        for offset in range(0, len(pcm), 320):
            gate.observe(pcm[offset : offset + 320])
        expected = bool(case["expected_candidate"])
        detected = bool(utterances)
        results.append(
            AcceptanceCaseResult(
                name=str(case["name"]),
                category=str(case["category"]),
                expected_candidate=expected,
                candidate_detected=detected,
                passed=expected == detected,
                max_probability=max(probabilities, default=0.0),
                utterance_count=len(utterances),
            )
        )
        reset = getattr(vad, "reset", None)
        if callable(reset):
            reset()
    return {
        "schema_version": 1,
        "manifest": str(manifest_path),
        "stores_pcm": False,
        "summary": summarize_results(results),
        "results": [asdict(item) for item in results],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    report = evaluate_manifest(args.manifest.resolve())
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
