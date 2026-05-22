import tkinter as tk
from tkinter import ttk, messagebox
import os
import threading
import requests
import time
from dotenv import load_dotenv

# 분리해 낸 데이터베이스 및 UDP 소켓 통신 모듈 임포트
from db_service import DBService
from udp_service import UDPService

load_dotenv()

def _env_int(name, default):
    try: return int(os.getenv(name, str(default)))
    except (TypeError, ValueError): return default

class SubtitleUI:
    def __init__(self, on_start_callback, get_state_callback):
        self.on_start_callback = on_start_callback
        
        # 1. Firestore DB 서비스 초기화 (키 파일 경로 지정)
        base_dir = os.path.dirname(os.path.abspath(__file__))
        key_path = os.path.join(base_dir, "credentials", "vocalogin-a5a6b9be0cad.json")
        self.db = DBService(key_path)
        
        # 2. 라즈베리파이 통신용 UDP 서비스 초기화 및 수신 스레드 가동
        pi_ip = os.getenv("SUBTITLE_PI_IP", "127.0.0.1").strip() or "127.0.0.1"
        self.udp = UDPService(
            pi_ip=pi_ip,
            pi_port=_env_int("SUBTITLE_PI_PORT", 5005),
            command_port=_env_int("SUBTITLE_PC_COMMAND_PORT", 5006),
            get_state_callback=get_state_callback,
            on_save_word_callback=self._handle_save_word
        )
        self.udp.start_listening()

        # UI 상태 제어용 내부 변수
        self.last_detected_url = ""
        self.last_detected_title = ""
        self.is_processing = False
        self.logged_in_user = None
        
        # 3. 메인 윈도우 생성 및 초기화
        self.root = tk.Tk()
        self.root.title("PC 자막 엔진 (URL 관리자)")
        self.root.geometry("700x500")
        
        self._build_ui()
        self.root.withdraw() # 로그인 완료 전까지 메인 창 숨김
        self.show_login_window()

        # 크롬 확장 프로그램 연동을 위한 유튜브 URL 백그라운드 감지 시작
        threading.Thread(target=self.poll_detected_url, daemon=True).start()

    def _build_ui(self):
        """메인 제어 화면의 위젯 레이아웃 구성"""
        self.setup_frame = tk.Frame(self.root)
        self.setup_frame.pack(fill='both', expand=True, padx=20, pady=20)
        
        # 자동 감지 영역
        detect_frame = tk.LabelFrame(self.setup_frame, text="🔗 현재 시청 중인 유튜브 (자동 감지)", font=("맑은 고딕", 10, "bold"), padx=10, pady=10)
        detect_frame.pack(fill='x', pady=10)

        self.detected_title_var = tk.StringVar(value="제목 감지 대기 중...")
        tk.Label(detect_frame, textvariable=self.detected_title_var, font=("맑은 고딕", 10, "bold"), anchor='w').pack(fill='x', padx=5, pady=(0, 6))
        
        self.detected_url_var = tk.StringVar(value="감지 대기 중...")
        tk.Entry(detect_frame, textvariable=self.detected_url_var, width=70, font=("맑은 고딕", 9), state='readonly').pack(fill='x', side='left', expand=True, padx=5)
        tk.Button(detect_frame, text="사용", font=("맑은 고딕", 10, "bold"), bg="#2196F3", fg="white", command=self.use_detected_url, padx=15, pady=5).pack(side='left', padx=5)
        
        # 수동 제어 및 추출 시작 영역
        manual_frame = tk.LabelFrame(self.setup_frame, text="📝 수동 URL 입력 및 제어", font=("맑은 고딕", 10, "bold"), padx=10, pady=10)
        manual_frame.pack(fill='x', pady=10)
        
        self.url_entry = tk.Entry(manual_frame, width=80, font=("맑은 고딕", 10))
        self.url_entry.pack(fill='x', pady=10)
        
        button_frame = tk.Frame(manual_frame)
        button_frame.pack(fill='x', pady=5)
        
        self.start_btn = tk.Button(button_frame, text="추출 및 전송 시작", font=("맑은 고딕", 12, "bold"), bg="#4CAF50", fg="white", command=self.start_processing)
        self.start_btn.pack(side='left', expand=True, fill='x', padx=(0, 5))
        tk.Button(button_frame, text="내 단어장 열기", font=("맑은 고딕", 12, "bold"), bg="#FF9800", fg="white", command=self.open_vocab_window).pack(side='left', expand=True, fill='x', padx=(5, 0))
        
        self.status_label = tk.Label(self.setup_frame, text="대기 중...", font=("맑은 고딕", 10), fg="gray")
        self.status_label.pack(pady=10)
        
        self.progress = ttk.Progressbar(self.setup_frame, orient="horizontal", length=600, mode="determinate")
        self.progress.pack()

    def show_login_window(self):
        """인증 전용 모달(Toplevel) 윈도우 생성"""
        win = tk.Toplevel(self.root)
        win.title("로그인")
        win.geometry("300x250")
        win.protocol("WM_DELETE_WINDOW", self.root.destroy) # 로그인 취소 시 앱 완전 종료
        
        tk.Label(win, text="자막 엔진 로그인", font=("맑은 고딕", 14, "bold")).pack(pady=15)
        tk.Label(win, text="ID:").pack()
        id_entry = tk.Entry(win)
        id_entry.pack(pady=5)
        tk.Label(win, text="Password:").pack()
        pw_entry = tk.Entry(win, show="*")
        pw_entry.pack(pady=5)
        
        def attempt_login():
            user_id, pw = id_entry.get().strip(), pw_entry.get().strip()
            if not user_id or not pw: return messagebox.showerror("오류", "모두 입력해주세요.", parent=win)
            
            # DB 컴포넌트에 유저 검증 위임
            success, msg = self.db.login(user_id, pw)
            if success:
                self.logged_in_user = user_id
                messagebox.showinfo("성공", f"{user_id}님 환영합니다!", parent=win)
                win.destroy()
                self.root.deiconify() # 본 창 활성화
                self.root.title(f"PC 자막 엔진 (URL 관리자) - 로그인: {user_id}")
            else:
                messagebox.showerror("오류", msg, parent=win)

        btn_frame = tk.Frame(win)
        btn_frame.pack(pady=10)
        tk.Button(btn_frame, text="로그인", font=("맑은 고딕", 10, "bold"), bg="#4CAF50", fg="white", width=10, command=attempt_login).pack(side="left", padx=5)
        tk.Button(btn_frame, text="회원가입", font=("맑은 고딕", 10, "bold"), bg="#2196F3", fg="white", width=10, command=lambda: self.show_signup_window(win)).pack(side="left", padx=5)

    def show_signup_window(self, parent_win):
        """회원가입 전용 창"""
        win = tk.Toplevel(parent_win)
        win.title("회원가입")
        win.geometry("300x320")
        
        tk.Label(win, text="신규 회원가입", font=("맑은 고딕", 14, "bold")).pack(pady=15)
        tk.Label(win, text="ID:").pack()
        id_entry = tk.Entry(win)
        id_entry.pack(pady=5)
        tk.Label(win, text="Password:").pack()
        pw_entry = tk.Entry(win, show="*")
        pw_entry.pack(pady=5)
        tk.Label(win, text="Email:").pack()
        email_entry = tk.Entry(win)
        email_entry.pack(pady=5)
        
        def process_signup():
            user_id, pw, email = id_entry.get().strip(), pw_entry.get().strip(), email_entry.get().strip()
            if not user_id or not pw or not email: return messagebox.showerror("오류", "모두 입력해주세요.", parent=win)
            
            # DB 컴포넌트에 회원 데이터 삽입 위임
            success, msg = self.db.signup(user_id, pw, email)
            if success:
                messagebox.showinfo("성공", msg, parent=win)
                win.destroy()
            else:
                messagebox.showerror("오류", msg, parent=win)
            
        tk.Button(win, text="가입 완료", font=("맑은 고딕", 10, "bold"), bg="#FF9800", fg="white", width=15, command=process_signup).pack(pady=15)

    def _handle_save_word(self, word, lang_name):
        """UDP 통신으로 들어온 파이 측의 단어 저장 이벤트를 DB 모듈로 매핑"""
        if self.logged_in_user:
            self.db.translate_and_save_word(self.logged_in_user, word, lang_name)

    def open_vocab_window(self):
        """접속한 사용자의 개인 단어장 뷰어 창 오픈 및 데이터 처리"""
        if not self.db.is_connected() or not self.logged_in_user:
            return messagebox.showerror("오류", "로그인 상태가 아니거나 DB가 없습니다.")

        win = tk.Toplevel(self.root)
        win.title(f"내 단어장 - {self.logged_in_user}")
        win.geometry("550x400")
        
        btn_frame = tk.Frame(win)
        btn_frame.pack(fill='x', padx=10, pady=10)
        
        tree = ttk.Treeview(win, columns=("word", "meaning", "lang"), show="headings", selectmode="extended")
        tree.heading("word", text="단어"); tree.heading("meaning", text="뜻"); tree.heading("lang", text="언어")
        tree.column("word", width=200); tree.column("meaning", width=200); tree.column("lang", width=100, anchor="center")
        
        scrollbar = ttk.Scrollbar(win, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        tree.pack(expand=True, fill="both", padx=10, pady=(0, 10))

        def load_data():
            tree.delete(*tree.get_children())
            try:
                # 유저별 저장 단어 바인딩
                for data in self.db.get_vocabulary(self.logged_in_user):
                    tree.insert('', tk.END, iid=data['id'], values=(data.get("word", ""), data.get("meaning", ""), data.get("lang", "")))
            except Exception as e:
                messagebox.showerror("DB 로딩 실패", f"데이터를 가져오지 못했습니다: {e}", parent=win)

        def delete_selected():
            selected = tree.selection()
            if not selected: return messagebox.showwarning("경고", "삭제할 단어를 선택해 주세요.", parent=win)
            if messagebox.askyesno("삭제 확인", f"선택한 {len(selected)}개의 단어를 삭제하시겠습니까?", parent=win):
                self.db.delete_vocabulary(selected)
                for doc_id in selected: tree.delete(doc_id)

        def delete_all():
            if messagebox.askyesno("전체 삭제", "내 단어장을 모두 비우시겠습니까?", parent=win):
                try:
                    self.db.delete_all_vocabulary(self.logged_in_user)
                    tree.delete(*tree.get_children())
                    messagebox.showinfo("완료", "단어장 초기화 완료.", parent=win)
                except Exception as e:
                    messagebox.showerror("오류", f"삭제 실패: {e}", parent=win)

        def sort_by_lang():
            items = [(tree.set(k, "lang"), k) for k in tree.get_children('')]
            for index, (val, k) in enumerate(sorted(items, key=lambda t: t[0])):
                tree.move(k, '', index)

        tk.Button(btn_frame, text="선택 삭제", command=delete_selected, bg="#f44336", fg="white", font=("맑은 고딕", 9, "bold")).pack(side="left", padx=2)
        tk.Button(btn_frame, text="전체 삭제", command=delete_all, bg="#d32f2f", fg="white", font=("맑은 고딕", 9, "bold")).pack(side="left", padx=2)
        tk.Button(btn_frame, text="언어별 정렬", command=sort_by_lang, bg="#2196F3", fg="white", font=("맑은 고딕", 9, "bold")).pack(side="right", padx=2)

        load_data()

    def use_detected_url(self):
        """자동 감지된 주소를 인풋 영역으로 붙여넣고 바로 오디오 추출 가동"""
        detected = self.detected_url_var.get().strip()
        if detected and not detected.startswith("감지"):
            self.url_entry.delete(0, tk.END)
            self.url_entry.insert(0, detected)
            self.root.after(100, self.start_processing)

    def poll_detected_url(self):
        """Flask 백엔드 엔드포인트를 0.5초 주기로 조회하여 확장 프로그램이 감지한 URL 파싱"""
        while True:
            try:
                res = requests.get('http://localhost:5000/get_detected_url', timeout=1.5)
                if res.ok:
                    data = res.json()
                    title, url = data.get('title'), data.get('url')
                    if title and title != self.last_detected_title:
                        self.last_detected_title = title
                        self.root.after(0, lambda t=title: self.detected_title_var.set(t))
                    if url and url.startswith('http') and url != self.last_detected_url:
                        self.last_detected_url = url
                        self.root.after(0, lambda u=url: self.detected_url_var.set(u))
            except Exception: pass
            time.sleep(0.5)

    def start_processing(self):
        """자막 추출 엔진 핵심 파이프라인 구동 스레드 호출"""
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
                    err = message or "자막 추출에 실패했습니다."
                    self.status_label.config(text=err)
                    messagebox.showerror("실패", err)
            self.root.after(0, finish_ui)
            
            # 자막 처리가 최종 성공하면 라즈베리파이 상태 업데이트 무한 루프 시작
            if success and not hasattr(self, 'loop_running'):
                self.loop_running = True
                self._run_udp_loop()
            
        self.on_start_callback(url, update_progress, on_complete)

    def _run_udp_loop(self):
        """Tkinter 메인 스레드 컨텍스트 내에서 비차단식(Non-blocking) 100ms 타이머 기반 송신 틱 연동"""
        self.udp.send_loop_tick()
        self.root.after(100, self._run_udp_loop)

    def run(self):
        self.root.mainloop()