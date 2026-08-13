#!/usr/bin/env python3
"""줌 녹화 인제스트 — 폴더(또는 파일)를 받아 구성 요소를 인식하고 manifest.json을 만든다.

로컬 녹화 폴더 구성을 기준으로 한다:
    zoom_0.mp4 / GMT<날짜>-<시각>_Recording_*.mp4   ← 본 영상
    audio_only.m4a                                  ← 오디오 전용(분석 가속용)
    chat.txt                                        ← 질문 시점 힌트 (배포물에는 미포함)
    *.vtt                                           ← 클라우드 녹화면 존재(전사 시드, 선택)

사용:
    python3 ingest.py <녹화폴더|영상파일> [-o 출력폴더]
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from lve.common import eprint, ffprobe_json, hhmmss, save_json

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".m4v"}
AUDIO_EXT = {".m4a", ".mp3", ".wav", ".aac"}

# 줌 로컬 녹화 파일명 패턴
ZOOM_VIDEO_RE = re.compile(r"^(zoom_\d+|GMT\d{8}-\d{6}_Recording[^/]*)\.(mp4|mov|mkv)$", re.I)
# 폴더명 예: "2026-08-13 10.00.00 3학년 국어 수업 나눔"
ZOOM_DIR_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})[ _](\d{2}[.:]\d{2}[.:]\d{2})\s*(.*)$")


def pick_main_video(candidates: list[Path]) -> Path | None:
    """본 영상 선택 — 줌 패턴을 우선하고, 그중 가장 긴(=큰) 파일을 고른다.

    화면 공유 녹화가 따로 저장되는 경우가 있어 파일이 여러 개일 수 있다.
    """
    if not candidates:
        return None
    zoomish = [p for p in candidates if ZOOM_VIDEO_RE.match(p.name)]
    pool = zoomish or candidates
    return max(pool, key=lambda p: p.stat().st_size)


def parse_zoom_dirname(name: str) -> dict[str, str]:
    m = ZOOM_DIR_RE.match(name.strip())
    if not m:
        return {"date": "", "time": "", "title": name.strip()}
    date, time_raw, title = m.groups()
    return {
        "date": date,
        "time": time_raw.replace(".", ":"),
        "title": title.strip() or name.strip(),
    }


def video_meta(path: Path) -> dict[str, Any]:
    probe = ffprobe_json(path)
    fmt = probe.get("format", {})
    v = next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), {})
    a = next((s for s in probe.get("streams", []) if s.get("codec_type") == "audio"), {})

    fps = 0.0
    raw_fps = v.get("avg_frame_rate") or v.get("r_frame_rate") or "0/0"
    if "/" in raw_fps:
        num, _, den = raw_fps.partition("/")
        try:
            fps = round(float(num) / float(den), 3) if float(den) else 0.0
        except (ValueError, ZeroDivisionError):
            fps = 0.0

    duration = float(fmt.get("duration") or 0.0)
    return {
        "duration_sec": round(duration, 3),
        "duration_hms": hhmmss(duration),
        "width": v.get("width"),
        "height": v.get("height"),
        "fps": fps,
        "video_codec": v.get("codec_name"),
        "audio_codec": a.get("codec_name"),
        "sample_rate": int(a.get("sample_rate") or 0) or None,
        "channels": a.get("channels"),
        "size_bytes": int(fmt.get("size") or 0),
        "has_audio": bool(a),
    }


def ingest(source: Path, out_dir: Path) -> dict[str, Any]:
    source = source.expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"경로를 찾을 수 없습니다: {source}")

    if source.is_file():
        folder = source.parent
        main = source
        siblings = []
    else:
        folder = source
        siblings = [p for p in sorted(folder.iterdir()) if p.is_file()]
        main = pick_main_video([p for p in siblings if p.suffix.lower() in VIDEO_EXT])
        if main is None:
            raise FileNotFoundError(
                f"영상 파일을 찾지 못했습니다: {folder}\n"
                f"  줌 녹화 폴더(zoom_0.mp4 등)를 지정했는지 확인하세요."
            )

    names = {p.name.lower(): p for p in siblings}
    audio_only = names.get("audio_only.m4a") or next(
        (p for p in siblings if p.suffix.lower() in AUDIO_EXT), None
    )
    chat = names.get("chat.txt")
    vtt = next((p for p in siblings if p.suffix.lower() == ".vtt"), None)
    extra_videos = [
        p for p in siblings if p.suffix.lower() in VIDEO_EXT and p != main
    ]

    meta = video_meta(main)
    if not meta["has_audio"]:
        eprint("경고: 영상에 오디오 트랙이 없습니다. 무음 분석이 불가능합니다.")

    naming = parse_zoom_dirname(folder.name)
    recording_kind = "cloud" if vtt else "local"

    manifest = {
        "source": {
            "folder": str(folder),
            "main_video": str(main),
            # 오디오 전용 파일이 있으면 분석에 쓴다 — 디코딩이 훨씬 빠르다
            "audio_only": str(audio_only) if audio_only else None,
            "chat_txt": str(chat) if chat else None,
            "vtt": str(vtt) if vtt else None,
            "extra_videos": [str(p) for p in extra_videos],
            "recording_kind": recording_kind,
        },
        "lecture": naming,
        "media": meta,
        "output_dir": str(out_dir),
    }

    manifest_path = out_dir / "manifest.json"
    save_json(manifest_path, manifest)
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description="줌 녹화 폴더를 인제스트한다")
    ap.add_argument("source", type=Path, help="줌 녹화 폴더 또는 영상 파일")
    ap.add_argument("-o", "--out", type=Path, default=None, help="산출물 폴더")
    args = ap.parse_args()

    src = args.source.expanduser().resolve()
    base = src if src.is_dir() else src.parent
    out_dir = (args.out or base / "_lve_output").expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    m = ingest(src, out_dir)
    media, lec, s = m["media"], m["lecture"], m["source"]

    print(f"■ 인제스트 완료 — {lec['title'] or '(제목 없음)'}")
    print(f"  본 영상   : {Path(s['main_video']).name}")
    print(f"  길이      : {media['duration_hms']}  ({media['duration_sec']}s)")
    print(f"  해상도    : {media['width']}x{media['height']} @ {media['fps']}fps")
    print(f"  코덱      : v={media['video_codec']} / a={media['audio_codec']}")
    print(f"  녹화 종류 : {s['recording_kind']}")
    if s["audio_only"]:
        print(f"  오디오전용: {Path(s['audio_only']).name} (분석 가속에 사용)")
    if s["chat_txt"]:
        print(f"  채팅 로그 : {Path(s['chat_txt']).name} (질문 시점 힌트, 배포물 미포함)")
    if s["vtt"]:
        print(f"  VTT       : {Path(s['vtt']).name} (전사 보조)")
    if s["extra_videos"]:
        print(f"  추가 영상 : {len(s['extra_videos'])}개 (본 영상에서 제외됨)")
    print(f"  manifest  : {Path(m['output_dir']) / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
