#!/usr/bin/env python3
"""M2 로직 단위 테스트 — Whisper 없이 합성 전사로 검증한다.

검증 항목:
  B3  컷 경계가 단어("그렇습니다")를 자르면 경계가 단어 밖으로 밀리는가
  B5  딱 붙은 반복("그래서 그래서")은 자동 컷, 간격 있는 반복은 확인 필요인가
  SRT 컷으로 사라진 시간만큼 타임코드가 재계산되는가
  B6  완성 대본이 생성되고 보호된 단어가 들어 있는가

사용: python3 test_m2_logic.py <작업폴더>
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"

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
    proc = subprocess.run(
        cmd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", cwd=SCRIPTS,
    )
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        raise SystemExit(f"명령 실패: {' '.join(cmd[:3])}")
    return proc.stdout


def W(start: float, end: float, word: str) -> dict:
    return {"start": start, "end": end, "word": word, "prob": 0.95}


def build_fixture_files(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    # 픽스처 타임라인과 같은 컷 구성 (확인 필요 컷은 승인된 상태)
    cuts = [
        {"kind": "head", "category": "auto", "applied": True,
         "start": 0.0, "end": 5.75, "duration": 5.75},
        {"kind": "silence", "category": "auto", "applied": True,
         "start": 14.25, "end": 16.25, "duration": 2.0},
        {"kind": "silence", "category": "confirm", "applied": True,
         "start": 26.75, "end": 29.25, "duration": 2.5},
        {"kind": "silence", "category": "auto", "applied": True,
         "start": 46.75, "end": 50.25, "duration": 3.5},
        {"kind": "tail", "category": "auto", "applied": True,
         "start": 55.75, "end": 60.0, "duration": 4.25},
    ]
    keeps = [
        {"start": 5.75, "end": 14.25}, {"start": 16.25, "end": 26.75},
        {"start": 29.25, "end": 46.75}, {"start": 50.25, "end": 55.75},
    ]
    edl = {
        "params": {"noise": "-35dB", "min_silence": 1.5, "pad": 0.25,
                   "min_cut": 0.5, "min_keep": 0.4, "freeze_checked": True,
                   "freeze_overlap": 0.7, "kept_cut_indices": [],
                   "confirmed_cut_indices": [3]},
        "duration_sec": 60.0,
        "cuts": cuts,
        "keeps": keeps,
        "stats": {"original_sec": 60.0, "removed_sec": 18.0, "final_sec": 42.0,
                  "removed_ratio": 0.3, "cut_count": 5, "applied_count": 5,
                  "confirm_count": 1, "keep_count": 4},
    }
    words = [
        # 도입 (6.0~)
        W(6.0, 6.5, "안녕하세요"), W(6.6, 7.0, "오늘은"), W(7.1, 7.5, "노션"),
        W(7.6, 8.0, "수업을"), W(8.1, 9.0, "시작하겠습니다"),
        # 딱 붙은 반복 → 자동 컷 대상 (첫 발화 9.2~9.7 제거)
        W(9.2, 9.6, "그래서"), W(9.7, 10.1, "그래서"),
        W(10.3, 10.7, "화면을"), W(10.8, 11.2, "봐주세요"),
        # 컷(14.25~) 경계가 이 단어 한가운데를 지난다 → 스냅으로 보호돼야 함
        W(13.9, 14.5, "그렇습니다"),
        # 두 번째 발화 구간 (16.5~)
        W(16.6, 17.0, "다음"), W(17.1, 17.5, "단계로"), W(17.6, 18.3, "넘어갑니다"),
        # 간격 있는 반복(0.6s) → 확인 필요
        W(19.0, 19.3, "이제"), W(19.9, 20.2, "이제"), W(20.3, 20.9, "정리해봅시다"),
        # 세 번째 구간 (29.5~)
        W(29.6, 30.1, "여기까지"), W(30.2, 30.8, "따라오셨으면"),
        W(30.9, 31.5, "성공입니다"),
        # 마무리 (50.5~)
        W(50.6, 51.2, "오늘"), W(51.3, 51.9, "수고"), W(52.0, 52.6, "많으셨습니다"),
    ]
    transcript = {"language": "ko", "language_prob": 0.99, "duration_sec": 60.0,
                  "model": "synthetic", "segments": [], "words": words}
    manifest = {"lecture": {"title": "M2 로직 검증"}, "output_dir": str(out)}
    (out / "edl.json").write_text(
        json.dumps(edl, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "transcript.json").write_text(
        json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    if len(sys.argv) < 2:
        print("사용: test_m2_logic.py <작업폴더>", file=sys.stderr)
        return 2
    out = Path(sys.argv[1]).expanduser().resolve() / "m2"
    build_fixture_files(out)

    print("■ M2-1. refine_cuts (경계 스냅 + 반복 탐지)")
    sh([sys.executable, "refine_cuts.py", str(out / "edl.json")])
    edl = json.loads((out / "edl.json").read_text(encoding="utf-8"))
    cuts = edl["cuts"]

    snapped = [c for c in cuts if c.get("snapped")]
    check("컷 1개가 스냅됨", len(snapped) == 1, f"{len(snapped)}개")
    if snapped:
        # 14.25가 '그렇습니다'(13.9~14.5) 한가운데였으므로 14.5+0.2=14.7로 밀려야 함
        check("경계가 단어 뒤로 밀림 (14.25 → 14.7)",
              abs(snapped[0]["start"] - 14.7) < 0.01,
              f"{snapped[0]['start']}")

    reps = [c for c in cuts if c["kind"] == "repeat"]
    auto_reps = [r for r in reps if r["category"] == "auto"]
    confirm_reps = [r for r in reps if r["category"] == "confirm"]
    check("반복 2곳 탐지", len(reps) == 2, f"{len(reps)}곳")
    check("딱 붙은 반복은 자동", len(auto_reps) == 1
          and abs(auto_reps[0]["start"] - 9.2) < 0.01,
          f"{[r['start'] for r in auto_reps]}")
    check("간격 있는 반복은 확인 필요", len(confirm_reps) == 1
          and abs(confirm_reps[0]["start"] - 19.0) < 0.01,
          f"{[r['start'] for r in confirm_reps]}")
    check("반복 컷은 반복분만 제거 (0.5s)",
          auto_reps and abs(auto_reps[0]["duration"] - 0.5) < 0.01,
          f"{auto_reps[0]['duration'] if auto_reps else '-'}s")

    # 60 - (5.75 + 0.5 + 1.55 + 2.5 + 3.5 + 4.25) = 41.95
    check("보정 후 길이 41.95s", abs(edl["stats"]["final_sec"] - 41.95) < 0.05,
          f"{edl['stats']['final_sec']}s")

    print("■ M2-2. make_subtitles (SRT 재계산 + 대본)")
    sh([sys.executable, "make_subtitles.py", str(out / "edl.json")])
    srt = (out / "subtitles" / "master.srt").read_text(encoding="utf-8")
    script = (out / "final_script.md").read_text(encoding="utf-8")

    check("SRT 생성", srt.strip() != "")
    check("스냅으로 보호된 '그렇습니다'가 자막에 있음", "그렇습니다" in srt)
    check("반복 첫 발화는 자막에서 한 번만",
          srt.count("그래서") == 1, f"{srt.count('그래서')}회")
    check("첫 자막이 0초 부근에서 시작 (00:00:00)", "00:00:00," in srt.split("\n")[1])
    # '여기까지'(원본 29.6s)는 앞의 컷 5개가 제거되어 약 19.3s에 나와야 한다
    blocks = [b for b in srt.split("\n\n") if "여기까지" in b]
    remapped_ok = bool(blocks) and "00:00:19," in blocks[0]
    check("타임코드 재계산 (원본 29.6s → 약 19.3s)", remapped_ok,
          blocks[0].splitlines()[1] if blocks else "블록 없음")
    check("대본 생성 + 안내문", "처음부터 끝까지" in script)
    check("대본에 보호 단어 포함", "그렇습니다" in script)
    check("대본에 마무리 인사 포함", "많으셨습니다" in script)

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
