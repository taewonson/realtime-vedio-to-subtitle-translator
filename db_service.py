# Firestore 사용자 인증, 단어장 저장, 필요 시 번역/TTS 호출을 담당합니다.
"""Firestore-backed user and vocabulary service.

Firestore credentials are used only for login and vocabulary persistence.
Translation and Text-to-Speech use the separate AI credentials configured by
GCP_CREDENTIALS_FILE so speech/translation permissions stay isolated.
"""

import os
import hashlib
import datetime
import html
from google.cloud import firestore, translate_v3 as translate
from google.oauth2 import service_account
from google_cloud_auth import get_google_project_id, load_google_credentials

class DBService:
    """
    Firestore DB 연동, 유저 인증(로그인/회원가입), 단어 번역 및 저장 등 
    데이터베이스와 관련된 모든 비즈니스 로직을 전담하는 클래스.
    """
    def __init__(self, key_path):
        self.db_client = None
        self.gcp_credentials = None
        self.gcp_project_id = None
        self.ai_credentials = None
        self.ai_project_id = None
        self.translate_client = None
        self.tts_client = None

        # Firestore용 서비스 계정과 AI용 서비스 계정을 분리해 초기화함
        self._init_firestore(key_path)
        self._init_ai_clients()

    def _init_firestore(self, key_path):
        """로그인/단어장 저장에 사용할 Firestore 클라이언트를 초기화합니다."""
        try:
            if not os.path.exists(key_path):
                print(f"⚠️ JSON 키 파일이 없습니다: {key_path}")
                return

            self.gcp_credentials = service_account.Credentials.from_service_account_file(key_path)
            self.gcp_project_id = self.gcp_credentials.project_id
            self.db_client = firestore.Client(
                project=self.gcp_project_id,
                credentials=self.gcp_credentials,
                database="capstonevoca",
            )
            print(f"🗳️ Firestore 연결 성공! (Project: {self.gcp_project_id})")
        except Exception as e:
            print(f"Firestore 초기화 실패: {e}")

    def _init_ai_clients(self):
        """단어 상세 번역과 TTS에 사용할 Google AI 클라이언트를 초기화합니다."""
        try:
            self.ai_credentials = load_google_credentials()
            self.ai_project_id = get_google_project_id()
            self.translate_client = translate.TranslationServiceClient(credentials=self.ai_credentials)
        except Exception as e:
            print(f"Google AI 인증 초기화 실패: {e}")

    def _ensure_ai_ready(self):
        """번역/TTS 실행 전에 AI 인증이 준비되어 있는지 확인합니다."""
        if not self.ai_credentials or not self.ai_project_id:
            raise RuntimeError("Google AI 인증이 초기화되지 않았습니다.")

    def is_connected(self):
        """DB 클라이언트가 정상적으로 초기화되었는지 확인하는 헬퍼 함수"""
        return self.db_client is not None

    def login(self, user_id, password):
        """입력받은 ID와 비밀번호(SHA-256 해시)로 Firestore 유저 정보를 대조하여 로그인 처리"""
        if not self.is_connected():
            return False, "DB 연동이 되어있지 않습니다."
            
        doc = self.db_client.collection("users").document(user_id).get()
        if doc.exists:
            db_pw = doc.to_dict().get("password")
            hashed_pw = hashlib.sha256(password.encode()).hexdigest()
            if db_pw == hashed_pw:
                return True, "성공"
            return False, "비밀번호가 일치하지 않습니다."
        return False, "존재하지 않는 아이디입니다."

    def signup(self, user_id, pw, email):
        """신규 유저의 ID, 비밀번호, 이메일을 Firestore에 저장하여 회원가입 처리"""
        if not self.is_connected():
            return False, "DB 연동이 되어있지 않습니다."
            
        doc_ref = self.db_client.collection("users").document(user_id)
        if doc_ref.get().exists:
            return False, "이미 존재하는 아이디입니다."
            
        # 비밀번호는 보안을 위해 SHA-256으로 단방향 해시 암호화 후 저장
        doc_ref.set({
            "password": hashlib.sha256(pw.encode()).hexdigest(),
            "email": email
        })
        return True, "회원가입이 완료되었습니다."

    def translate_and_save_word(self, user_id, word, provided_lang_name, lang_code=None):
        """파이(LCD)에서 보낸 단어를 번역 없이 Firestore 단어장에 저장"""
        if not self.is_connected() or not user_id:
            return

        try:
            # Firestore 'vocabulary' 컬렉션에 새 문서 생성하여 저장
            self.db_client.collection("vocabulary").document().set({
                "word": word,
                "lang": provided_lang_name,
                "lang_code": lang_code or "",
                "note": "",
                "user_id": user_id, # 어떤 유저의 단어장인지 식별
                "timestamp": firestore.SERVER_TIMESTAMP # 정렬을 위한 서버 타임스탬프
            })
            print(f"🗳️ DB 저장 완료 [{user_id}] -> {word}")
        except Exception as e:
            print(f"단어 DB 저장 프로세스 실패: {e}")

    def translate_text(self, text, target_lang_code):
        """저장된 단어/문장을 사용자가 선택한 언어로 필요할 때 번역"""
        if not self.is_connected():
            raise RuntimeError("DB 연동이 되어있지 않습니다.")
        self._ensure_ai_ready()

        location = os.getenv("GCP_TRANSLATE_LOCATION", "global")
        parent = f"projects/{self.ai_project_id}/locations/{location}"

        response = self.translate_client.translate_text(
            request={
                "parent": parent,
                "contents": [text],
                "mime_type": "text/plain",
                "target_language_code": target_lang_code,
            }
        )
        return html.unescape(response.translations[0].translated_text.strip())

    def synthesize_speech(self, text, language_code):
        """Google Cloud TTS로 텍스트 발음 오디오 데이터를 WAV 형식으로 생성"""
        self._ensure_ai_ready()
        try:
            from google.cloud import texttospeech
        except ImportError as e:
            raise RuntimeError("google-cloud-texttospeech 패키지가 설치되어 있지 않습니다.") from e

        if self.tts_client is None:
            self.tts_client = texttospeech.TextToSpeechClient(credentials=self.ai_credentials)

        synthesis_input = texttospeech.SynthesisInput(text=text)
        voice = texttospeech.VoiceSelectionParams(
            language_code=language_code,
            ssml_gender=texttospeech.SsmlVoiceGender.NEUTRAL,
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.LINEAR16
        )
        response = self.tts_client.synthesize_speech(
            input=synthesis_input,
            voice=voice,
            audio_config=audio_config,
        )
        return response.audio_content

    def get_vocabulary(self, user_id):
        """특정 유저의 단어장 데이터를 Firestore에서 최신순으로 가져옴"""
        if not self.is_connected(): return []
        docs = self.db_client.collection("vocabulary").where("user_id", "==", user_id).stream()
        
        vocab_list = [{"id": doc.id, **doc.to_dict()} for doc in docs]
        
        # 파이썬 메모리에서 최신순으로 정렬 (Firestore 복합 인덱스 에러 방지용)
        def get_time(item):
            t = item.get('timestamp')
            return t if t else datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
            
        vocab_list.sort(key=get_time, reverse=True)
        return vocab_list

    def delete_vocabulary(self, doc_ids):
        """UI에서 선택한 특정 단어들(문서 ID 리스트)을 삭제"""
        for doc_id in doc_ids:
            self.db_client.collection("vocabulary").document(doc_id).delete()

    def update_vocabulary_note(self, doc_id, note):
        """단어장 상세 창에서 입력한 개인 메모를 저장"""
        self.db_client.collection("vocabulary").document(doc_id).update({
            "note": note,
            "updated_at": firestore.SERVER_TIMESTAMP,
        })

    def delete_all_vocabulary(self, user_id):
        """특정 유저의 단어장 전체를 삭제 (Firestore 배치 처리)"""
        docs = self.db_client.collection("vocabulary").where("user_id", "==", user_id).stream()
        batch = self.db_client.batch()
        count = 0
        for doc in docs:
            batch.delete(doc.reference)
            count += 1
            # Firestore의 한 번 배치 처리 한도(500개)를 넘지 않도록 400개마다 커밋
            if count >= 400:
                batch.commit()
                batch = self.db_client.batch()
                count = 0
        if count > 0:
            batch.commit()
