import tkinter as tk
from tkinter import ttk

class SubtitleUI:
    def __init__(self, on_start_callback, get_texts_callback):
        self.on_start_callback = on_start_callback
        self.get_texts_callback = get_texts_callback # 딕셔너리를 가져옴
        
        self.root = tk.Tk()
        self.root.title("subtitle_app")
        self.root.attributes("-topmost", True)
        
        # 언어 매핑 딕셔너리
        self.lang_map = {
            "원본 (Original)": "original",
            "한국어 (Korean)": "ko",
            "영어 (English)": "en",
            "일본어 (Japanese)": "ja",
            # "중국어 (Chinese)": "zh",
            # "독일어 (German)": "de"
        }
        
        # === [모드 1] 준비 화면 ===
        self.root.geometry("600x250")
        self.setup_frame = tk.Frame(self.root)
        self.setup_frame.pack(fill='both', expand=True, padx=20, pady=20)
        tk.Label(self.setup_frame, text="유튜브 URL을 입력하세요:", font=("맑은 고딕", 12)).pack(anchor='w')
        self.url_entry = tk.Entry(self.setup_frame, width=60, font=("맑은 고딕", 11))
        self.url_entry.pack(fill='x', pady=10)
        self.start_btn = tk.Button(self.setup_frame, text="다운로드 및 자막 준비 시작", font=("맑은 고딕", 12, "bold"), bg="#4CAF50", fg="white", command=self.start_processing)
        self.start_btn.pack(pady=5)
        self.status_label = tk.Label(self.setup_frame, text="", font=("맑은 고딕", 10), fg="gray")
        self.status_label.pack(pady=(15, 5))
        self.progress = ttk.Progressbar(self.setup_frame, orient="horizontal", length=500, mode="determinate")
        self.progress.pack()

        # === [모드 2] 자막 화면 ===
        self.subtitle_frame = tk.Frame(self.root, bg='black')
        
        # 콤보박스 (우측 상단)
        self.control_frame = tk.Frame(self.subtitle_frame, bg='black')
        self.control_frame.pack(fill='x', anchor='ne')
        self.lang_combo = ttk.Combobox(self.control_frame, values=list(self.lang_map.keys()), state="readonly", width=15)
        self.lang_combo.set("원본 (Original)") # 기본값
        self.lang_combo.pack(side='right', padx=10, pady=5)
        
        # 자막 라벨
        self.subtitle_label = tk.Label(self.subtitle_frame, text="자막 대기 중...", font=("맑은 고딕", 22, "bold"), fg="white", bg="black", wraplength=780, justify="center")
        self.subtitle_label.pack(expand=True, fill='both')

    def start_processing(self):
        url = self.url_entry.get().strip()
        if not url:
            self.status_label.config(text="⚠️ URL을 먼저 입력해 주세요!", fg="red")
            return
        self.start_btn.config(state='disabled', text="작업 진행 중...")
        self.url_entry.config(state='disabled')
        self.progress["value"] = 0
        
        def update_progress(msg, percent):
            self.root.after(0, lambda: self._update_ui_state(msg, percent))
        def on_complete():
            self.root.after(0, self.switch_to_subtitle_mode)
            
        self.on_start_callback(url, update_progress, on_complete)

    def _update_ui_state(self, msg, percent):
        self.status_label.config(text=msg)
        self.progress["value"] = percent

    def switch_to_subtitle_mode(self):
        self.setup_frame.pack_forget()
        self.root.geometry("800x120") # 콤보박스 공간 확보를 위해 살짝 늘림
        self.root.configure(bg='black')
        self.subtitle_frame.pack(fill='both', expand=True)
        self.update_subtitle()

    def update_subtitle(self):
        current_texts = self.get_texts_callback()
        
        # 콤보박스에서 선택한 언어의 코드를 가져옴
        selected_display = self.lang_combo.get()
        lang_code = self.lang_map.get(selected_display, "original")
        
        # 현재 시간에 해당하는 텍스트 딕셔너리가 있다면, 선택된 언어 출력
        if current_texts and lang_code in current_texts:
            self.subtitle_label.config(text=current_texts[lang_code])
        else:
            self.subtitle_label.config(text="")
            
        self.root.after(100, self.update_subtitle)

    def run(self):
        self.root.mainloop()