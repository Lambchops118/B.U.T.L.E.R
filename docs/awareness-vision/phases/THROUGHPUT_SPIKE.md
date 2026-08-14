# Throughput Spike — Vision Models on the Dev Mac (Phase V0 evidence)

**Date:** 2026-07-17 · **Host:** MacBook, Apple Silicon (`arm64`), macOS 15.7 ·
**Method:** ephemeral benchmark in an isolated venv (no repo code, no persisted
frames); 30–100 timed forwards per stage after warmup on synthetic 1280×720
frames. Not run against a live camera. Reproducible via `scratchpad/spike.py`.

## Results

| Stage | Backend | p50 ms | p95 ms | FPS |
|---|---|---|---|---|
| YOLO11n **detection** @640 (presence/occupancy) | torch **MPS** | 7.2 | 8.0 | **136** |
| YOLO11n **pose** @640 (activity) | torch **MPS** | 8.3 | 8.7 | **121** |
| InsightFace **SCRFD** face detector @640 (re-ID) | onnxruntime **CPU** | 112.4 | 129.2 | 8.7 |
| InsightFace **ArcFace r50** embed, per face (re-ID) | onnxruntime **CPU** | 74.9 | 101.6 | 12.9 |

### Combined per-frame budget (estimated)
- **Detect + pose every frame:** ~15.6 ms → **64 FPS ceiling**.
- **+ re-ID amortized** (full re-ID pass ~270 ms for 2 faces, run 1-in-5 frames): ~69.6 ms/frame → **~14 FPS**.
- **Target need:** presence/activity are useful at **2–5 FPS**. Even the fully
  loaded, unoptimized pipeline delivers ~3–7× the required rate.

## Interpretation

1. **Detection and pose are effortless on this Mac** (MPS): a single camera
   uses a few percent of available headroom. This is the common path for
   presence, occupancy, and activity.
2. **Face re-ID is the only heavy stage — and it ran unaccelerated.** InsightFace
   used `CPUExecutionProvider` even though `CoreMLExecutionProvider` was
   available, so the 112 ms detector / 75 ms embed figures are a **conservative
   floor**. Expect meaningful speedups from: (a) engaging CoreML on the Mac,
   (b) CUDA on the RTX 2060/5080 or a Jetson, and (c) the architectural
   optimization below.
3. **We don't need full-frame SCRFD.** YOLO already yields person boxes, so face
   detection can run only inside person ROIs (much smaller than 640×640), and
   re-ID need only run on **new/unconfirmed tracks**, not every frame. Both cut
   the re-ID cost far below this worst-case estimate.
4. **Synthetic-frame caveat:** noise frames contain ~0 real faces, so SCRFD does
   full detection work but alignment/embedding of real faces adds minor overhead
   not captured here. Numbers are indicative, not a live-camera measurement.

## Decision impact

- **The single-camera workload is not throughput-bound on any of the candidate
  hosts.** The Mac alone clears it with room to spare; the 2060/5080 or a Jetson
  would be idle by comparison.
- Therefore the compute decision is driven by **placement** (USB/CSI tethers the
  box to the living room) and **privacy** (inference isolated at the edge), not
  by FPS. See `PHASE_V0_DISCOVERY.md` D-V0-2.
- **Recommendation:** develop on the Mac (proven here); defer the deploy-device
  purchase to end of V1. If a dedicated edge device is chosen, a Jetson Orin
  Nano-class board runs all three stages on CUDA comfortably and keeps frames at
  the camera. Sharing the RTX 2060 is viable on throughput but re-introduces the
  USB tether to the PC and contends with the voice pipeline.

## Notes / artifacts
- Model weights downloaded to caches during the spike: `yolo11n.pt`,
  `yolo11n-pose.pt` (scratchpad), and `~/.insightface/models/buffalo_l` (~280 MB,
  standard InsightFace cache). None are repo files.
- Weights licensing is an **owner decision before V1** (D-V0-8): Ultralytics YOLO
  is AGPL-3.0; the InsightFace `buffalo_l` pack carries non-commercial research
  terms. Alternatives (e.g. permissively-licensed detectors / ArcFace variants)
  should be evaluated if the license matters for this deployment.
