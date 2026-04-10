# Google Cloud YouTube Subtitle Sync App

이 프로젝트는 Chrome 확장 프로그램이 보내는 YouTube 재생 상태를 기준으로,
로컬 Python 앱이 YouTube 오디오를 받아 Google Cloud Speech-to-Text로 전사하고,
Cloud Translation으로 번역한 뒤 Tkinter 화면에 동기화 자막을 보여주는 예제입니다.

## 필수 환경 변수

### Windows CMD 예시

```bat
set GOOGLE_CLOUD_PROJECT=your-project-id
set GOOGLE_APPLICATION_CREDENTIALS=C:\path\to\service-account.json
set SOURCE_LANGUAGE_CODE=en-US
set TARGET_LANGUAGE_CODE=ko
```

## 설치

```bat
pip install -r requirements.txt
```

ffmpeg 설치 후 PATH 또는 `FFMPEG_PATH` 환경 변수 설정이 필요합니다.

## 실행

```bat
python main.py
```

## 구조 설명

- `browser_sync/`: Chrome 확장 프로그램에서 보내는 재생 상태 수신
- `media/`: YouTube 오디오 URL 해석, ffmpeg PCM 스트리밍
- `stt/`: Google Speech 스트리밍 어댑터
- `translate/`: Google Translation 어댑터
- `subtitle/`: 자막 큐 저장 및 현재 시간 기준 조회
- `session/`: URL 변경 / seek 감지 후 파이프라인 재시작 판단
- `ui/`: Tkinter 표시 전용

## 주의

- 이 코드는 로컬 게이트웨이 방식 예제입니다.
- Speech-to-Text 스트리밍은 gRPC 기반이며, 인증이 올바르게 설정되어야 동작합니다.
- Translation은 최종 자막(`is_final=True`)에 대해서만 호출하도록 되어 있습니다.
