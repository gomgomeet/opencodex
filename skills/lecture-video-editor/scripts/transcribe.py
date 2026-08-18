#!/usr/bin/env python3
"""한국어 전사 — faster-whisper로 단어 단위 타임스탬프를 뽑아 transcript.json을 만든다.

전사는 두 가지 용도로 쓰인다:
  1. 자막(SRT) 생성 (make_subtitles.py)
  2. 컷 경계 보정 — 한국어는 어미를 작게 발음해 무음 검출이 말끝을 먹는다.
     단어 타임스탬프에 컷 경계를 스냅해 "그렇습니다"가 "그렇니다"가 되는 것을
     막는다 (refine_cuts.py)

모든 처리는 로컬에서 수행한다. 음성을 외부 API로 보내지 않는다.

사용:
    python3 transcribe.py <출력폴더>/manifest.json [--model large-v3] [--device auto]
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any

from lve.common import hhmmss, load_json, save_json


def load_whisper():
    try:
        from faster_whisper import WhisperModel  # type: ignore
        return WhisperModel
    except ImportError:
        raise SystemExit(
            "faster-whisper가 설치되어 있지 않습니다.\n"
            "  pip install faster-whisper\n"
            "첫 실행 시 모델(large-v3 기준 약 3GB)을 자동 다운로드합니다.\n"
            "메모리가 부족하면 --model medium 또는 small을 쓰세요."
        )


def transcribe(
    audio: Path, *, model_name: str, device: str, compute_type: str, language: str
) -> dict[str, Any]:
    WhisperModel = load_whisper()
    try:
        model = WhisperModel(model_name, device=device, compute_type=compute_type)
    except Exception as e:  # 모델 다운로드 실패(오프라인/프록시)가 흔한 원인
        raise SystemExit(
            f"whisper 모델({model_name}) 로드 실패: {e}\n"
            "첫 실행은 모델 다운로드에 인터넷이 필요합니다. 네트워크가 막힌 환경이면\n"
            "온라인 환경에서 같은 모델을 한 번 실행해 캐시(~/.cache/huggingface)를\n"
            "받아두거나, 더 작은 모델(--model small)을 시도하세요."
        )

    segments_iter, info = model.transcribe(
        str(audio),
        language=language,
        word_timestamps=True,      # 컷 스냅의 핵심 — 단어마다 시각이 필요하다
        vad_filter=True,           # 무음 구간을 건너뛰어 환각을 줄인다
        beam_size=5,
    )

    segments: list[dict[str, Any]] = []
    words: list[dict[str, Any]] = []
    for seg in segments_iter:
        segments.append(
            {"start": round(seg.start, 3), "end": round(seg.end, 3),
             "text": seg.text.strip()}
        )
        for w in seg.words or []:
            words.append(
                {"start": round(w.start, 3), "end": round(w.end, 3),
                 "word": w.word.strip(), "prob": round(w.probability, 3)}
            )

    return {
        "language": info.language,
        "language_prob": round(info.language_probability, 3),
        "duration_sec": round(info.duration, 3),
        "model": model_name,
        "segments": segments,
        "words": words,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="한국어 전사 (단어 타임스탬프 포함)")
    ap.add_argument("manifest", type=Path, help="ingest.py가 만든 manifest.json")
    ap.add_argument("--model", default="large-v3",
                    help="whisper 모델 (기본 large-v3, 가벼운 대안: medium/small)")
    ap.add_argument("--device", default="auto", help="auto/cpu/cuda")
    ap.add_argument("--compute-type", default="auto",
                    help="auto/int8/float16 등 — CPU면 int8이 빠르다")
    ap.add_argument("--language", default="ko")
    args = ap.parse_args()

    manifest = load_json(args.manifest)
    out_dir = Path(manifest["output_dir"])

    # 오디오 전용 파일이 있으면 그걸 쓴다 — 디코딩이 훨씬 빠르다
    src = manifest["source"].get("audio_only") or manifest["source"]["main_video"]
    src_path = Path(src)
    duration = float(manifest["media"]["duration_sec"])

    print(f"■ 전사 시작 — {src_path.name} ({hhmmss(duration)}, {args.model})")
    print("  모든 처리는 로컬입니다. 음성을 외부로 보내지 않습니다.")

    t0 = time.monotonic()
    result = transcribe(
        src_path,
        model_name=args.model,
        device=args.device,
        compute_type=args.compute_type,
        language=args.language,
    )
    elapsed = time.monotonic() - t0

    result["elapsed_sec"] = round(elapsed, 2)
    path = save_json(out_dir / "transcript.json", result)

    print(f"  언어      : {result['language']} (확신도 {result['language_prob']})")
    print(f"  세그먼트  : {len(result['segments'])}개 / 단어 {len(result['words'])}개")
    print(f"  소요      : {elapsed:.1f}s"
          f" (실시간의 {duration / elapsed:.1f}배속)" if elapsed else "")
    print(f"  transcript: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
