# 실시간 유튜브 다국어 자막 번역 및 스마트 단어장 시스템

## 1. 프로젝트 개요
유튜브 영상의 음성을 추출하여 실시간으로 다국어 자막을 생성하고, 별도의 디스플레이(Raspberry Pi LCD)를 통해 자막 시청 및 단어 학습을 지원하는 통합 시스템입니다. Chrome 확장 프로그램으로 시청 중인 영상을 감지하고, GCP(Google Cloud Platform)를 활용해 STT 및 번역 파이프라인을 구동합니다.

## 2. 주요 기능
* **자동 영상 감지 및 동기화**: Chrome 확장 프로그램이 유튜브 URL과 재생 타임라인을 주기적으로 백엔드로 전송하여 영상과 자막을 완벽히 동기화합니다.
* **클라우드 기반 STT 및 다국어 번역**: `yt-dlp`로 추출한 오디오를 GCP Speech-to-Text로 원문 변환 후, GCP Translation API를 활용해 5개 국어(한, 영, 일, 중, 독)로 병렬 번역합니다.
* **독립적인 자막 디스플레이 (Raspberry Pi)**: 메인 모니터를 가리지 않도록 별도의 외부 LCD를 통해 자막을 출력하며, 오버헤드가 적은 UDP 소켓 통신을 사용해 지연 시간을 최소화합니다.
* **인터랙티브 제어 및 단어장 (Voca Login)**: 파이 화면에서 자막을 드래그해 단어를 선택하면 즉시 번역되어 개인 Firestore DB 단어장에 저장됩니다. 또한 UI 프로그레스 바를 통한 영상 탐색(Seek) 및 재생/정지 제어를 역으로 PC에 명령할 수 있습니다.

## 3. 시스템 아키텍처 및 핵심 모듈
* **PC 백엔드 및 프로세싱 (Python)**
  * `main.py` / `flask_server.py`: 메인 프로세스 진입점 및 확장 프로그램과의 HTTP 통신, 인메모리 상태(SharedState) 동기화.
  * `google_cloud_stt.py` / `google_cloud_translator.py`: 긴 오디오의 50초 단위 청크 분할 인식 및 멀티스레드(ThreadPoolExecutor) 기반 병렬 번역 엔진.
* **PC 프론트엔드 및 데이터 관리 (Tkinter)**
  * `ui_app.py` / `db_service.py` / `udp_service.py`: URL 처리 진행률 모니터링, Firestore 인증(로그인/회원가입), 단어장 UI 제공 및 UDP 브로드캐스팅.
* **외부 디스플레이 클라이언트 (Python / Tkinter)**
  * `pi_lcd.py` / `fake_pi_lcd.py`: UDP로 수신한 자막 페이로드를 렌더링하고, 사용자의 터치/드래그 이벤트를 파싱하여 제어 명령으로 PC에 송신.
* **크롬 확장 프로그램 (JavaScript)**
  * `content.js` / `manifest.json`: 브라우저 내 유튜브 DOM 객체에 직접 접근하여 재생 시간과 URL을 추출.

## 4. 기술 스택 (Tech Stack)
* **Language**: Python 3, JavaScript
* **Cloud & AI**: Google Cloud Speech-to-Text, Google Cloud Translate V3
* **Database**: Firebase Firestore (NoSQL Document DB)
* **Network**: Flask (REST), Socket (UDP)
* **Media Processing**: `yt-dlp`, `ffmpeg`
* **GUI**: Tkinter
