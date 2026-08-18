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
EXPECTED_SILENCES = 6        # 6.0 / 2.5 / 3.0 / 1.0 / 4.0 / 4.5초
EXPECTED_CUTS = 5            # 1.0초 숨 고르기는 min_silence(1.5) 미만이라 제외
EXPECTED_CONFIRMS = 1        # 3.0초 무음은 화면 시연 중 → '확인 필요'
EXPECTED_DEFAULT_SEC = 44.5  # 자동 컷만 적용 (60 - 15.5)
EXPECTED_FINAL_SEC = 42.0    # 확인 필요 컷까지 승인하면 (60 - 18)

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
    # encoding을 명시하지 않으면 로케일 코드페이지로 디코딩한다. 한글 Windows(cp949)에서
    # ffmpeg/스크립트가 뱉는 UTF-8 바이트를 만나면 리더 스레드가 죽고 stdout이 None이 된다.
    proc = subprocess.run(
        cmd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", cwd=SCRIPTS,
    )
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
    check("화면 정지 구간 검출", (analysis["stats"]["freeze_count"] or 0) > 0,
          f"{analysis['stats']['freeze_count']}개")

    print("■ 3. 컷 리스트 제안 (freezedetect 교차 검증)")
    sh([sys.executable, "plan_cuts.py", str(out / "analysis.json")])
    edl = json.loads((out / "edl.json").read_text(encoding="utf-8"))
    kinds = [c["kind"] for c in edl["cuts"]]
    confirms = [c for c in edl["cuts"] if c["category"] == "confirm"]
    check(f"컷 {EXPECTED_CUTS}개", len(edl["cuts"]) == EXPECTED_CUTS, f"{len(edl['cuts'])}개")
    check("시작 대기 구간 탐지", "head" in kinds)
    check("종료 여운 구간 탐지", "tail" in kinds)
    check("1초 숨 고르기는 보존",
          all(not (36.0 < c["start"] < 38.0) for c in edl["cuts"]))
    check(f"화면 시연 중 무음은 '확인 필요' {EXPECTED_CONFIRMS}개",
          len(confirms) == EXPECTED_CONFIRMS, f"{len(confirms)}개")
    check("시연 구간(26.75s)이 확인 필요로 분류",
          any(26.0 < c["start"] < 27.5 for c in confirms))
    check("승인용 표 생성", (out / "edl.md").exists())
    check(f"기본(자동만) 편집 후 {EXPECTED_DEFAULT_SEC}초",
          abs(edl["stats"]["final_sec"] - EXPECTED_DEFAULT_SEC) < 1.0,
          f"{edl['stats']['final_sec']}s")

    print("■ 4. 되살리기 (--keep-cuts 1)")
    sh([sys.executable, "plan_cuts.py", str(out / "analysis.json"), "--keep-cuts", "1"])
    edl_kept = json.loads((out / "edl.json").read_text(encoding="utf-8"))
    check("컷이 1개 줄어듦", len(edl_kept["cuts"]) == EXPECTED_CUTS - 1,
          f"{len(edl_kept['cuts'])}개")
    check("되살린 뒤 결과가 더 김", edl_kept["stats"]["final_sec"] > edl["stats"]["final_sec"])

    print("■ 4b. 확인 필요 컷 승인 (--confirm-cuts 3)")
    sh([sys.executable, "plan_cuts.py", str(out / "analysis.json"),
        "--confirm-cuts", "3"])
    edl = json.loads((out / "edl.json").read_text(encoding="utf-8"))
    check(f"승인 후 편집 {EXPECTED_FINAL_SEC}초",
          abs(edl["stats"]["final_sec"] - EXPECTED_FINAL_SEC) < 1.0,
          f"{edl['stats']['final_sec']}s")
    check("승인 후 적용 컷 5개", edl["stats"]["applied_count"] == 5,
          f"{edl['stats']['applied_count']}개")

    print("■ 5. 마스터 렌더")
    sh([sys.executable, "render_master.py", str(out / "edl.json"), "--preset", "draft"])
    log = json.loads((out / "render_master_log.json").read_text(encoding="utf-8"))
    master = Path(log["output"])
    check("마스터 파일 생성", master.exists())
    check("길이 오차 1초 미만", abs(log["drift_sec"]) < 1.0, f"{log['drift_sec']:+.2f}s")
    check("단일 패스로 처리", log["passes"] == 1, f"{log['passes']}패스")

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=duration", "-of", "csv=p=0", str(master)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout.strip()
    a_dur = float(probe.splitlines()[0]) if probe else 0.0
    check("A/V 싱크 (0.2초 이내)", abs(a_dur - log["actual_sec"]) < 0.2,
          f"오디오 {a_dur}s vs 영상 {log['actual_sec']}s")

    residual = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(master),
         "-af", "silencedetect=noise=-35dB:d=1.5", "-vn", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stderr.count("silence_start")
    check("긴 무음이 남지 않음", residual == 0, f"{residual}개 잔존")

    # select 수식은 between() 항이 100개를 넘으면 ffmpeg 파서가 죽는다. 구간이 많으면
    # 패스를 나눠 렌더한 뒤 이어붙여야 하고, 그 결과가 단일 패스와 같아야 한다.
    # 픽스처는 구간이 적으므로 --max-terms 2로 분할 경로를 강제해 검증한다.
    print("■ 6. 분할 렌더 (--max-terms 2로 강제)")
    multi = out / "master" / "multipass.mp4"
    sh([sys.executable, "render_master.py", str(out / "edl.json"),
        "--preset", "draft", "--max-terms", "2", "-o", str(multi)])
    mlog = json.loads((out / "render_master_log.json").read_text(encoding="utf-8"))
    check("여러 패스로 나뉨", mlog["passes"] > 1, f"{mlog['passes']}패스")
    check("분할 결과 파일 생성", multi.exists())
    check("분할 결과도 길이 오차 1초 미만",
          abs(mlog["drift_sec"]) < 1.0, f"{mlog['drift_sec']:+.2f}s")
    check("단일 패스와 길이 일치",
          abs(mlog["actual_sec"] - log["actual_sec"]) < 0.5,
          f"분할 {mlog['actual_sec']}s vs 단일 {log['actual_sec']}s")
    check("중간 파트 폴더 정리됨", not (out / "master" / "_parts").exists())

    m_residual = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(multi),
         "-af", "silencedetect=noise=-35dB:d=1.5", "-vn", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stderr.count("silence_start")
    check("분할본에도 긴 무음이 남지 않음", m_residual == 0, f"{m_residual}개 잔존")

    # 줌 로컬 녹화는 VFR인 경우가 많고, 정규화 없이 자르면 재생 불가/싱크 밀림이
    # 난다(TROUBLESHOOTING 참조). 픽스처에서 프레임을 불규칙하게 떨어뜨려 VFR을
    # 재현하고, 감지 → fps 정규화 → A/V 싱크 유지를 검증한다.
    print("■ 7. VFR 원본 (감지 + CFR 정규화)")
    vfr_rec = work / "vfr" / "rec"
    vfr_out = work / "vfr" / "out"
    vfr_rec.mkdir(parents=True, exist_ok=True)
    fixture_video = fixture / "zoom_0.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-i", str(fixture_video),
         "-vf", "select='not(mod(n,3))+not(mod(n,7))'", "-fps_mode", "vfr",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
         "-pix_fmt", "yuv420p", "-c:a", "copy", str(vfr_rec / "zoom_0.mp4")],
        check=True, capture_output=True,
    )
    sh([sys.executable, "ingest.py", str(vfr_rec), "-o", str(vfr_out)])
    vfr_manifest = json.loads((vfr_out / "manifest.json").read_text(encoding="utf-8"))
    check("VFR 감지됨", vfr_manifest["media"]["vfr"] is True,
          f"avg {vfr_manifest['media']['fps']} vs r {vfr_manifest['media']['r_fps']}")

    sh([sys.executable, "analyze.py", str(vfr_out / "manifest.json")])
    sh([sys.executable, "plan_cuts.py", str(vfr_out / "analysis.json")])
    sh([sys.executable, "render_master.py", str(vfr_out / "edl.json"),
        "--preset", "draft"])
    vlog = json.loads((vfr_out / "render_master_log.json").read_text(encoding="utf-8"))
    check("CFR 정규화 적용됨", vlog["cfr_fps"] is not None, f"{vlog['cfr_fps']}fps")
    check("VFR도 길이 오차 1초 미만", abs(vlog["drift_sec"]) < 1.0,
          f"{vlog['drift_sec']:+.2f}s")

    v_streams = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration",
         "-of", "csv=p=0", vlog["output"]],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout.strip().splitlines()
    durs = [float(line.split(",")[-1]) for line in v_streams if line]
    av_gap = abs(durs[0] - durs[1]) if len(durs) >= 2 else 999.0
    check("VFR 렌더 A/V 싱크 (0.2초 이내)", av_gap < 0.2, f"차이 {av_gap:.3f}s")

    # M2 로직(컷 스냅·반복 탐지·SRT 재계산·대본)은 합성 전사로 검증한다 —
    # whisper 없이 돌므로 CI에서도 항상 실행 가능
    print("■ 8. M2 로직 (합성 전사)")
    m2 = subprocess.run(
        [sys.executable, str(HERE / "test_m2_logic.py"), str(work)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    tail = (m2.stdout or "").strip().splitlines()
    check("M2 단위 테스트 통과", m2.returncode == 0,
          tail[-1] if tail else "출력 없음")

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
