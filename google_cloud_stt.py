# YouTube 오디오를 내려받아 ffmpeg로 변환한 뒤 Google Cloud STT로 원문 자막을 추출합니다.
import yt_dlp
import os
import glob
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import List, Tuple

from google.cloud import speech
from google_cloud_auth import load_google_credentials, load_google_env

# ==========================================
# Google Cloud STT 기반 자막 추출 엔진
# - 기존 함수명/반환 구조 유지
# - 인증 정보 로딩은 google_cloud_auth.py에서 일괄 처리
# - 긴 오디오는 API 제한을 피하기 위해 50초 단위로 잘라서 순차 인식
# ==========================================

@dataclass
class SimpleSegment:
    """
    STT 결과를 저장하기 위한 간단한 데이터 클래스.
    시작 시간(초), 종료 시간(초), 인식된 텍스트를 담습니다.
    """
    start: float
    end: float
    text: str


def _safe_print(message):
    """
    Windows 콘솔 환경에서 진행 메시지 출력 시 발생할 수 있는 인코딩 에러가
    실제 STT 작업 실패로 이어지지 않도록 방어하는 출력 함수입니다.
    """
    try:
        print(message)
    except UnicodeEncodeError:
        print(str(message).encode("ascii", errors="replace").decode("ascii"))


def extract_original_subtitles(youtube_url, status_callback=None) -> Tuple[List[SimpleSegment], float]:
    """
    유튜브 URL을 받아 오디오를 다운로드하고, Google Cloud STT를 통해
    자막 세그먼트 리스트와 전체 오디오 길이(초)를 반환하는 핵심 함수입니다.
    """
    def update_status(msg, percent):
        # UI 프로그레스 바 업데이트를 위한 콜백 호출 및 콘솔 출력
        _safe_print(msg)
        if status_callback:
            status_callback(msg, percent)

    # 환경변수 로드
    load_google_env()

    # GCP STT 환경 설정값 (언어, 청크 길이, 샘플레이트 등)
    SOURCE_LANGUAGE = os.getenv("GCP_STT_LANGUAGE", "en-US")
    ALT_LANGS_RAW = os.getenv("GCP_STT_ALTERNATIVE_LANGUAGES", "ko-KR")
    CHUNK_SECONDS = int(os.getenv("GCP_STT_CHUNK_SECONDS", "50"))  # 한 번에 처리할 최대 길이 (60초 미만 권장)
    SAMPLE_RATE = 16000

    # 대체 인식 언어 목록 생성 (기본 언어 제외)
    alternative_languages = [
        lang.strip() for lang in ALT_LANGS_RAW.split(",")
        if lang.strip() and lang.strip() != SOURCE_LANGUAGE
    ]

    # 임시 디렉토리 생성 (작업 완료 후 일괄 삭제됨)
    temp_dir = tempfile.mkdtemp(prefix="yt_stt_")
    original_audio = None
    wav_audio = os.path.join(temp_dir, "full_audio.wav")

    try:
        update_status("\n[1/4] 유튜브 오디오 다운로드 중...", 10)

        # yt-dlp 옵션: 최고 음질의 오디오만 추출하여 임시 폴더에 저장
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(temp_dir, "temp_audio.%(ext)s"),
            "quiet": True,
            "noplaylist": True, # 플레이리스트인 경우 단일 영상만 다운로드
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([youtube_url])

        # 다운로드된 실제 파일명 찾기 (확장자가 다를 수 있으므로 glob 사용)
        candidates = glob.glob(os.path.join(temp_dir, "temp_audio.*"))
        if not candidates:
            raise RuntimeError("다운로드된 오디오 파일을 찾지 못했습니다.")
        original_audio = candidates[0]

        update_status("[2/4] STT용 WAV 변환 중...", 20)

        # 다운로드한 오디오를 Google STT가 선호하는 형식(Mono, 16kHz, WAV)으로 ffmpeg를 이용해 변환
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", original_audio,
                "-ac", "1",
                "-ar", str(SAMPLE_RATE),
                "-vn", # 비디오 스트림 무시
                wav_audio
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        # 변환된 WAV 파일의 전체 길이(초) 조회
        duration = _get_audio_duration(wav_audio)
        if duration <= 0:
            raise RuntimeError("오디오 길이를 확인할 수 없습니다.")

        update_status("[3/4] Google Cloud STT 인식 중...", 30)

        # 인증 로딩을 공통 헬퍼에 맡겨 STT/번역 모듈의 인증 경로 일관성 유지
        credentials = load_google_credentials()
        client = speech.SpeechClient(credentials=credentials)
        segments: List[SimpleSegment] = []

        # 오디오를 설정된 청크(예: 50초) 단위로 나누어 처리할 횟수 계산
        total_chunks = int((duration + CHUNK_SECONDS - 1) // CHUNK_SECONDS)

        # 청크 단위로 오디오를 잘라서 순차적으로 STT API에 요청
        for idx in range(total_chunks):
            start_sec = idx * CHUNK_SECONDS
            chunk_duration = min(CHUNK_SECONDS, duration - start_sec)
            chunk_path = os.path.join(temp_dir, f"chunk_{idx:04d}.wav")

            # ffmpeg로 현재 루프에 해당하는 시간만큼 오디오 잘라내기
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-ss", str(start_sec),
                    "-t", str(chunk_duration),
                    "-i", wav_audio,
                    "-ac", "1",
                    "-ar", str(SAMPLE_RATE),
                    "-vn",
                    chunk_path
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

            # 잘라낸 오디오 파일을 읽어 메모리에 적재
            with open(chunk_path, "rb") as audio_file:
                content = audio_file.read()

            audio = speech.RecognitionAudio(content=content)
            config = speech.RecognitionConfig(
                encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=SAMPLE_RATE,
                language_code=SOURCE_LANGUAGE,
                alternative_language_codes=alternative_languages,
                enable_automatic_punctuation=True,  # 자동 구두점(마침표, 쉼표 등) 추가
                enable_word_time_offsets=True,      # 단어별 시작/종료 시간 추출 활성화
                model="default",
            )

            # 동기식 API 호출을 통해 음성 인식 수행
            response = client.recognize(config=config, audio=audio)

            # 응답받은 인식 결과를 SimpleSegment 형태로 파싱
            for result in response.results:
                alt = result.alternatives[0]
                transcript = alt.transcript.strip()
                if not transcript:
                    continue

                # 단어별 타임스탬프가 있으면 첫 단어의 시작과 마지막 단어의 끝을 해당 문장의 시간으로 계산
                if alt.words:
                    seg_start = start_sec + _duration_to_seconds(alt.words[0].start_time)
                    seg_end = start_sec + _duration_to_seconds(alt.words[-1].end_time)
                else:
                    # 타임스탬프 정보가 누락된 예외 상황에 대비하여 현재 청크의 전체 시간을 할당
                    seg_start = float(start_sec)
                    seg_end = float(start_sec + chunk_duration)

                segments.append(
                    SimpleSegment(
                        start=round(seg_start, 3),
                        end=round(seg_end, 3),
                        text=transcript
                    )
                )

            # 진행률 계산 및 UI 업데이트
            progress = 30 + int(((idx + 1) / total_chunks) * 20)
            update_status(f"  - 청크 {idx + 1}/{total_chunks} 인식 완료", progress)

            # 처리 완료된 청크 파일 즉시 삭제하여 디스크 공간 확보
            if os.path.exists(chunk_path):
                os.remove(chunk_path)

        update_status("[4/4] 인식 결과 정리 중...", 55)

        # STT 결과가 시간 순서대로 오지 않을 수 있으므로 시작 시간 기준으로 정렬
        segments.sort(key=lambda s: (s.start, s.end))

        # 너무 잘게 쪼개진 자막(1초 미만 등)이 연속될 경우 하나로 병합 (가독성 향상)
        segments = _merge_adjacent_segments(segments)

        update_status("✅ Google Cloud STT 원문 추출 완료!", 60)
        
        # 반환: 정렬 및 병합된 자막 리스트, 전체 오디오 길이
        return segments, float(duration)

    except Exception as e:
        raise RuntimeError(f"STT 처리 실패: {e}") from e

    finally:
        # 정상 완료든 예외 발생이든 사용한 임시 디렉토리와 파일들을 무조건 정리
        shutil.rmtree(temp_dir, ignore_errors=True)


def _get_audio_duration(file_path: str) -> float:
    """
    ffprobe 도구를 사용하여 오디오 파일의 전체 길이(초 단위)를 정확히 추출합니다.
    """
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            file_path
        ],
        capture_output=True,
        text=True,
        check=True
    )
    return float(result.stdout.strip())


