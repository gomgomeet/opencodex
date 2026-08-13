#!/usr/bin/env python3
"""컷 리스트(EDL) 제안 — analysis.json에서 잘라낼 구간을 정하고 승인용 표를 만든다.

원칙: 여기서는 아무것도 렌더링하지 않는다. 사람이 표를 보고 승인·수정한 뒤에만
render_master.py가 실제로 자른다(비파괴).

되살리기:
    특정 컷을 살리려면 --keep-cuts 3,7 처럼 번호를 넘겨 다시 생성한다.

사용:
    python3 plan_cuts.py <출력폴더>/analysis.json [--min-silence 1.5] [--pad 0.25]
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from lve.common import hhmmss, load_json, save_json

CUT_LABEL = {
    "head": "시작 대기",
    "tail": "종료 여운",
    "silence": "무음",
}


def build_cuts(
    silences: list[dict[str, float]],
    duration: float,
    *,
    min_silence: float,
    pad: float,
    min_cut: float,
    head_threshold: float,
    tail_threshold: float,
) -> list[dict[str, Any]]:
    """무음 목록에서 실제로 잘라낼 구간을 만든다.

    각 무음의 앞뒤로 pad만큼은 남긴다 — 말이 끝나자마자 끊으면 숨 쉴 틈 없이
    급하게 들리기 때문이다. 시작·종료의 긴 공백은 여백 없이 통째로 잘라낸다.
    """
    cuts: list[dict[str, Any]] = []
    for s in silences:
        start, end, dur = s["start"], s["end"], s["duration"]
        is_head = start <= 0.05 and dur >= head_threshold
        is_tail = end >= duration - 0.05 and dur >= tail_threshold

        if is_head:
            kind, c_start, c_end = "head", 0.0, max(0.0, end - pad)
        elif is_tail:
            kind, c_start, c_end = "tail", min(duration, start + pad), duration
        else:
            if dur < min_silence:
                continue  # 짧은 숨 고르기는 남긴다
            kind, c_start, c_end = "silence", start + pad, end - pad

        if c_end - c_start < min_cut:
            continue  # 너무 짧은 컷은 오히려 튀어 보인다
        cuts.append(
            {
                "kind": kind,
                "start": round(c_start, 3),
                "end": round(c_end, 3),
                "duration": round(c_end - c_start, 3),
                "source_silence": {"start": start, "end": end, "duration": dur},
            }
        )
    return cuts


def cuts_to_keeps(
    cuts: list[dict[str, Any]], duration: float, *, min_keep: float
) -> list[dict[str, float]]:
    """컷의 여집합 = 남길 구간. 너무 짧은 조각은 앞 구간에 흡수한다."""
    keeps: list[dict[str, float]] = []
    cursor = 0.0
    for c in sorted(cuts, key=lambda x: x["start"]):
        if c["start"] > cursor:
            keeps.append({"start": round(cursor, 3), "end": round(c["start"], 3)})
        cursor = max(cursor, c["end"])
    if duration - cursor > 0.01:
        keeps.append({"start": round(cursor, 3), "end": round(duration, 3)})

    merged: list[dict[str, float]] = []
    for k in keeps:
        if k["end"] - k["start"] < min_keep and merged:
            merged[-1]["end"] = k["end"]  # 파편은 앞 구간에 붙인다
        elif k["end"] - k["start"] >= min_keep or not merged:
            merged.append(dict(k))
    return [k for k in merged if k["end"] - k["start"] > 0.01]


def render_markdown(edl: dict[str, Any], manifest: dict[str, Any] | None) -> str:
    st = edl["stats"]
    p = edl["params"]
    title = (manifest or {}).get("lecture", {}).get("title") or "(제목 없음)"

    lines = [
        f"# 컷 리스트 제안 — {title}",
        "",
        f"- 원본 길이: **{hhmmss(st['original_sec'])}**",
        f"- 편집 후 예상: **{hhmmss(st['final_sec'])}** "
        f"(−{hhmmss(st['removed_sec'])}, {st['removed_ratio'] * 100:.1f}% 단축)",
        f"- 컷 개수: **{st['cut_count']}개** / 남는 구간 {st['keep_count']}개",
        f"- 기준: 무음 {p['min_silence']}s 이상, 앞뒤 여백 {p['pad']}s, "
        f"임계값 {p['noise']}",
        "",
        "## 잘라낼 구간",
        "",
        "| # | 종류 | 시작 | 끝 | 길이 |",
        "|---|------|------|-----|------|",
    ]
    for i, c in enumerate(edl["cuts"], 1):
        lines.append(
            f"| {i} | {CUT_LABEL.get(c['kind'], c['kind'])} "
            f"| {hhmmss(c['start'], ms=True)} | {hhmmss(c['end'], ms=True)} "
            f"| {c['duration']:.1f}s |"
        )
    if not edl["cuts"]:
        lines.append("| — | 잘라낼 구간이 없습니다 | | | |")

    lines += [
        "",
        "## 승인 방법",
        "",
        "- 이대로 좋으면: **\"승인\"** → 마스터 렌더링을 진행합니다.",
        "- 특정 컷을 살리려면: **\"3번, 7번은 살려줘\"** → 해당 구간을 남기고 다시 제안합니다.",
        "- 기준을 바꾸려면: **\"무음 2초 이상만 잘라줘\"** → 기준을 바꿔 다시 제안합니다.",
        "",
        "> 원본은 수정하지 않습니다. 렌더링 결과는 별도 폴더에 생성됩니다.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="컷 리스트(EDL)를 제안한다")
    ap.add_argument("analysis", type=Path, help="analyze.py가 만든 analysis.json")
    ap.add_argument("--min-silence", type=float, default=1.5,
                    help="이 길이 이상의 무음만 자른다 (기본 1.5초)")
    ap.add_argument("--pad", type=float, default=0.25,
                    help="무음 앞뒤로 남길 여백 (기본 0.25초)")
    ap.add_argument("--min-cut", type=float, default=0.5,
                    help="이보다 짧은 컷은 적용하지 않는다 (기본 0.5초)")
    ap.add_argument("--min-keep", type=float, default=0.4,
                    help="이보다 짧은 잔여 조각은 앞 구간에 흡수 (기본 0.4초)")
    ap.add_argument("--head-threshold", type=float, default=3.0,
                    help="시작부 이만큼 이상 비어 있으면 대기 구간으로 보고 통째로 자른다")
    ap.add_argument("--tail-threshold", type=float, default=3.0,
                    help="종료부 기준 (기본 3초)")
    ap.add_argument("--keep-cuts", default="",
                    help="살릴 컷 번호 (예: 3,7) — 이전 제안의 번호 기준")
    args = ap.parse_args()

    analysis = load_json(args.analysis)
    out_dir = Path(args.analysis).parent
    duration = float(analysis["duration_sec"])

    manifest = None
    mpath = out_dir / "manifest.json"
    if mpath.exists():
        manifest = load_json(mpath)

    cuts = build_cuts(
        analysis["silences"], duration,
        min_silence=args.min_silence,
        pad=args.pad,
        min_cut=args.min_cut,
        head_threshold=args.head_threshold,
        tail_threshold=args.tail_threshold,
    )

    rejected: list[int] = []
    if args.keep_cuts.strip():
        rejected = [
            int(x) for x in args.keep_cuts.replace(" ", "").split(",") if x.isdigit()
        ]
        # 번호는 1부터 — 사용자가 표에서 본 그대로
        cuts = [c for i, c in enumerate(cuts, 1) if i not in rejected]

    keeps = cuts_to_keeps(cuts, duration, min_keep=args.min_keep)
    removed = sum(c["duration"] for c in cuts)
    final = sum(k["end"] - k["start"] for k in keeps)

    edl: dict[str, Any] = {
        "params": {
            "noise": analysis["params"]["noise"],
            "min_silence": args.min_silence,
            "pad": args.pad,
            "min_cut": args.min_cut,
            "min_keep": args.min_keep,
            "kept_cut_indices": rejected,
        },
        "duration_sec": duration,
        "cuts": cuts,
        "keeps": keeps,
        "stats": {
            "original_sec": round(duration, 3),
            "removed_sec": round(removed, 3),
            "final_sec": round(final, 3),
            "removed_ratio": round(removed / duration, 4) if duration else 0.0,
            "cut_count": len(cuts),
            "keep_count": len(keeps),
        },
    }

    edl_path = save_json(out_dir / "edl.json", edl)
    md_path = out_dir / "edl.md"
    md_path.write_text(render_markdown(edl, manifest), encoding="utf-8")

    st = edl["stats"]
    print(f"■ 컷 리스트 제안 — {st['cut_count']}개 컷")
    print(f"  원본      : {hhmmss(st['original_sec'])}")
    print(f"  편집 후   : {hhmmss(st['final_sec'])} "
          f"(−{hhmmss(st['removed_sec'])}, {st['removed_ratio'] * 100:.1f}% 단축)")
    if rejected:
        print(f"  되살린 컷 : {rejected}")
    print(f"  edl.json  : {edl_path}")
    print(f"  승인용 표 : {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
