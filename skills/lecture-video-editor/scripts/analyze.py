#!/usr/bin/env python3
"""무음 분석 — ffmpeg silencedetect로 무음 구간을 뽑아 analysis.json에 저장한다.

탐지는 한 번만 하고(디코딩이 가장 비싼 단계), 실제 컷 기준(최소 무음 길이·여백)은
plan_cuts.py에서 적용한다. 그래서 여기서는 최소 길이를 낮게(0.3s) 잡아
원본 무음 목록을 넉넉히 남긴다.

줌 녹화는 노이즈 억제·자동 게인이 이미 적용되어 음소거 구간이 거의 디지털 무음이라
임계값 -35dB로도 잘 잡힌다.

사용:
    python3 analyze.py <출력폴더>/manifest.json [--noise -35dB] [--min-silence 0.3]
"""

from __future__ import annotations

import argparse
import re
import time
from pathlib import Path
from typing import Any

from lve.common import hhmmss, load_json, require_tool, run, save_json

SILENCE_START_RE = re.compile(r"silence_start:\s*(-?[\d.]+)")
SILENCE_END_RE = re.compile(r"silence_end:\s*(-?[\d.]+)")


def detect_silences(
    media: Path, duration: float, *, noise: str, min_silence: float
) -> list[dict[str, float]]:
    """[{start, end, duration}] 목록을 반환한다."""
    require_tool("ffmpeg")
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats",
        "-i", str(media),
        "-af", f"silencedetect=noise={noise}:d={min_silence}",
        "-vn", "-f", "null", "-",
    ]
    # silencedetect 결과는 stderr로 나온다
    proc = run(cmd)
    lines = (proc.stderr or "").splitlines()

    silences: list[dict[str, float]] = []
    pending: float | None = None
    for line in lines:
        if "silence_start" in line:
            m = SILENCE_START_RE.search(line)
            if m:
                pending = max(0.0, float(m.group(1)))
        elif "silence_end" in line:
            m = SILENCE_END_RE.search(line)
            if m and pending is not None:
                end = float(m.group(1))
                if end > pending:
                    silences.append(
                        {"start": round(pending, 3), "end": round(end, 3),
                         "duration": round(end - pending, 3)}
                    )
                pending = None
    # 마지막 무음이 파일 끝까지 이어지면 silence_end가 나오지 않는다
    if pending is not None and duration > pending:
        silences.append(
            {"start": round(pending, 3), "end": round(duration, 3),
             "duration": round(duration - pending, 3)}
        )
    return silences


def speech_spans(silences: list[dict[str, float]], duration: float) -> list[dict[str, float]]:
    """무음의 여집합 = 발화 구간."""
    spans: list[dict[str, float]] = []
    cursor = 0.0
    for s in silences:
        if s["start"] > cursor:
            spans.append({"start": round(cursor, 3), "end": round(s["start"], 3)})
        cursor = max(cursor, s["end"])
    if duration > cursor:
        spans.append({"start": round(cursor, 3), "end": round(duration, 3)})
    return spans


def main() -> int:
    ap = argparse.ArgumentParser(description="무음 구간을 분석한다")
    ap.add_argument("manifest", type=Path, help="ingest.py가 만든 manifest.json")
    ap.add_argument("--noise", default="-35dB", help="무음 판정 임계값 (기본 -35dB)")
    ap.add_argument(
        "--min-silence", type=float, default=0.3,
        help="탐지 최소 무음 길이(초). 실제 컷 기준은 plan_cuts.py에서 정한다",
    )
    args = ap.parse_args()

    manifest = load_json(args.manifest)
    out_dir = Path(manifest["output_dir"])
    duration = float(manifest["media"]["duration_sec"])

    if not manifest["media"].get("has_audio"):
        raise SystemExit("오디오 트랙이 없어 무음 분석을 할 수 없습니다.")

    # 오디오 전용 파일이 있으면 그걸 쓴다 — 영상 디코딩을 건너뛰어 훨씬 빠르다
    src = manifest["source"].get("audio_only") or manifest["source"]["main_video"]
    src_path = Path(src)
    print(f"■ 무음 분석 시작 — {src_path.name} ({hhmmss(duration)}, {args.noise})")

    t0 = time.monotonic()
    silences = detect_silences(
        src_path, duration, noise=args.noise, min_silence=args.min_silence
    )
    elapsed = time.monotonic() - t0

    spans = speech_spans(silences, duration)
    silent_total = sum(s["duration"] for s in silences)

    analysis: dict[str, Any] = {
        "params": {"noise": args.noise, "min_silence_detected": args.min_silence},
        "source": str(src_path),
        "duration_sec": duration,
        "silences": silences,
        "speech_spans": spans,
        "stats": {
            "silence_count": len(silences),
            "silence_total_sec": round(silent_total, 3),
            "silence_ratio": round(silent_total / duration, 4) if duration else 0.0,
            "speech_span_count": len(spans),
            "analyze_elapsed_sec": round(elapsed, 2),
            "realtime_factor": round(duration / elapsed, 1) if elapsed else None,
        },
    }
    path = save_json(out_dir / "analysis.json", analysis)

    st = analysis["stats"]
    print(f"  무음 구간 : {st['silence_count']}개 / 총 {hhmmss(silent_total)}"
          f" ({st['silence_ratio'] * 100:.1f}%)")
    print(f"  발화 구간 : {st['speech_span_count']}개")
    print(f"  소요      : {elapsed:.1f}s (실시간의 {st['realtime_factor']}배속)")
    print(f"  analysis  : {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
