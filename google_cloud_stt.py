import yt_dlp
import os
import glob
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import List, Tuple

from google.cloud import speech

# ==========================================
# Google Cloud STT 기반 자막 추출 엔진
# - 기존 함수명/반환 구조 유지
# - 로컬 개발용 (ADC: gcloud auth application-default login)
# - 긴 오디오는 50초 단위로 잘라서 순차 인식
# ==========================================

@dataclass
class SimpleSegment:
    start: float
    end: float
    text: str


def extract_original_subtitles(youtube_url, status_callback=None) -> Tuple[List[SimpleSegment], float]:
    def update_status(msg, percent):
        print(msg)
        if status_callback:
            status_callback(msg, percent)

    # 설정값
    SOURCE_LANGUAGE = os.getenv("GCP_STT_LANGUAGE", "en-US")
    ALT_LANGS_RAW = os.getenv("GCP_STT_ALTERNATIVE_LANGUAGES", "ko-KR")
    CHUNK_SECONDS = int(os.getenv("GCP_STT_CHUNK_SECONDS", "50"))  # 60초 미만 유지
    SAMPLE_RATE = 16000

    alternative_languages = [
        lang.strip() for lang in ALT_LANGS_RAW.split(",")
        if lang.strip() and lang.strip() != SOURCE_LANGUAGE
    ]

    temp_dir = tempfile.mkdtemp(prefix="yt_stt_")
    original_audio = None
    wav_audio = os.path.join(temp_dir, "full_audio.wav")

    try:
        update_status("\n[1/4] 유튜브 오디오 다운로드 중...", 10)

        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(temp_dir, "temp_audio.%(ext)s"),
            "quiet": True,
            "noplaylist": True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([youtube_url])

        candidates = glob.glob(os.path.join(temp_dir, "temp_audio.*"))
        if not candidates:
            raise RuntimeError("다운로드된 오디오 파일을 찾지 못했습니다.")
        original_audio = candidates[0]

        update_status("[2/4] STT용 WAV 변환 중...", 20)

        # mono 16kHz wav 로 변환
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", original_audio,
                "-ac", "1",
                "-ar", str(SAMPLE_RATE),
                "-vn",
                wav_audio
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        # 전체 길이 조회
        duration = _get_audio_duration(wav_audio)
        if duration <= 0:
            raise RuntimeError("오디오 길이를 확인할 수 없습니다.")

        update_status("[3/4] Google Cloud STT 인식 중...", 30)

        client = speech.SpeechClient()
        segments: List[SimpleSegment] = []

        total_chunks = int((duration + CHUNK_SECONDS - 1) // CHUNK_SECONDS)

        for idx in range(total_chunks):
            start_sec = idx * CHUNK_SECONDS
            chunk_duration = min(CHUNK_SECONDS, duration - start_sec)
            chunk_path = os.path.join(temp_dir, f"chunk_{idx:04d}.wav")

            # ffmpeg로 일정 구간 추출
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

            with open(chunk_path, "rb") as audio_file:
                content = audio_file.read()

            audio = speech.RecognitionAudio(content=content)
            config = speech.RecognitionConfig(
                encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=SAMPLE_RATE,
                language_code=SOURCE_LANGUAGE,
                alternative_language_codes=alternative_languages,
                enable_automatic_punctuation=True,
                enable_word_time_offsets=True,
                model="default",
            )

            response = client.recognize(config=config, audio=audio)

            # 각 result를 기존 segment 형태로 변환
            for result in response.results:
                alt = result.alternatives[0]
                transcript = alt.transcript.strip()
                if not transcript:
                    continue

                # word timestamp가 있으면 start/end 계산
                if alt.words:
                    seg_start = start_sec + _duration_to_seconds(alt.words[0].start_time)
                    seg_end = start_sec + _duration_to_seconds(alt.words[-1].end_time)
                else:
                    # word timestamp가 비어있는 예외 상황 방어
                    seg_start = float(start_sec)
                    seg_end = float(start_sec + chunk_duration)

                segments.append(
                    SimpleSegment(
                        start=round(seg_start, 3),
                        end=round(seg_end, 3),
                        text=transcript
                    )
                )

            progress = 30 + int(((idx + 1) / total_chunks) * 20)
            update_status(f"  - 청크 {idx + 1}/{total_chunks} 인식 완료", progress)

            if os.path.exists(chunk_path):
                os.remove(chunk_path)

        update_status("[4/4] 인식 결과 정리 중...", 55)

        # 혹시 시간 순서가 꼬일 수 있으니 정렬
        segments.sort(key=lambda s: (s.start, s.end))

        # 너무 짧거나 중복된 구간 정리
        segments = _merge_adjacent_segments(segments)

        update_status("✅ Google Cloud STT 원문 추출 완료!", 60)
        # duration은 침묵 구간 포함 전체 길이이므로 UI 진행바/총길이에 사용
        return segments, float(duration)

    except Exception as e:
        raise RuntimeError(f"STT 처리 실패: {e}") from e

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _get_audio_duration(file_path: str) -> float:
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
    Google Cloud STT 응답의 시간 객체를 초(float)로 변환한다.
    환경에 따라 datetime.timedelta 또는 protobuf Duration 형태가 올 수 있다.
    """

    # 1) datetime.timedelta 인 경우
    if hasattr(duration_obj, "total_seconds"):
        return float(duration_obj.total_seconds())

    # 2) protobuf Duration 인 경우
    seconds = getattr(duration_obj, "seconds", 0)
    nanos = getattr(duration_obj, "nanos", 0)
    return float(seconds) + float(nanos) / 1_000_000_000


def _merge_adjacent_segments(segments: List[SimpleSegment]) -> List[SimpleSegment]:
    """
    STT 결과가 지나치게 잘게 끊길 수 있으므로,
    짧고 인접한 결과는 가볍게 병합.
    기존 구조를 크게 바꾸지 않기 위한 후처리.
    """
    if not segments:
        return []

    merged = [segments[0]]

    for current in segments[1:]:
        prev = merged[-1]

        gap = current.start - prev.end
        short_prev = (prev.end - prev.start) < 1.2
        short_curr = (current.end - current.start) < 1.2

        # 가까운 시간대에 붙어 있고 둘 다 매우 짧으면 병합
        if gap <= 0.35 and (short_prev or short_curr):
            prev.text = f"{prev.text} {current.text}".strip()
            prev.end = max(prev.end, current.end)
        else:
            merged.append(current)

    return merged