# 실시간 유튜브 다국어 자막 번역 및 스마트 단어장 시스템

## 개요
유튜브 영상의 오디오를 추출해 Google Cloud Speech-to-Text로 원문 자막을 만들고, 번역한 뒤 PC UI와 Raspberry Pi LCD로 동시에 보여주는 시스템입니다. Chrome 확장 프로그램이 재생 중인 유튜브 URL과 시간을 감지해 백엔드에 보내고, Firestore 단어장과 UDP 제어까지 연결합니다.

## 현재 실행 구조
* `main.py`: 전체 프로그램 진입점입니다. Flask 서버를 먼저 띄우고, PySide6 UI를 실행하며 STT와 번역 작업을 백그라운드에서 연결합니다.
* `flask_server.py`: Chrome 확장 프로그램과 PC/LCD가 공유하는 상태 저장소입니다. 현재 영상, 자막, 재생 시간, 제어 명령, 영상 불일치 상태를 메모리에서 관리합니다.
* `google_cloud_stt.py`: `yt-dlp`와 `ffmpeg`로 오디오를 추출한 뒤 Google Cloud STT로 원문 자막을 생성합니다.
* `google_cloud_translator.py`: STT 결과를 설정된 언어들로 병렬 번역해 최종 자막 데이터로 조립합니다.
* `ui_pyside.py`: 현재 사용하는 메인 UI입니다. 로그인, URL 감지, 추출 시작, 단어장, TTS, UDP 송수신을 담당합니다.
* `db_service.py`: Firestore 로그인/회원가입, 단어장 저장/삭제, 단어 번역, TTS 호출을 담당합니다.
* `udp_service.py`: PC와 LCD 사이의 UDP 통신을 담당합니다. 자막 페이로드 송신과 LCD 명령 수신을 처리합니다.
* `pi_lcd.py`: 실제 Raspberry Pi에서 실행하는 LCD 클라이언트입니다.
* `fake_pi_lcd.py`: PC에서 라즈베리파이 없이 테스트할 때 쓰는 시뮬레이터입니다.
* `content.js` / `manifest.json`: Chrome 확장 프로그램이 YouTube 재생 정보와 URL을 읽어 백엔드로 보냅니다.

## 주요 기능
* 자동 영상 감지 및 타임라인 동기화
* Google Cloud STT 기반 원문 추출
* Google Cloud Translate 기반 다국어 자막 생성
* Raspberry Pi LCD 또는 PC 시뮬레이터로 자막 표시
* LCD에서 단어 저장, 재생, 정지, 탐색 제어
* Firestore 기반 로그인 및 단어장 관리

## 기술 스택
* Python 3, JavaScript
* Google Cloud Speech-to-Text, Google Cloud Translate V3, Google Cloud Text-to-Speech
* Firebase Firestore
* Flask, UDP Socket
* PySide6
* `yt-dlp`, `ffmpeg`

## 실행 요약
1. `python -m pip install -r requirements.txt`
2. `ffmpeg` / `ffprobe` 설치 확인
3. `.env` 와 `credentials/*.json` 확인
4. `python main.py`
5. PC 테스트면 `python fake_pi_lcd.py`, 실제 Pi면 `python pi_lcd.py`
