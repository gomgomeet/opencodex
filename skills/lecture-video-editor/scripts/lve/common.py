"""공통 유틸 — ffmpeg/ffprobe 실행, 시간 포맷, JSON 입출력."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


class ToolMissing(RuntimeError):
    pass


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise ToolMissing(
            f"'{name}' 을(를) 찾을 수 없습니다. 설치 후 다시 실행하세요.\n"
            f"  macOS:  brew install ffmpeg\n"
            f"  Ubuntu: sudo apt-get install ffmpeg"
        )
    return path


def run(cmd: list[str], *, capture: bool = True) -> subprocess.CompletedProcess:
    """ffmpeg/ffprobe 실행. 실패 시 stderr를 그대로 올려 원인을 감추지 않는다."""
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-15:]
        raise RuntimeError(
            "명령 실패: " + " ".join(cmd[:6]) + " ...\n" + "\n".join(tail)
        )
    return proc


def ffprobe_json(path: Path) -> dict[str, Any]:
    require_tool("ffprobe")
    proc = run(
        [
            "ffprobe", "-v", "error",
            "-print_format", "json",
            "-show_format", "-show_streams",
            str(path),
        ]
    )
    return json.loads(proc.stdout)


def hhmmss(seconds: float, *, ms: bool = False) -> str:
    """초 → HH:MM:SS(.mmm). 음수는 0으로 클램프한다."""
    seconds = max(0.0, float(seconds))
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if ms:
        frac = int(round((seconds - total) * 1000))
        if frac == 1000:  # 반올림이 초를 넘기면 한 칸 올린다
            frac, s = 0, s + 1
            if s == 60:
                s, m = 0, m + 1
                if m == 60:
                    m, h = 0, h + 1
        return f"{h:02d}:{m:02d}:{s:02d}.{frac:03d}"
    return f"{h:02d}:{m:02d}:{s:02d}"


def mmss(seconds: float) -> str:
    """유튜브 챕터용 — 1시간 미만이면 M:SS, 이상이면 H:MM:SS."""
    seconds = max(0.0, float(seconds))
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_json(path: Path, data: Any) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def eprint(*args: Any) -> None:
    print(*args, file=sys.stderr)
