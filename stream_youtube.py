import whisper
import subprocess
import numpy as np

# 유튜브 영상 URL
url = "https://www.youtube.com/watch?v=v4t0E3S1N1k&pp=ygUVMSBtaW51dGUgY29udmVyc2F0aW9u"

# Whisper 모델 로드
model = whisper.load_model("tiny")

# 유튜브 스트림 → ffmpeg
cmd = f'yt-dlp -f ba/b -o - "{url}" | ffmpeg -i pipe:0 -f s16le -ac 1 -ar 16000 pipe:1'

# subprocess로 위 명령어 실행
# stdout=subprocess.PIPE → 프로그램 출력 데이터를 파이썬으로 가져오기
# shell=True → 문자열 명령어를 쉘에서 실행
process = subprocess.Popen(cmd, stdout=subprocess.PIPE, shell=True)

# ffmpeg에서 출력된 오디오 데이터를 전부 읽어옴
# 이 시점에서 audio는 raw 바이너리 오디오 데이터
audio = process.stdout.read()

# numpy 배열로 변환
# int16 → float32 변환 후 -1 ~ 1 범위로 정규화
audio_np = np.frombuffer(audio, np.int16).astype(np.float32) / 32768.0

# Whisper에 오디오 데이터 전달해서 음성 인식 수행
# fp16=False → CPU 환경에서 실행하도록 설정
result = model.transcribe(audio_np, fp16=False)

# Whisper 결과에는 여러 개의 segment가 있음
# 각 segment는 "시작시간 / 끝시간 / 인식된 문장" 정보를 가지고 있음
for seg in result["segments"]:
    
    # 문장이 시작되는 시간 (초 단위)
    start = seg["start"]
    
    # 문장이 끝나는 시간 (초 단위)
    end = seg["end"]
    
    # 인식된 텍스트
    text = seg["text"]
    
    # 출력 형식
    # 예: 0.00s ~ 2.31s : Hello everyone
    print(f"{start:.2f}s ~ {end:.2f}s : {text}")

    