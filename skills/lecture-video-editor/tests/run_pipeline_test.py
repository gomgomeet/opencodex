#!/usr/bin/env python3
"""파이프라인 회귀 테스트 — 픽스처로 인제스트→분석→컷제안→렌더를 전부 돌리고 검증한다.

사용:
    python3 run_pipeline_test.py <작업폴더>
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
FIXTURE_NAME = "2026-08-13 10.00.00 테스트 강의 - 무음 컷 검증"

# make_fixture.py의 타임라인에서 나오는 기대값
EXPECTED_SILENCES = 6      # 6.0 / 2.5 / 3.0 / 1.0 / 4.0 / 4.5초
EXPECTED_CUTS = 5          # 1.0초 숨 고르기는 min_silence(1.5) 미만이라 제외
EXPECTED_FINAL_SEC = 42.0  # 60초 - 컷 18초

failures: list[str] = []
checks = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(f"{label}: {detail}")


def sh(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=SCRIPTS)
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        raise SystemExit(f"명령 실패: {' '.join(cmd[:3])}")
    return proc.stdout


def main() -> int:
    if len(sys.argv) < 2:
        print("사용: run_pipeline_test.py <작업폴더>", file=sys.stderr)
        return 2
    work = Path(sys.argv[1]).expanduser().resolve()
    work.mkdir(parents=True, exist_ok=True)
    fixture = work / "fixtures" / FIXTURE_NAME
    out = work / "out"

    if not fixture.exists():
        print("■ 픽스처 생성")
        sh([sys.executable, str(HERE / "make_fixture.py"), str(work / "fixtures")])

    print("■ 1. 인제스트")
    sh([sys.executable, "ingest.py", str(fixture), "-o", str(out)])
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    check("본 영상은 zoom_0.mp4", Path(manifest["source"]["main_video"]).name == "zoom_0.mp4")
    check("audio_only 인식", manifest["source"]["audio_only"] is not None)
    check("chat.txt 인식", manifest["source"]["chat_txt"] is not None)
    check("로컬 녹화로 판정", manifest["source"]["recording_kind"] == "local")
    check("길이 60초", abs(manifest["media"]["duration_sec"] - 60.0) < 0.5,
          f"{manifest['media']['duration_sec']}s")

    print("■ 2. 무음 분석")
    sh([sys.executable, "analyze.py", str(out / "manifest.json")])
    analysis = json.loads((out / "analysis.json").read_text(encoding="utf-8"))
    n_sil = analysis["stats"]["silence_count"]
    check(f"무음 {EXPECTED_SILENCES}개 검출", n_sil == EXPECTED_SILENCES, f"{n_sil}개")
    check("분석에 audio_only 사용", "audio_only" in analysis["source"])

    print("■ 3. 컷 리스트 제안")
    sh([sys.executable, "plan_cuts.py", str(out / "analysis.json")])
    edl = json.loads((out / "edl.json").read_text(encoding="utf-8"))
    kinds = [c["kind"] for c in edl["cuts"]]
    check(f"컷 {EXPECTED_CUTS}개", len(edl["cuts"]) == EXPECTED_CUTS, f"{len(edl['cuts'])}개")
    check("시작 대기 구간 탐지", "head" in kinds)
    check("종료 여운 구간 탐지", "tail" in kinds)
    check("1초 숨 고르기는 보존",
          all(not (36.0 < c["start"] < 38.0) for c in edl["cuts"]))
    check("승인용 표 생성", (out / "edl.md").exists())
    check(f"편집 후 {EXPECTED_FINAL_SEC}초",
          abs(edl["stats"]["final_sec"] - EXPECTED_FINAL_SEC) < 1.0,
          f"{edl['stats']['final_sec']}s")

    print("■ 4. 되살리기 (--keep-cuts 1)")
    sh([sys.executable, "plan_cuts.py", str(out / "analysis.json"), "--keep-cuts", "1"])
    edl_kept = json.loads((out / "edl.json").read_text(encoding="utf-8"))
    check("컷이 1개 줄어듦", len(edl_kept["cuts"]) == EXPECTED_CUTS - 1,
          f"{len(edl_kept['cuts'])}개")
    check("되살린 뒤 결과가 더 김", edl_kept["stats"]["final_sec"] > edl["stats"]["final_sec"])
    sh([sys.executable, "plan_cuts.py", str(out / "analysis.json")])  # 원복

    print("■ 5. 마스터 렌더")
    sh([sys.executable, "render_master.py", str(out / "edl.json"), "--preset", "draft"])
    log = json.loads((out / "render_master_log.json").read_text(encoding="utf-8"))
    master = Path(log["output"])
    check("마스터 파일 생성", master.exists())
    check("길이 오차 1초 미만", abs(log["drift_sec"]) < 1.0, f"{log['drift_sec']:+.2f}s")

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=duration", "-of", "csv=p=0", str(master)],
        capture_output=True, text=True,
    ).stdout.strip()
    a_dur = float(probe.splitlines()[0]) if probe else 0.0
    check("A/V 싱크 (0.2초 이내)", abs(a_dur - log["actual_sec"]) < 0.2,
          f"오디오 {a_dur}s vs 영상 {log['actual_sec']}s")

    residual = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(master),
         "-af", "silencedetect=noise=-35dB:d=1.5", "-vn", "-f", "null", "-"],
        capture_output=True, text=True,
    ).stderr.count("silence_start")
    check("긴 무음이 남지 않음", residual == 0, f"{residual}개 잔존")

    print(f"\n{'─' * 50}")
    if failures:
        print(f"실패 {len(failures)}/{checks}")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"전체 통과 ({checks}/{checks})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
