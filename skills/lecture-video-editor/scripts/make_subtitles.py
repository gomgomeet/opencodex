#!/usr/bin/env python3
"""자막 + 완성 대본 — 전사를 마스터 타임라인으로 옮겨 SRT와 검수용 대본을 만든다.

컷으로 사라진 시간만큼 타임코드를 다시 계산한다. 원본 시각 t의 단어는
마스터에서 t' = (t가 속한 keep의 출력 시작) + (t - keep 시작) 에 나타난다.
컷 안의 단어는 자막에서 빠진다.

산출물:
  subtitles/master.srt   마스터 타임라인 자막
  final_script.md        완성본 대본 (렌더 전 사람이 처음부터 끝까지 읽고 검수 — B6)

사용:
    python3 make_subtitles.py <출력폴더>/edl.json [--max-chars 32] [--max-dur 5.0]
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from lve.common import hhmmss, load_json


def srt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    ms = int(round((seconds - int(seconds)) * 1000))
    total = int(seconds)
    if ms == 1000:
        ms, total = 0, total + 1
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


class TimeMapper:
    """원본 시각 → 마스터(컷 적용 후) 시각 매핑."""

    def __init__(self, keeps: list[dict[str, float]]):
        self.keeps = sorted(keeps, key=lambda k: k["start"])
        self.out_starts: list[float] = []
        cursor = 0.0
        for k in self.keeps:
            self.out_starts.append(cursor)
            cursor += k["end"] - k["start"]
        self.total = cursor

    def map(self, t: float) -> float | None:
        """컷 안이면 None."""
        for k, out0 in zip(self.keeps, self.out_starts):
            if k["start"] <= t <= k["end"]:
                return out0 + (t - k["start"])
        return None

    def word_visible(self, w: dict[str, Any]) -> bool:
        """단어 중심이 keep 안에 있으면 살아남은 것으로 본다."""
        mid = (w["start"] + w["end"]) / 2
        return self.map(mid) is not None


def build_captions(
    words: list[dict[str, Any]],
    mapper: TimeMapper,
    *,
    max_chars: int,
    max_dur: float,
    gap_break: float,
) -> list[dict[str, Any]]:
    """살아남은 단어를 자막 줄로 묶는다."""
    captions: list[dict[str, Any]] = []
    line: list[tuple[float, float, str]] = []  # (out_start, out_end, word)

    def flush() -> None:
        if not line:
            return
        captions.append(
            {
                "start": line[0][0],
                "end": line[-1][1],
                "text": " ".join(w for _, _, w in line),
            }
        )
        line.clear()

    for w in words:
        if not mapper.word_visible(w):
            continue
        mid = (w["start"] + w["end"]) / 2
        base = mapper.map(mid)
        if base is None:
            continue
        half = (w["end"] - w["start"]) / 2
        out_s, out_e = base - half, base + half

        if line:
            cur_len = sum(len(t) + 1 for _, _, t in line) + len(w["word"])
            too_long = cur_len > max_chars
            too_slow = out_e - line[0][0] > max_dur
            gapped = out_s - line[-1][1] > gap_break
            if too_long or too_slow or gapped:
                flush()
        line.append((out_s, out_e, w["word"]))
    flush()

    # 자막이 겹치지 않게 최소 간격을 강제한다
    for a, b in zip(captions, captions[1:]):
        if a["end"] > b["start"]:
            a["end"] = max(a["start"] + 0.2, b["start"] - 0.05)
    return captions


def write_srt(captions: list[dict[str, Any]], path: Path) -> None:
    lines: list[str] = []
    for i, c in enumerate(captions, 1):
        lines += [str(i), f"{srt_time(c['start'])} --> {srt_time(c['end'])}",
                  c["text"], ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def write_script(
    captions: list[dict[str, Any]],
    path: Path,
    *,
    title: str,
    total: float,
    para_gap: float,
) -> None:
    """검수용 완성 대본 — 렌더 전에 처음부터 끝까지 눈으로 읽는다 (B6)."""
    lines = [
        f"# 완성본 대본 — {title}",
        "",
        f"> 편집 후 길이 {hhmmss(total)} · 자막 {len(captions)}줄",
        "> 렌더 전에 처음부터 끝까지 읽고 이상한 곳(끊긴 문장, 남은 반복,",
        "> 잘못 잘린 말끝)이 없는지 확인하세요.",
        "",
    ]
    para: list[str] = []
    last_end = None
    for c in captions:
        if last_end is not None and c["start"] - last_end > para_gap and para:
            lines.append(" ".join(para))
            lines.append("")
            para = []
        if not para:
            lines.append(f"**[{hhmmss(c['start'])}]**")
        para.append(c["text"])
        last_end = c["end"]
    if para:
        lines.append(" ".join(para))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="마스터 타임라인 SRT + 검수용 대본 생성")
    ap.add_argument("edl", type=Path, help="edl.json (refine_cuts.py 이후 권장)")
    ap.add_argument("--max-chars", type=int, default=32, help="자막 한 줄 최대 글자")
    ap.add_argument("--max-dur", type=float, default=5.0, help="자막 한 줄 최대 초")
    ap.add_argument("--gap-break", type=float, default=0.8,
                    help="이 이상 침묵이면 줄을 나눈다")
    ap.add_argument("--para-gap", type=float, default=2.0,
                    help="대본에서 문단을 나누는 침묵 길이")
    args = ap.parse_args()

    out_dir = Path(args.edl).parent
    edl = load_json(args.edl)
    transcript = load_json(out_dir / "transcript.json")
    words = transcript.get("words") or []
    if not words:
        raise SystemExit("전사에 단어가 없습니다. transcribe.py를 먼저 실행하세요.")

    title = "(제목 없음)"
    mpath = out_dir / "manifest.json"
    if mpath.exists():
        title = load_json(mpath).get("lecture", {}).get("title") or title

    mapper = TimeMapper(edl["keeps"])
    captions = build_captions(
        words, mapper,
        max_chars=args.max_chars, max_dur=args.max_dur, gap_break=args.gap_break,
    )
    dropped = sum(1 for w in words if not mapper.word_visible(w))

    srt_path = out_dir / "subtitles" / "master.srt"
    write_srt(captions, srt_path)
    script_path = out_dir / "final_script.md"
    write_script(captions, script_path, title=title, total=mapper.total,
                 para_gap=args.para_gap)

    print(f"■ 자막 + 대본 생성 — {title}")
    print(f"  단어      : {len(words)}개 (컷으로 제외 {dropped}개)")
    print(f"  자막      : {len(captions)}줄 → {srt_path}")
    print(f"  대본      : {script_path}")
    print("  ※ 렌더 전에 대본을 처음부터 끝까지 읽고 검수하세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
