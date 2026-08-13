#!/usr/bin/env python3
"""마스터 렌더 — 승인된 edl.json의 남길 구간만 이어붙여 마스터 영상을 만든다.

풀영상·분할본·쇼츠는 모두 이 마스터에서 파생되므로, 여기서만 정확히 자르고
이후 단계는 마스터를 기준으로 삼는다(파생 일관성).

구현: select/aselect 필터로 한 번에 통과시킨다. 세그먼트를 파일로 쪼갠 뒤 concat하는
방식보다 임시 파일이 없고, 키프레임에 얽매이지 않아 프레임 단위로 정확하다.

사용:
    python3 render_master.py <출력폴더>/edl.json [--preset master] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from lve.common import ffprobe_json, hhmmss, load_json, require_tool, run, save_json

PRESETS_PATH = Path(__file__).resolve().parent.parent / "presets" / "encode.json"


def build_filter_script(keeps: list[dict[str, float]], *, has_audio: bool) -> str:
    """select/aselect 필터 스크립트를 만든다.

    between(t,S,E)를 더해 남길 구간만 통과시키고, setpts로 타임스탬프를 0부터
    다시 매긴다(안 하면 잘라낸 만큼 빈 시간이 남는다).
    """
    terms = "+".join(
        f"between(t,{k['start']:.3f},{k['end']:.3f})" for k in keeps
    )
    parts = [f"[0:v]select='{terms}',setpts=N/FRAME_RATE/TB[v]"]
    if has_audio:
        parts.append(f"[0:a]aselect='{terms}',asetpts=N/SR/TB[a]")
    return ";\n".join(parts)


def load_preset(name: str) -> dict[str, Any]:
    presets = json.loads(PRESETS_PATH.read_text(encoding="utf-8"))
    if name not in presets:
        raise SystemExit(
            f"알 수 없는 프리셋: {name}\n사용 가능: {', '.join(presets)}"
        )
    return presets[name]


def main() -> int:
    ap = argparse.ArgumentParser(description="승인된 EDL로 마스터를 렌더링한다")
    ap.add_argument("edl", type=Path, help="plan_cuts.py가 만든 edl.json")
    ap.add_argument("--preset", default="master", help="인코딩 프리셋 (기본 master)")
    ap.add_argument("-o", "--out", type=Path, default=None, help="출력 파일 경로")
    ap.add_argument("--dry-run", action="store_true", help="명령만 출력하고 실행하지 않는다")
    args = ap.parse_args()

    out_dir = Path(args.edl).parent
    edl = load_json(args.edl)
    manifest = load_json(out_dir / "manifest.json")

    keeps = edl.get("keeps") or []
    if not keeps:
        raise SystemExit("남길 구간이 없습니다. edl.json을 확인하세요.")

    src = Path(manifest["source"]["main_video"])
    has_audio = bool(manifest["media"].get("has_audio"))
    preset = load_preset(args.preset)

    out_path = args.out or (out_dir / "master" / f"{src.stem}_master.mp4")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    filter_script = build_filter_script(keeps, has_audio=has_audio)
    script_path = out_dir / "filter_master.txt"
    script_path.write_text(filter_script, encoding="utf-8")

    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        "-i", str(src),
        "-filter_complex_script", str(script_path),
        "-map", "[v]",
    ]
    if has_audio:
        cmd += ["-map", "[a]"]
    cmd += list(preset["video"])
    if has_audio:
        cmd += list(preset["audio"])
    cmd += list(preset.get("extra") or [])
    cmd += [str(out_path)]

    st = edl["stats"]
    print(f"■ 마스터 렌더 — {src.name}")
    print(f"  남길 구간 : {len(keeps)}개")
    print(f"  예상 길이 : {hhmmss(st['final_sec'])} "
          f"(원본 {hhmmss(st['original_sec'])}에서 {st['removed_ratio'] * 100:.1f}% 단축)")
    print(f"  프리셋    : {args.preset} — {preset['description']}")
    print(f"  출력      : {out_path}")

    if args.dry_run:
        print("\n[dry-run] 실행할 명령:")
        print("  " + " ".join(cmd))
        return 0

    require_tool("ffmpeg")
    t0 = time.monotonic()
    run(cmd)
    elapsed = time.monotonic() - t0

    probe = ffprobe_json(out_path)
    actual = float(probe.get("format", {}).get("duration") or 0.0)
    drift = actual - st["final_sec"]

    log = {
        "input": str(src),
        "output": str(out_path),
        "preset": args.preset,
        "keeps": len(keeps),
        "expected_sec": st["final_sec"],
        "actual_sec": round(actual, 3),
        "drift_sec": round(drift, 3),
        "elapsed_sec": round(elapsed, 2),
        "realtime_factor": round(st["final_sec"] / elapsed, 2) if elapsed else None,
        "command": cmd,
        "size_bytes": out_path.stat().st_size,
    }
    save_json(out_dir / "render_master_log.json", log)

    print(f"  완료      : {hhmmss(actual)} (예상 대비 {drift:+.2f}s)")
    print(f"  소요      : {elapsed:.1f}s (실시간의 {log['realtime_factor']}배속)")
    # 프레임 경계 반올림으로 0.5초 안쪽 오차는 정상이다
    if abs(drift) > 1.0:
        print(f"  ⚠ 길이 오차가 {drift:+.2f}s 입니다 — EDL과 결과를 확인하세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
