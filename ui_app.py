import tkinter as tk
from tkinter import ttk, messagebox
import os
import socket
import threading
import json
import requests
import time
import hashlib
import datetime
from dotenv import load_dotenv

from flask_server import state

from google.cloud import translate_v3 as translate
from google.cloud import firestore
from google.oauth2 import service_account

load_dotenv()

def _env_int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default

class SubtitleUI:
    def __init__(self, on_start_callback, get_state_callback):
        self.on_start_callback = on_start_callback
        self.get_state_callback = get_state_callback
        
        self.sock_send = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_recv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.command_port = _env_int("SUBTITLE_PC_COMMAND_PORT", 5006)
        self.sock_recv.bind(("0.0.0.0", self.command_port))
        
        self.pi_ip = os.getenv("SUBTITLE_PI_IP", "127.0.0.1").strip() or "127.0.0.1"
        self.pi_port = _env_int("SUBTITLE_PI_PORT", 5005)
        self.current_lang = "original"
        self.last_sent_payload = ""
        self.last_detected_url = ""
        self.last_detected_title = ""
        self.is_processing = False
        
        try:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            key_path = os.path.join(base_dir, "credentials", "vocalogin-a5a6b9be0cad.json")
            
            if os.path.exists(key_path):
                self.gcp_credentials = service_account.Credentials.from_service_account_file(key_path)
                self.gcp_project_id = self.gcp_credentials.project_id
                
                self.db_client = firestore.Client(
                    project=self.gcp_project_id, 
                    credentials=self.gcp_credentials,
                    database="capstonevoca"
                )
                print(f"🗳️ 새 GCP 프로젝트 연결 성공! (Project: {self.gcp_project_id})")
            else:
                print(f"⚠️ JSON 키 파일이 없습니다: {key_path}")
                self.db_client = None
                self.gcp_credentials = None
                self.gcp_project_id = None
        except Exception as e:
            print(f"Firestore 초기화 실패: {e}")
            self.db_client = None
            self.gcp_credentials = None
        
        self.root = tk.Tk()
        self.root.title("PC 자막 엔진 (URL 관리자)")
        self.root.geometry("700x500")
        
        self.setup_frame = tk.Frame(self.root)
        self.setup_frame.pack(fill='both', expand=True, padx=20, pady=20)
        
        detect_frame = tk.LabelFrame(self.setup_frame, text="🔗 현재 시청 중인 유튜브 (자동 감지)", font=("맑은 고딕", 10, "bold"), padx=10, pady=10)
        detect_frame.pack(fill='x', pady=10)

        self.detected_title_var = tk.StringVar(value="제목 감지 대기 중...")
        self.detected_title_label = tk.Label(detect_frame, textvariable=self.detected_title_var, font=("맑은 고딕", 10, "bold"), anchor='w')
        self.detected_title_label.pack(fill='x', padx=5, pady=(0, 6))
        
        self.detected_url_var = tk.StringVar(value="감지 대기 중...")
        self.detected_url_label = tk.Entry(detect_frame, textvariable=self.detected_url_var, width=70, font=("맑은 고딕", 9), state='readonly')
        self.detected_url_label.pack(fill='x', side='left', expand=True, padx=5)
        
        self.use_detected_btn = tk.Button(detect_frame, text="사용", font=("맑은 고딕", 10, "bold"), bg="#2196F3", fg="white", command=self.use_detected_url, padx=15, pady=5)
        self.use_detected_btn.pack(side='left', padx=5)
        
        manual_frame = tk.LabelFrame(self.setup_frame, text="📝 수동 URL 입력 및 제어", font=("맑은 고딕", 10, "bold"), padx=10, pady=10)
        manual_frame.pack(fill='x', pady=10)
        
        self.url_entry = tk.Entry(manual_frame, width=80, font=("맑은 고딕", 10))
        self.url_entry.pack(fill='x', pady=10)
        
        button_frame = tk.Frame(manual_frame)
        button_frame.pack(fill='x', pady=5)
        
        self.start_btn = tk.Button(button_frame, text="추출 및 전송 시작", font=("맑은 고딕", 12, "bold"), bg="#4CAF50", fg="white", command=self.start_processing)
        self.start_btn.pack(side='left', expand=True, fill='x', padx=(0, 5))

        self.vocab_btn = tk.Button(button_frame, text="내 단어장 열기", font=("맑은 고딕", 12, "bold"), bg="#FF9800", fg="white", command=self.open_vocab_window)
        self.vocab_btn.pack(side='left', expand=True, fill='x', padx=(5, 0))
        
        self.status_label = tk.Label(self.setup_frame, text="대기 중...", font=("맑은 고딕", 10), fg="gray")
        self.status_label.pack(pady=10)
        
        self.progress = ttk.Progressbar(self.setup_frame, orient="horizontal", length=600, mode="determinate")
        self.progress.pack()

        # 💡 메인 창 숨기고 로그인 창 먼저 띄우기
        self.root.withdraw()
        self.logged_in_user = None
        self.show_login_window()

        threading.Thread(target=self.listen_for_commands, daemon=True).start()
        threading.Thread(target=self.poll_detected_url, daemon=True).start()

    # ==========================================
    # 💡 로그인 / 회원가입 창 구현부
    # ==========================================
    def show_login_window(self):
        self.login_win = tk.Toplevel(self.root)
        self.login_win.title("로그인")
        self.login_win.geometry("300x250")
        self.login_win.protocol("WM_DELETE_WINDOW", self.root.destroy)
        
        tk.Label(self.login_win, text="자막 엔진 로그인", font=("맑은 고딕", 14, "bold")).pack(pady=15)
        
        tk.Label(self.login_win, text="ID:").pack()
        self.login_id_entry = tk.Entry(self.login_win)
        self.login_id_entry.pack(pady=5)
        
        tk.Label(self.login_win, text="Password:").pack()
        self.login_pw_entry = tk.Entry(self.login_win, show="*")
        self.login_pw_entry.pack(pady=5)
        
        btn_frame = tk.Frame(self.login_win)
        btn_frame.pack(pady=10)
        
        tk.Button(btn_frame, text="로그인", font=("맑은 고딕", 10, "bold"), bg="#4CAF50", fg="white", width=10, command=self.process_login).pack(side="left", padx=5)
        tk.Button(btn_frame, text="회원가입", font=("맑은 고딕", 10, "bold"), bg="#2196F3", fg="white", width=10, command=self.show_signup_window).pack(side="left", padx=5)

    def process_login(self):
        user_id = self.login_id_entry.get().strip()
        password = self.login_pw_entry.get().strip()
        
        if not user_id or not password:
            messagebox.showerror("오류", "ID와 비밀번호를 모두 입력해주세요.", parent=self.login_win)
            return
            
        if not self.db_client:
            messagebox.showerror("오류", "DB 연동이 되어있지 않습니다.", parent=self.login_win)
            return
            
        doc_ref = self.db_client.collection("users").document(user_id)
        doc = doc_ref.get()
        
        if doc.exists:
            db_pw = doc.to_dict().get("password")
            hashed_pw = hashlib.sha256(password.encode()).hexdigest()
            
            if db_pw == hashed_pw:
                self.logged_in_user = user_id
                messagebox.showinfo("성공", f"{user_id}님 환영합니다!", parent=self.login_win)
                self.login_win.destroy()
                self.root.deiconify() # 메인 창 복구
                self.root.title(f"PC 자막 엔진 (URL 관리자) - 로그인: {user_id}")
            else:
                messagebox.showerror("오류", "비밀번호가 일치하지 않습니다.", parent=self.login_win)
        else:
            messagebox.showerror("오류", "존재하지 않는 아이디입니다.", parent=self.login_win)

    def show_signup_window(self):
        signup_win = tk.Toplevel(self.login_win)
        signup_win.title("회원가입")
        signup_win.geometry("300x320")
        
        tk.Label(signup_win, text="신규 회원가입", font=("맑은 고딕", 14, "bold")).pack(pady=15)
        
        tk.Label(signup_win, text="ID:").pack()
        id_entry = tk.Entry(signup_win)
        id_entry.pack(pady=5)
        
        tk.Label(signup_win, text="Password:").pack()
        pw_entry = tk.Entry(signup_win, show="*")
        pw_entry.pack(pady=5)
        
        tk.Label(signup_win, text="Email:").pack()
        email_entry = tk.Entry(signup_win)
        email_entry.pack(pady=5)
        
        def process_signup():
            user_id = id_entry.get().strip()
            pw = pw_entry.get().strip()
            email = email_entry.get().strip()
            
            if not user_id or not pw or not email:
                messagebox.showerror("오류", "모든 항목을 입력해주세요.", parent=signup_win)
                return
                
            doc_ref = self.db_client.collection("users").document(user_id)
            if doc_ref.get().exists:
                messagebox.showerror("오류", "이미 존재하는 아이디입니다.", parent=signup_win)
                return
                
            doc_ref.set({
                "password": hashlib.sha256(pw.encode()).hexdigest(),
                "email": email
            })
            
            messagebox.showinfo("성공", "회원가입이 완료되었습니다.", parent=signup_win)
            signup_win.destroy()
            
        tk.Button(signup_win, text="가입 완료", font=("맑은 고딕", 10, "bold"), bg="#FF9800", fg="white", width=15, command=process_signup).pack(pady=15)

    # ==========================================
    # 💡 단어 저장 및 단어장 뷰어
    # ==========================================
    def _translate_and_save(self, word, provided_lang_name):
        if not self.db_client or not self.gcp_credentials or not self.logged_in_user:
            print("로그인 상태가 아니거나 DB에 연결할 수 없습니다.")
            return

        try:
            location = os.getenv("GCP_TRANSLATE_LOCATION", "global")
            client = translate.TranslationServiceClient(credentials=self.gcp_credentials)
            parent = f"projects/{self.gcp_project_id}/locations/{location}"

            response = client.translate_text(
                request={
                    "parent": parent,
                    "contents": [word],
                    "mime_type": "text/plain",
                    "target_language_code": "ko",
                }
            )
            
            translation = response.translations[0]
            translated_word = translation.translated_text.strip()
            detected_code = translation.detected_language_code
            
            code_to_name = {
                "ko": "한국어", "en": "영어", "ja": "일본어", 
                "zh": "중국어", "zh-CN": "중국어", "zh-TW": "중국어",
                "de": "독일어", "fr": "프랑스어", "es": "스페인어",
                "ru": "러시아어", "it": "이탈리아어", "pt": "포르투갈어"
            }
            
            if provided_lang_name == "원본":
                lang_name = code_to_name.get(detected_code, detected_code.upper())
            else:
                lang_name = provided_lang_name

            doc_ref = self.db_client.collection("vocabulary").document()
            doc_ref.set({
                "word": word,
                "meaning": translated_word,
                "lang": lang_name,
                "user_id": self.logged_in_user, # 💡 접속 중인 사용자 ID 추가
                "timestamp": firestore.SERVER_TIMESTAMP
            })
            print(f"🗳️ DB 저장 완료 [{self.logged_in_user}] -> {word} : {translated_word}")

        except Exception as e:
            print(f"단어 번역 및 DB 저장 프로세스 실패: {e}")

    def open_vocab_window(self):
        if not self.db_client or not self.logged_in_user:
            messagebox.showerror("오류", "로그인 상태가 아니거나 DB가 없습니다.")
            return

        vocab_window = tk.Toplevel(self.root)
        vocab_window.title(f"내 단어장 - {self.logged_in_user}")
        vocab_window.geometry("550x400")
        
        btn_frame = tk.Frame(vocab_window)
        btn_frame.pack(fill='x', padx=10, pady=10)
        
        columns = ("word", "meaning", "lang")
        tree = ttk.Treeview(vocab_window, columns=columns, show="headings", selectmode="extended")
        tree.heading("word", text="단어")
        tree.heading("meaning", text="뜻")
        tree.heading("lang", text="언어")
        
        tree.column("word", width=200)
        tree.column("meaning", width=200)
        tree.column("lang", width=100, anchor="center")
        
        scrollbar = ttk.Scrollbar(vocab_window, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        tree.pack(expand=True, fill="both", padx=10, pady=(0, 10))

        def load_data():
            for item in tree.get_children():
                tree.delete(item)
            try:
                # 💡 접속한 사용자의 데이터만 가져옴
                docs_ref = self.db_client.collection("vocabulary").where("user_id", "==", self.logged_in_user).stream()
                
                vocab_list = []
                for doc in docs_ref:
                    data = doc.to_dict()
                    data['id'] = doc.id
                    vocab_list.append(data)
                
                # 복합 인덱스 오류 우회를 위해 파이썬 메모리에서 최신순으로 정렬
                def get_time(item):
                    t = item.get('timestamp')
                    if t is None:
                        return datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
                    return t
                    
                vocab_list.sort(key=get_time, reverse=True)

                for data in vocab_list:
                    tree.insert('', tk.END, iid=data['id'], values=(
                        data.get("word", ""), 
                        data.get("meaning", ""), 
                        data.get("lang", "")
                    ))
            except Exception as e:
                print(f"DB 로딩 실패: {e}")
                messagebox.showerror("DB 로딩 실패", f"데이터를 가져오지 못했습니다: {e}", parent=vocab_window)

        def delete_selected():
            selected = tree.selection()
            if not selected:
                messagebox.showwarning("경고", "삭제할 단어를 선택해 주세요.", parent=vocab_window)
                return
            
            if messagebox.askyesno("삭제 확인", f"선택한 {len(selected)}개의 단어를 완전히 삭제하시겠습니까?", parent=vocab_window):
                for doc_id in selected:
                    try:
                        self.db_client.collection("vocabulary").document(doc_id).delete()
                        tree.delete(doc_id)
                    except Exception as e:
                        print(f"문서 삭제 에러({doc_id}): {e}")

        def delete_all():
            if messagebox.askyesno("전체 삭제", "내 단어장을 모두 비우시겠습니까?\n이 작업은 되돌릴 수 없습니다.", parent=vocab_window):
                try:
                    # 💡 본인 단어장만 삭제 처리
                    docs = self.db_client.collection("vocabulary").where("user_id", "==", self.logged_in_user).stream()
                    batch = self.db_client.batch()
                    count = 0
                    for doc in docs:
                        batch.delete(doc.reference)
                        count += 1
                        if count >= 400:
                            batch.commit()
                            batch = self.db_client.batch()
                            count = 0
                    if count > 0:
                        batch.commit()
                        
                    for item in tree.get_children():
                        tree.delete(item)
                    messagebox.showinfo("완료", "단어장 초기화가 성공적으로 끝났습니다.", parent=vocab_window)
                except Exception as e:
                    messagebox.showerror("오류", f"전체 삭제 실패: {e}", parent=vocab_window)

        def sort_by_lang():
            items = [(tree.set(k, "lang"), k) for k in tree.get_children('')]
            items.sort(key=lambda t: t[0])
            for index, (val, k) in enumerate(items):
                tree.move(k, '', index)

        tk.Button(btn_frame, text="선택 삭제", command=delete_selected, bg="#f44336", fg="white", font=("맑은 고딕", 9, "bold")).pack(side="left", padx=2)
        tk.Button(btn_frame, text="전체 삭제", command=delete_all, bg="#d32f2f", fg="white", font=("맑은 고딕", 9, "bold")).pack(side="left", padx=2)
        tk.Button(btn_frame, text="언어별 정렬", command=sort_by_lang, bg="#2196F3", fg="white", font=("맑은 고딕", 9, "bold")).pack(side="right", padx=2)

        load_data()

    def use_detected_url(self):
        detected = self.detected_url_var.get().strip()
        if detected and not detected.startswith("감지"):
            self.url_entry.delete(0, tk.END)
            self.url_entry.insert(0, detected)
            self.root.after(100, self.start_processing)

    def poll_detected_url(self):
        while True:
            try:
                response = requests.get('http://localhost:5000/get_detected_url', timeout=1.5)
                if response.ok:
                    data = response.json()
                    detected_url = data.get('url')
                    detected_title = data.get('title')
                    if detected_title and isinstance(detected_title, str) and detected_title != self.last_detected_title:
                        self.last_detected_title = detected_title
                        self.root.after(0, lambda t=detected_title: self.detected_title_var.set(t))
                    if detected_url and detected_url.startswith('http') and detected_url != self.last_detected_url:
                        self.last_detected_url = detected_url
                        self.root.after(0, lambda u=detected_url: self.detected_url_var.set(u))
            except (requests.exceptions.RequestException, ValueError):
                pass
            time.sleep(0.5)

    def start_processing(self):
        if self.is_processing: return
        url = self.url_entry.get().strip()
        if not url: return
        self.is_processing = True
        self.start_btn.config(state='disabled', text="처리 중...")
        self.status_label.config(text="처리 시작...")
        self.progress["value"] = 0
        
        def update_progress(msg, percent):
            self.root.after(0, lambda: self.status_label.config(text=msg))
            self.root.after(0, lambda: self.progress.configure(value=percent))
            
        def on_complete(success=True, message=None):
            def finish_ui():
                self.is_processing = False
                self.start_btn.config(state='normal', text="새 URL 추출 시작")
                if success:
                    self.status_label.config(text="✅ 전송 중... (파이 LCD를 확인하세요)")
                else:
                    error_text = message or "자막 추출에 실패했습니다."
                    self.status_label.config(text=error_text)
                    messagebox.showerror("자막 추출 실패", error_text)

            self.root.after(0, finish_ui)
            if success and not hasattr(self, 'loop_running'):
                self.loop_running = True
                self.send_loop()
            
        self.on_start_callback(url, update_progress, on_complete)

    def send_loop(self):
        current_state = self.get_state_callback()
        current_texts = current_state.get('texts', {})
        playback_mismatch = bool(current_state.get('playback_mismatch'))
        
        display_text = ""
        if (not playback_mismatch) and current_texts and self.current_lang in current_texts:
            display_text = current_texts[self.current_lang]

        overlay_text = "다른 영상 재생 중" if playback_mismatch else ""
            
        payload_dict = {
            "text": display_text,
            "curr": current_state.get('curr', 0.0),
            "total": current_state.get('total', 0.1),
            "title": current_state.get('title', ""),
            "lang": self.current_lang,
            "cue_start": current_state.get('cue_start'),
            "cue_end": current_state.get('cue_end'),
            "overlay_text": overlay_text,
        }
        payload_str = json.dumps(payload_dict)
        
        if payload_str != self.last_sent_payload:
            try:
                self.sock_send.sendto(payload_str.encode('utf-8'), (self.pi_ip, self.pi_port))
                self.last_sent_payload = payload_str
            except OSError:
                pass
        
        self.root.after(100, self.send_loop)

    def listen_for_commands(self):
        while True:
            try:
                data, _ = self.sock_recv.recvfrom(1024)
                msg = data.decode('utf-8')

                if msg.startswith("SET_LANG:"):
                    self.current_lang = msg.split(":")[1]
                elif msg.startswith("SEEK:"):
                    seek_time = float(msg.split(":")[1])
                    state.pending_command = {"command": "seek", "time": seek_time}
                elif msg == "CMD:PLAY":
                    state.pending_command = {"command": "play"}
                elif msg == "CMD:PAUSE":
                    state.pending_command = {"command": "pause"}
                
                elif msg.startswith("SAVE_WORD:"):
                    word = msg.split(":", 1)[1].strip()
                    
                    lang_labels = {
                        "original": "원본", "ko": "한국어", "en": "영어", 
                        "ja": "일본어", "zh": "중국어", "de": "독일어"
                    }
                    lang_name = lang_labels.get(self.current_lang, self.current_lang)
                    
                    threading.Thread(
                        target=self._translate_and_save, 
                        args=(word, lang_name), 
                        daemon=True
                    ).start()
                    
            except (OSError, UnicodeDecodeError, ValueError):
                pass

    def run(self):
        self.root.mainloop()