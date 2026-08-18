# lecture-video-editor

줌으로 녹화한 강의 영상을 유튜브용 완성본(풀영상 / 20분 분할본 / 쇼츠)으로
편집하는 Claude Code 스킬. 설계 배경과 전체 로드맵은
[PRD](../../docs/prd/lecture-video-editor.md) 참조.

## 설치

이 폴더를 로컬 Claude Code의 스킬 경로에 두면 된다.

```bash
mkdir -p ~/.claude/skills
cp -r skills/lecture-video-editor ~/.claude/skills/
```

의존성은 `ffmpeg`뿐이다(자막 단계부터 `faster-whisper` 추가 예정).

```bash
brew install ffmpeg              # macOS
sudo apt-get install ffmpeg      # Ubuntu
```

## 쓰는 법

Claude Code에서 녹화 폴더를 알려주면 된다.

```
~/Documents/Zoom/2026-08-13 10.00.00 3학년 국어 수업나눔 이 폴더 편집해줘
```

스킬이 알아서 분석하고 **컷 리스트를 표로 보여준 뒤 승인을 기다린다.**
"승인" 하면 마스터를 렌더링하고, "3번은 살려줘" / "무음 2초 이상만 잘라줘"
같은 수정 요청도 받는다.

## 직접 실행 (스크립트)

```bash
cd scripts
python3 ingest.py "~/Documents/Zoom/2026-08-13 10.00.00 강의" -o /tmp/out
python3 analyze.py /tmp/out/manifest.json
python3 plan_cuts.py /tmp/out/analysis.json     # → /tmp/out/edl.md 확인
python3 render_master.py /tmp/out/edl.json --preset master
```

## 산출물

```
<출력폴더>/
  manifest.json            인제스트 결과 (파일 구성·해상도·길이)
  analysis.json            무음 구간 목록
  edl.json / edl.md        컷 리스트 (승인용 표)
  filter_master.txt        렌더에 쓴 ffmpeg 필터
  master/*_master.mp4      마스터 영상
  render_master_log.json   재현용 명령·소요 시간 로그
```

원본은 절대 수정하지 않는다.

## 테스트

실제 녹화 없이 합성 픽스처로 전 과정을 검증한다.

```bash
python3 tests/run_pipeline_test.py /tmp/lve-test
```

## 구현 범위

| 단계 | 기능 | 상태 |
|------|------|------|
| M1 | 줌 인제스트(VFR 감지) · 무음+화면정지 분석 · 컷 분류·승인 · 마스터 렌더 | ✅ 완료 |
| M2 | 한국어 전사(단어 타임스탬프) · 컷 경계 스냅 · 반복 어절 · SRT · 완성 대본 | ✅ 완료 |
| M3 | 20분 분할 · 쇼츠 추출 · 화면 노출 검수 | 예정 |
| M4 | 브랜드 모션 삽입 · 업로드 메타 · 패키징 | 예정 |

자막 단계는 `pip install faster-whisper` 필요 (모든 처리는 로컬, 음성 외부 전송 없음).