def _duration_to_seconds(duration_obj) -> float:
    """
    Google Cloud STT 응답에 포함된 시간 객체를 실수형(float) 초 단위로 변환합니다.
    구글 클라이언트 라이브러리 버전에 따라 리턴되는 타입(datetime.timedelta 또는 protobuf Duration)이 
    다를 수 있으므로 이를 안전하게 호환 처리합니다.
    """

    # 1) datetime.timedelta 타입으로 들어온 경우
    if hasattr(duration_obj, "total_seconds"):
        return float(duration_obj.total_seconds())

    # 2) protobuf Duration 객체로 들어온 경우
    seconds = getattr(duration_obj, "seconds", 0)
    nanos = getattr(duration_obj, "nanos", 0)
    return float(seconds) + float(nanos) / 1_000_000_000


def _merge_adjacent_segments(segments: List[SimpleSegment]) -> List[SimpleSegment]:
    """
    STT 결과가 말하는 중간에 너무 잘게 끊기면 UI에 텍스트가 번쩍거리며 나타나므로,
    간격이 짧고 인접한 결과는 하나의 자막 덩어리로 부드럽게 병합해줍니다.
    """
    if not segments:
        return []

    merged = [segments[0]]

    for current in segments[1:]:
        prev = merged[-1]

        # 이전 자막의 끝과 현재 자막 시작 사이의 시간 차이
        gap = current.start - prev.end
        # 각 자막 덩어리가 너무 짧은지(1.2초 미만) 확인
        short_prev = (prev.end - prev.start) < 1.2
        short_curr = (current.end - current.start) < 1.2

        # 간격이 매우 짧고(0.35초 이하) 둘 중 하나라도 너무 짧은 자막이라면 문장을 이어 붙임
        if gap <= 0.35 and (short_prev or short_curr):
            prev.text = f"{prev.text} {current.text}".strip()
            prev.end = max(prev.end, current.end)
        else:
            merged.append(current)

    return merged
