#!/usr/bin/env python3
"""테스트용 가짜 줌 녹화 폴더를 만든다.

실제 강의 녹화의 구조를 흉내낸다:
  - 시작부 대기 구간(무음)
  - 발화 / 무음이 번갈아 나오는 본문
  - 종료부 여운(무음)
  - zoom_0.mp4 + audio_only.m4a + chat.txt

사용: python3 make_fixture.py <출력폴더>
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# (구간 길이, 발화 여부) — 총 60초
TIMELINE = [
    (6.0, False),   # 입장 대기
    (8.0, True),    # 인사·도입
    (2.5, False),   # 무음
    (10.0, True),   # 본론 1
    (3.0, False),   # 무음
    (7.0, True),    # 본론 2
    (1.0, False),   # 짧은 숨 고르기 — 잘리면 안 된다
    (9.0, True),    # 본론 3
    (4.0, False),   # 무음
    (5.0, True),    # 마무리
    (4.5, False),   # 종료 여운
]
FPS = 30
TONE_HZ = 330


def build_audio_filter() -> tuple[str, float]:
    """발화 구간엔 톤, 무음 구간엔 침묵을 넣는 필터를 만든다."""
    parts, labels, cursor = [], [], 0.0
    for i, (dur, speaking) in enumerate(TIMELINE):
        label = f"a{i}"
        if speaking:
            # 단조로운 톤은 비현실적이라 진폭을 흔들어 말소리처럼 만든다
            parts.append(
                f"sine=frequency={TONE_HZ}:duration={dur}:sample_rate=48000,"
                f"volume='0.5+0.4*sin(2*PI*t*3)':eval=frame[{label}]"
            )
        else:
            parts.append(f"anullsrc=r=48000:cl=mono,atrim=duration={dur}[{label}]")
        labels.append(f"[{label}]")
        cursor += dur
    graph = ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(TIMELINE)}:v=0:a=1[aout]"
    return graph, cursor


def main() -> int:
    if len(sys.argv) < 2:
        print("사용: make_fixture.py <출력폴더>", file=sys.stderr)
        return 2
    out = Path(sys.argv[1]).expanduser().resolve()
    # 실제 줌 폴더명 규칙을 따른다
    folder = out / "2026-08-13 10.00.00 테스트 강의 - 무음 컷 검증"
    folder.mkdir(parents=True, exist_ok=True)

    graph, total = build_audio_filter()
    video = folder / "zoom_0.mp4"

    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-y",
            "-f", "lavfi", "-i",
            f"testsrc=size=1280x720:rate={FPS}:duration={total}",
            "-filter_complex", graph,
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
            "-t", str(total), str(video),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-y", "-i", str(video),
         "-vn", "-c:a", "aac", "-b:a", "128k", str(folder / "audio_only.m4a")],
        check=True,
        capture_output=True,
    )
    (folder / "chat.txt").write_text(
        "10:05:12 From 참가자 : 선생님 화면 잘 보입니다\n"
        "10:22:40 From 참가자 : 질문 있습니다\n",
        encoding="utf-8",
    )

    speech = sum(d for d, s in TIMELINE if s)
    print(f"픽스처 생성: {folder}")
    print(f"  총 {total:.1f}s / 발화 {speech:.1f}s / 무음 {total - speech:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
