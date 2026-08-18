#!/usr/bin/env python3
"""컷 정밀 보정 — 전사(단어 타임스탬프)로 EDL을 다듬는다.

두 가지를 한다:

B3 · 컷 경계 단어 스냅
    한국어는 어미("~습니다")를 작게 발음해서 무음 검출이 말끝을 먹는다.
    컷 경계가 단어 한가운데를 지나면, 경계를 단어 밖으로 밀고 여유(기본 0.2s)를
    남긴다. "그렇습니다"가 "그렇니다"로 잘리는 것을 막는다.

B5 · 반복 어절 탐지
    말을 더듬어 같은 말을 두 번 한 곳(인접 1~6어절 반복)을 찾아 첫 번째 발화를
    컷으로 제안한다. 딱 붙은 정확한 반복만 자동, 간격이 있으면 '확인 필요'.
    반복된 부분만 자른다 — 문장 전체를 지우지 않는다.

사용:
    python3 refine_cuts.py <출력폴더>/edl.json [--snap-margin 0.2]
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from lve.common import hhmmss, load_json, save_json
from plan_cuts import cuts_to_keeps, render_markdown

PUNCT_RE = re.compile(r"[^\w가-힣]+")


def snap_cut_to_words(
    cut: dict[str, Any], words: list[dict[str, Any]], *, margin: float
) -> tuple[bool, list[str]]:
    """컷 경계가 단어를 자르면 경계를 단어 밖으로 민다. (수정 여부, 로그) 반환."""
    notes: list[str] = []
    moved = False
    for w in words:
        # 컷 시작이 단어 한가운데 → 단어가 끝난 뒤로 민다 (단어를 살린다)
        if w["start"] < cut["start"] < w["end"]:
            new_start = round(w["end"] + margin, 3)
            notes.append(
                f"시작 {hhmmss(cut['start'], ms=True)} → {hhmmss(new_start, ms=True)}"
                f" ('{w['word']}' 보호)"
            )
            cut["start"] = new_start
            moved = True
        # 컷 끝이 단어 한가운데 → 단어가 시작하기 전으로 당긴다
        if w["start"] < cut["end"] < w["end"]:
            new_end = round(w["start"] - margin, 3)
            notes.append(
                f"끝 {hhmmss(cut['end'], ms=True)} → {hhmmss(new_end, ms=True)}"
                f" ('{w['word']}' 보호)"
            )
            cut["end"] = new_end
            moved = True
    if moved:
        cut["duration"] = round(cut["end"] - cut["start"], 3)
        cut["snapped"] = True
    return moved, notes


def norm(word: str) -> str:
    return PUNCT_RE.sub("", word).lower()


def find_repetitions(
    words: list[dict[str, Any]], *, max_ngram: int, max_gap: float
) -> list[dict[str, Any]]:
    """인접 반복 어절을 찾는다. 긴 반복을 우선하고, 겹치는 제안은 만들지 않는다.

    컷 범위는 [첫 발화 시작, 두 번째 발화 시작) — 반복된 부분만 잘리고 두 번째
    (보통 말을 고쳐 한 쪽)가 남는다.
    """
    reps: list[dict[str, Any]] = []
    used: set[int] = set()
    n_words = len(words)
    for n in range(max_ngram, 0, -1):
        i = 0
        while i + 2 * n <= n_words:
            if any(j in used for j in range(i, i + 2 * n)):
                i += 1
                continue
            first = [norm(w["word"]) for w in words[i:i + n]]
            second = [norm(w["word"]) for w in words[i + n:i + 2 * n]]
            if first == second and all(first):
                gap = words[i + n]["start"] - words[i + n - 1]["end"]
                if gap <= max_gap:
                    # 딱 붙은 반복(0.3s 이내)만 자동, 그 외엔 확인 필요
                    category = "auto" if gap <= 0.3 else "confirm"
                    text = " ".join(w["word"] for w in words[i:i + n])
                    reps.append(
                        {
                            "kind": "repeat",
                            "category": category,
                            "start": round(words[i]["start"], 3),
                            "end": round(words[i + n]["start"], 3),
                            "duration": round(
                                words[i + n]["start"] - words[i]["start"], 3
                            ),
                            "repeat_text": text,
                            "repeat_gap": round(gap, 3),
                        }
                    )
                    used.update(range(i, i + 2 * n))
                    i += 2 * n
                    continue
            i += 1
    return sorted(reps, key=lambda r: r["start"])


def main() -> int:
    ap = argparse.ArgumentParser(description="전사로 컷 경계 스냅 + 반복 어절 탐지")
    ap.add_argument("edl", type=Path, help="plan_cuts.py가 만든 edl.json")
    ap.add_argument("--snap-margin", type=float, default=0.2,
                    help="단어 보호 여유(초, 기본 0.2)")
    ap.add_argument("--max-ngram", type=int, default=6,
                    help="반복 탐지 최대 어절 수 (기본 6)")
    ap.add_argument("--max-gap", type=float, default=1.0,
                    help="반복으로 볼 최대 간격(초, 기본 1.0)")
    ap.add_argument("--no-repeats", action="store_true", help="반복 탐지를 건너뛴다")
    ap.add_argument("--min-cut", type=float, default=0.5,
                    help="스냅 후 이보다 짧아진 컷은 버린다 (기본 0.5)")
    args = ap.parse_args()

    out_dir = Path(args.edl).parent
    edl = load_json(args.edl)
    transcript_path = out_dir / "transcript.json"
    if not transcript_path.exists():
        raise SystemExit(
            f"transcript.json이 없습니다: {transcript_path}\n"
            "먼저 transcribe.py를 실행하세요."
        )
    transcript = load_json(transcript_path)
    words = transcript.get("words") or []
    if not words:
        raise SystemExit("전사에 단어 타임스탬프가 없습니다.")

    manifest = None
    mpath = out_dir / "manifest.json"
    if mpath.exists():
        manifest = load_json(mpath)

    duration = float(edl["duration_sec"])
    cuts: list[dict[str, Any]] = edl["cuts"]

    # B3 — 컷 경계 스냅
    snap_notes: list[str] = []
    snapped = 0
    for c in cuts:
        moved, notes = snap_cut_to_words(c, words, margin=args.snap_margin)
        if moved:
            snapped += 1
            snap_notes.extend(notes)
    # 스냅으로 너무 짧아졌거나 뒤집힌 컷은 버린다
    before = len(cuts)
    cuts = [c for c in cuts if c["end"] - c["start"] >= args.min_cut]
    dropped = before - len(cuts)

    # B5 — 반복 어절
    reps: list[dict[str, Any]] = []
    if not args.no_repeats:
        reps = find_repetitions(
            words, max_ngram=args.max_ngram, max_gap=args.max_gap
        )
        for r in reps:
            r["applied"] = r["category"] == "auto"
        cuts = sorted(cuts + reps, key=lambda c: c["start"])

    applied = [c for c in cuts if c.get("applied")]
    keeps = cuts_to_keeps(applied, duration, min_keep=0.4)
    removed = sum(c["duration"] for c in applied)
    final = sum(k["end"] - k["start"] for k in keeps)

    edl["cuts"] = cuts
    edl["keeps"] = keeps
    edl["params"]["snap_margin"] = args.snap_margin
    edl["params"]["refined"] = True
    edl["stats"].update(
        {
            "removed_sec": round(removed, 3),
            "final_sec": round(final, 3),
            "removed_ratio": round(removed / duration, 4) if duration else 0.0,
            "cut_count": len(cuts),
            "applied_count": len(applied),
            "confirm_count": sum(1 for c in cuts if c["category"] == "confirm"),
            "keep_count": len(keeps),
            "snapped_cuts": snapped,
            "dropped_by_snap": dropped,
            "repeat_cuts": len(reps),
        }
    )

    save_json(args.edl, edl)
    (out_dir / "edl.md").write_text(render_markdown(edl, manifest), encoding="utf-8")

    st = edl["stats"]
    print(f"■ 컷 정밀 보정 — 단어 {len(words)}개 기준")
    print(f"  경계 스냅 : {snapped}개 컷 조정"
          + (f", {dropped}개는 스냅 후 짧아져 제외" if dropped else ""))
    for note in snap_notes[:8]:
        print(f"    · {note}")
    if reps:
        auto_r = sum(1 for r in reps if r["category"] == "auto")
        print(f"  반복 어절 : {len(reps)}곳 (자동 {auto_r} / 확인 {len(reps) - auto_r})")
        for r in reps[:8]:
            print(f"    · {hhmmss(r['start'], ms=True)} \"{r['repeat_text']}\""
                  f" ({CATEGORY_KO[r['category']]})")
    print(f"  편집 후   : {hhmmss(st['final_sec'])} "
          f"(적용 {st['applied_count']} / 확인 필요 {st['confirm_count']})")
    print(f"  edl.json  : {args.edl} (갱신)")
    return 0


CATEGORY_KO = {"auto": "자동", "confirm": "확인 필요"}

if __name__ == "__main__":
    raise SystemExit(main())
