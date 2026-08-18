#!/usr/bin/env python3
"""테스트용 가짜 줌 녹화 폴더를 만든다.

실제 강의 녹화의 구조를 흉내낸다:
  - 시작부 대기 구간(무음 + 화면 정지)
  - 발화(움직이는 화면) / 무음이 번갈아 나오는 본문
  - 무음 중 하나는 '화면 시연 중'(말 없이 화면만 움직임) — 잘리면 안 되는 구간
  - 종료부 여운(무음 + 화면 정지)
  - zoom_0.mp4 + audio_only.m4a + chat.txt

사용: python3 make_fixture.py <출력폴더>
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# (구간 길이, 발화 여부, 화면 움직임 여부) — 총 60초
# 무음+화면정지 → 자동 컷 대상 / 무음+화면움직임 → '시연 중'이라 확인 필요
TIMELINE = [
    (6.0, False, False),   # 입장 대기 (정지 화면)
    (8.0, True, True),     # 인사·도입
    (2.5, False, False),   # 무음 + 정지 → 자동 컷
    (10.0, True, True),    # 본론 1
    (3.0, False, True),    # 무음이지만 화면 시연 중 → 확인 필요(C)
    (7.0, True, True),     # 본론 2
    (1.0, False, False),   # 짧은 숨 고르기 — 잘리면 안 된다
    (9.0, True, True),     # 본론 3
    (4.0, False, False),   # 무음 + 정지 → 자동 컷
    (5.0, True, True),     # 마무리
    (4.5, False, False),   # 종료 여운 (정지 화면)
]
FPS = 30
SIZE = "1280x720"
TONE_HZ = 330


def build_filter() -> tuple[str, float]:
    """구간별 오디오(톤/무음)와 비디오(testsrc/정지 색면)를 만들어 concat한다."""
    parts, labels, total = [], [], 0.0
    for i, (dur, speaking, moving) in enumerate(TIMELINE):
        a, v = f"a{i}", f"v{i}"
        if speaking:
            parts.append(
                f"sine=frequency={TONE_HZ}:duration={dur}:sample_rate=48000,"
                f"volume='0.5+0.4*sin(2*PI*t*3)':eval=frame[{a}]"
            )
        else:
            parts.append(f"anullsrc=r=48000:cl=mono,atrim=duration={dur}[{a}]")
        if moving:
            parts.append(f"testsrc=size={SIZE}:rate={FPS}:duration={dur}[{v}]")
        else:
            # 완전 정지 화면 — freezedetect가 잡아야 한다
            parts.append(f"color=c=0x8898a8:size={SIZE}:rate={FPS}:duration={dur}[{v}]")
        labels.append(f"[{v}][{a}]")
        total += dur
    graph = (
        ";".join(parts) + ";" + "".join(labels)
        + f"concat=n={len(TIMELINE)}:v=1:a=1[vout][aout]"
    )
    return graph, total


def main() -> int:
    if len(sys.argv) < 2:
        print("사용: make_fixture.py <출력폴더>", file=sys.stderr)
        return 2
    out = Path(sys.argv[1]).expanduser().resolve()
    folder = out / "2026-08-13 10.00.00 테스트 강의 - 무음 컷 검증"
    folder.mkdir(parents=True, exist_ok=True)

    graph, total = build_filter()
    video = folder / "zoom_0.mp4"

    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-y",
            "-filter_complex", graph,
            "-map", "[vout]", "-map", "[aout]",
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

    speech = sum(d for d, s, _ in TIMELINE if s)
    frozen = sum(d for d, s, m in TIMELINE if not s and not m)
    demo = sum(d for d, s, m in TIMELINE if not s and m)
    print(f"픽스처 생성: {folder}")
    print(f"  총 {total:.1f}s / 발화 {speech:.1f}s / 무음+정지 {frozen:.1f}s"
          f" / 무음+시연 {demo:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
