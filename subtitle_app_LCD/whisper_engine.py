from faster_whisper import WhisperModel
import yt_dlp
import os
import glob

def extract_original_subtitles(youtube_url, status_callback=None):
    def update_status(msg, percent):
        print(msg)
        if status_callback:
            status_callback(msg, percent)

    update_status("\n[1/2] 유튜브 오디오 다운로드 중 (초고속 모드)...", 10)
    
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': 'temp_audio.%(ext)s', 
        'quiet': True
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        # 💡 수정됨: 다운로드와 동시에 메타데이터(영상 길이)를 뽑아냅니다.
        info = ydl.extract_info(youtube_url, download=True)
        actual_duration = info.get('duration', 0.1)
        
    audio_file = glob.glob("temp_audio.*")[0]
    
    update_status("[2/2] Whisper 모델 추출 중...", 30)
    model = WhisperModel("small", device="cpu", compute_type="int8")
    segments, info = model.transcribe(audio_file, beam_size=1, vad_filter=True)
    
    segments = list(segments)
    
    if os.path.exists(audio_file):
        os.remove(audio_file)
        
    update_status("✅ 원본 자막 추출 초고속 완료!", 50)
    
    # 💡 자막 데이터와 함께 '실제 영상 길이'를 반환합니다.
    return segments, actual_duration