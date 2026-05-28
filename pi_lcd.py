# 라즈베리파이 LCD 화면에 자막을 표시하고 언어/재생/탐색/단어 저장 명령을 PC로 보냅니다.
import json
import os
import socket
import threading
import tkinter as tk
from dotenv import load_dotenv

# 환경변수 로드 (.env 파일에서 IP, 포트, 해상도 등 설정값들을 가져옴)
load_dotenv()

def env_int(name, default):
    """환경변수에서 정수형 값을 안전하게 파싱하는 헬퍼 함수"""
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default

# ==========================================
# 통신 및 UI 기본 설정
# ==========================================
# PC(자막 엔진)의 IP 주소 및 포트 정보
PC_IP = os.getenv("SUBTITLE_PC_IP", "127.0.0.1").strip() or "127.0.0.1"
MY_PORT = env_int("SUBTITLE_PI_PORT", 5005)           # 파이 측 데이터 수신 포트
PC_PORT = env_int("SUBTITLE_PC_COMMAND_PORT", 5006)   # PC 측 제어 명령 수신 포트

# LCD 화면 크기 및 전체화면 모드 설정
LCD_WIDTH, LCD_HEIGHT = 1024, 600
GEOMETRY = os.getenv("SUBTITLE_LCD_GEOMETRY", f"{LCD_WIDTH}x{LCD_HEIGHT}").strip() or f"{LCD_WIDTH}x{LCD_HEIGHT}"
FULLSCREEN = os.getenv("SUBTITLE_LCD_FULLSCREEN", "1").strip().lower() in {"1", "true", "yes", "on"}
FONT_FAMILY = "Malgun Gothic"

# ==========================================
# UDP 소켓 초기화 (수신용 / 송신용 분리)
# ==========================================
# 데이터를 계속 받아야 하는 수신 소켓
sock_receive = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock_receive.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) # 포트 충돌 방지
sock_receive.bind(("0.0.0.0", MY_PORT))

# PC로 제어 명령을 보낼 송신 소켓
sock_send = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

# UI 상태 저장을 위한 전역 변수들
last_total_time = 0.1
is_dragging = False          # 사용자가 프로그레스바를 드래그 중인지 여부
last_display_text = ""       # 화면 깜빡임 방지를 위한 이전 텍스트 상태
last_source_text = ""        
subtitle_pages = [""]        # 텍스트가 길 경우 페이지 단위로 분할된 데이터

LANG_LABELS = {
    "original": "원본", "ko": "한국어", "en": "영어", 
    "ja": "일본어", "zh": "중국어", "de": "독일어",
}

# Keep layout/behavior fixed; sync visual tokens with ui_pyside THEME.
BG_COLOR = "#07111f"
PANEL_COLOR = "#0f1b2b"
SURFACE_COLOR = "#14273b"
TEXT_COLOR = "#eef4ff"
SUBTEXT_COLOR = "#9fb4cc"
ACCENT_COLOR = "#4fd1ff"
ACCENT2_COLOR = "#38b5de"
PAUSE_COLOR = "#c85f76"
TRACK_COLOR = SURFACE_COLOR
BORDER_COLOR = "#1f3247"


def make_button(master, text, command, bg, fg=BG_COLOR, font=None, width=None, pad_x=18, pad_y=12):
    return tk.Button(
        master,
        text=text,
        command=command,
        bg=bg,
        fg=fg,
        activebackground=bg,
        activeforeground=fg,
        font=font,
        relief="flat",
        bd=0,
        highlightthickness=1,
        highlightbackground=BORDER_COLOR,
        padx=pad_x,
        pady=pad_y,
        width=width,
        cursor="hand2",
    )

def chunk_text(value, width):
    """매우 긴 단어(공백 없는 문자열 등)를 강제로 줄바꿈하기 위해 쪼개는 함수"""
    return [value[i : i + width] for i in range(0, len(value), width)]

def build_subtitle_pages(text, max_chars_per_line=28):
    """
    들어온 자막 텍스트가 너무 길어 LCD 화면을 벗어나지 않도록, 
    단어 단위로 끊어서 줄을 바꿈하고 (최대 글자 수 기준), 
    최대 2줄씩 묶어 여러 개의 '페이지'로 만듭니다.
    """
    if not isinstance(text, str): return [""]
    normalized = " ".join(text.strip().split())
    if not normalized: return [""]

    words = normalized.split(" ")
    lines = []
    current = ""
    
    # 1. 화면 폭에 맞게 단어들을 모아 한 줄(line)씩 생성
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if len(candidate) <= max_chars_per_line:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ""
        # 단일 단어가 제한 길이를 넘으면 강제로 쪼갬
        if len(word) <= max_chars_per_line:
            current = word
        else:
            lines.extend(chunk_text(word, max_chars_per_line))

    if current: lines.append(current)

    # 2. 만들어진 줄(lines)을 2줄씩 묶어 하나의 화면(페이지)으로 구성
    pages = []
    for index in range(0, len(lines), 2):
        first = lines[index]
        second = lines[index + 1] if (index + 1) < len(lines) else ""
        pages.append((first + "\n" + second).rstrip())

    return pages if pages else [""]

# ==========================================
# PC로 제어 명령(UDP) 송신 함수들
# ==========================================
def send_command(message):
    try: sock_send.sendto(message.encode("utf-8"), (PC_IP, PC_PORT))
    except OSError: pass

def send_language(lang_code): send_command(f"SET_LANG:{lang_code}") # 표시 언어 변경 요청
def send_play(): send_command("CMD:PLAY")                           # 영상 재생 요청
def send_pause(): send_command("CMD:PAUSE")                         # 영상 일시정지 요청

# 💡 단어 저장 함수
def send_save_word():
    """
    사용자가 LCD 텍스트 박스에서 마우스(또는 터치)로 드래그한 텍스트를 가져와,
    PC로 'SAVE_WORD' 명령과 함께 전송합니다. 전송 후 선택 영역은 해제됩니다.
    """
    try:
        selected_text = lcd_text.selection_get().strip()
        if selected_text:
            send_command(f"SAVE_WORD:{selected_text}")
            lcd_text.tag_remove(tk.SEL, "1.0", tk.END)
    except tk.TclError:
        pass # 텍스트가 선택되지 않았을 경우 발생하는 에러 무시

# ==========================================
# 프로그레스 바(재생 막대) 드래그 및 클릭 이벤트 처리
# ==========================================
def on_press(event):
    global is_dragging
    is_dragging = True
    update_drag_ui(event)

def on_drag(event):
    if is_dragging: update_drag_ui(event)

def on_release(event):
    """드래그(또는 터치)를 마쳤을 때, 해당 위치의 시간 비율을 계산하여 PC로 탐색(SEEK) 명령 전송"""
    global is_dragging
    is_dragging = False
    canvas_w = canvas.winfo_width()
    if canvas_w > 0:
        click_x = max(0, min(event.x, canvas_w))
        target_time = (click_x / canvas_w) * last_total_time
        send_command(f"SEEK:{target_time}")

def update_drag_ui(event):
    """사용자가 프로그레스 바를 조작 중일 때 실시간으로 막대 UI와 시간 라벨을 업데이트함"""
    canvas_w = canvas.winfo_width()
    if canvas_w <= 0: return
    click_x = max(0, min(event.x, canvas_w))
    temp_time = (click_x / canvas_w) * last_total_time
    curr_m, curr_s = divmod(int(temp_time), 60)
    tot_m, tot_s = divmod(int(last_total_time), 60)
    time_label.config(text=f"{curr_m}:{curr_s:02d} / {tot_m}:{tot_s:02d}")

    canvas.delete("all")
    canvas.create_rectangle(0, 8, canvas_w, 12, fill=TRACK_COLOR, outline="")
    canvas.create_rectangle(0, 8, click_x, 12, fill=ACCENT_COLOR, outline="")
    canvas.create_oval(click_x - 6, 4, click_x + 6, 16, fill=ACCENT_COLOR, outline="")

# ==========================================
# 메인 UI 업데이트 함수 (PC로부터 수신한 데이터 기반)
# ==========================================
def update_ui(payload):
    global last_total_time, last_display_text, last_source_text, subtitle_pages

    # 페이로드 데이터 추출 (텍스트, 제목, 진행 시간, 현재 자막의 시작/종료 시간 등)
    text = payload.get("text", "")
    overlay_text = payload.get("overlay_text", "")
    title = payload.get("title", "")
    lang_code = payload.get("lang", "original")
    curr = payload.get("curr", 0.0)
    total = payload.get("total", 0.1)
    cue_start = payload.get("cue_start")
    cue_end = payload.get("cue_end")

    # 시간 데이터 안전화 처리
    if not isinstance(curr, (int, float)): curr = 0.0
    if not isinstance(total, (int, float)) or total <= 0: total = 0.1
    curr = max(0.0, min(float(curr), float(total)))
    last_total_time = float(total)

    # 상단 상태 라벨 업데이트
    if title and isinstance(title, str):
        title_label.config(text=f"현재 재생: {title}")
    language_label.config(text=f"자막: {LANG_LABELS.get(lang_code, lang_code)}")

    # 자막 표시 로직 ("다른 영상 재생 중" 등의 오버레이 텍스트가 우선권 가짐)
    if isinstance(overlay_text, str) and overlay_text.strip():
        display_text = overlay_text.strip()
        subtitle_pages = [""]
        source_key = "__overlay__"
    else:
        # 새로 수신한 원본 텍스트가 이전과 다르면 페이지를 다시 나눔
        if text != last_source_text:
            subtitle_pages = build_subtitle_pages(text)

        # 현재 자막 구간의 진행도에 따라 긴 자막의 '페이지'를 넘겨줌 (자동 슬라이드 효과)
        if subtitle_pages:
            page_count = max(1, len(subtitle_pages))
            page_index = 0
            if isinstance(cue_start, (int, float)) and isinstance(cue_end, (int, float)) and cue_end > cue_start:
                cue_duration = max(0.0, float(cue_end) - float(cue_start))
                elapsed = max(0.0, min(curr - float(cue_start), cue_duration))
                per_page = max(cue_duration / page_count, 0.001)
                page_index = min(int(elapsed // per_page), page_count - 1)
            display_text = subtitle_pages[page_index]
        else:
            display_text = ""
        source_key = text

    # 화면 깜빡임을 최소화하기 위해 텍스트가 실제로 변경되었을 때만 UI 업데이트
    if source_key != last_source_text or display_text != last_display_text:
        lcd_text.config(state="normal")
        lcd_text.delete("1.0", tk.END)
        lcd_text.insert("1.0", display_text)
        lcd_text.tag_add("center", "1.0", "end")
        lcd_text.config(state="disabled") # 드래그 복사는 가능하되 직접 타이핑 수정은 못하도록 막음
        last_source_text = source_key
        last_display_text = display_text

    # 프로그레스 바(막대) 자동 업데이트 (사용자가 직접 드래그 중이 아닐 때만)
    if not is_dragging:
        curr_m, curr_s = divmod(int(curr), 60)
        tot_m, tot_s = divmod(int(total), 60)
        time_label.config(text=f"{curr_m}:{curr_s:02d} / {tot_m}:{tot_s:02d}")

        canvas_w = max(canvas.winfo_width(), 600)
        canvas.delete("all")
        canvas.create_rectangle(0, 8, canvas_w, 12, fill=TRACK_COLOR, outline="")
        fill_w = max(0, min(canvas_w, (curr / total) * canvas_w))
        canvas.create_rectangle(0, 8, fill_w, 12, fill=ACCENT_COLOR, outline="")
        canvas.create_oval(fill_w - 6, 4, fill_w + 6, 16, fill=ACCENT_COLOR, outline="")

def receive_loop():
    """백그라운드 스레드에서 무한 루프를 돌며 PC에서 보내는 UDP 패킷(JSON)을 수신합니다."""
    while True:
        try:
            data, _ = sock_receive.recvfrom(4096)
            payload = json.loads(data.decode("utf-8"))
            # 수신한 데이터를 바탕으로 메인 스레드(UI)에서 update_ui 함수를 실행하도록 위임
            root.after(0, lambda p=payload: update_ui(p))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            pass

# ==========================================
# Tkinter 창 및 위젯(버튼, 텍스트 상자 등) 배치
# ==========================================
root = tk.Tk()
root.title("Raspberry Pi Subtitle LCD")
root.geometry(GEOMETRY)
root.configure(bg=BG_COLOR)
if FULLSCREEN: root.attributes("-fullscreen", True) # 설정 시 전체화면 적용
root.bind("<Escape>", lambda _event: root.attributes("-fullscreen", False)) # ESC로 전체화면 해제
root.bind("q", lambda _event: root.destroy()) # 'q' 키로 빠른 종료

# 안전한 폰트 객체 생성 (공백이 있는 글꼴 이름 처리용)
import tkinter.font as tkfont
FONT_30_BOLD = tkfont.Font(root=root, family=FONT_FAMILY, size=30, weight="bold")
FONT_16_BOLD = tkfont.Font(root=root, family=FONT_FAMILY, size=16, weight="bold")
FONT_15_BOLD = tkfont.Font(root=root, family=FONT_FAMILY, size=15, weight="bold")
FONT_14_BOLD = tkfont.Font(root=root, family=FONT_FAMILY, size=14, weight="bold")
FONT_13_BOLD = tkfont.Font(root=root, family=FONT_FAMILY, size=13, weight="bold")
FONT_12_BOLD = tkfont.Font(root=root, family=FONT_FAMILY, size=12, weight="bold")

# 1. 언어 선택 버튼 프레임 (상단)
lang_frame = tk.Frame(root, bg=PANEL_COLOR, bd=0, highlightthickness=1, highlightbackground=BORDER_COLOR)
lang_frame.pack(fill="x")

for label, code in [("한국어", "ko"), ("영어", "en"), ("일본어", "ja"), ("독일어", "de"), ("원본", "original")]:
    btn = make_button(lang_frame, text=label, command=lambda c=code: send_language(c), bg=PANEL_COLOR, fg=TEXT_COLOR, font=FONT_14_BOLD, width=8)
    btn.pack(side="left", padx=4, pady=5)

# 종료 버튼 (우측 상단)
close_btn = make_button(lang_frame, text="✕", command=root.destroy, bg=ACCENT2_COLOR, fg=BG_COLOR, font=FONT_16_BOLD, width=3, pad_x=12, pad_y=8)
close_btn.pack(side="right", padx=8, pady=5)

# 2. 제목 및 상태 표시 라벨
title_label = tk.Label(root, text=f"자막 대기 중...", font=FONT_13_BOLD, fg=ACCENT_COLOR, bg=BG_COLOR)
title_label.pack(fill="x", padx=20, pady=(8, 0))

language_label = tk.Label(root, text="자막: 원본", font=FONT_12_BOLD, fg=ACCENT_COLOR, bg=BG_COLOR)
language_label.pack(fill="x", padx=20, pady=(4, 0))

# 3. 실제 자막 텍스트가 표시될 중앙 영역
lcd_text = tk.Text(
    root, font=FONT_30_BOLD, fg=TEXT_COLOR, bg=PANEL_COLOR,
    wrap="word", height=3, bd=0, highlightthickness=1, highlightbackground=BORDER_COLOR, insertbackground=TEXT_COLOR, cursor="ibeam"
)
lcd_text.tag_configure("center", justify='center')
lcd_text.insert("1.0", "PC 앱을 시작한 후 영상을 재생하세요.")
lcd_text.tag_add("center", "1.0", "end")
lcd_text.config(state="disabled")
lcd_text.pack(expand=True, fill="both", padx=20, pady=5)

# 4. 재생/정지/단어저장 등 제어 버튼 프레임 (하단부)
control_frame = tk.Frame(root, bg=BG_COLOR)
control_frame.pack(fill="x", padx=20, pady=(5, 10))
play_btn = make_button(control_frame, text="재생", command=send_play, bg=ACCENT_COLOR, fg=BG_COLOR, font=FONT_15_BOLD)
play_btn.configure(height=2)
play_btn.pack(side="left", expand=True, fill="x", padx=(0, 10))
pause_btn = make_button(control_frame, text="정지", command=send_pause, bg=PAUSE_COLOR, fg=BG_COLOR, font=FONT_15_BOLD)
pause_btn.configure(height=2)
pause_btn.pack(side="left", expand=True, fill="x", padx=(0, 10))
save_btn = make_button(control_frame, text="단어 저장", command=send_save_word, bg=ACCENT2_COLOR, fg=BG_COLOR, font=FONT_15_BOLD)
save_btn.configure(height=2)
save_btn.pack(side="left", expand=True, fill="x", padx=(0, 0))

# 5. 시간 및 프로그레스 바(캔버스) 영역 (최하단)
player_frame = tk.Frame(root, bg=BG_COLOR)
player_frame.pack(side="bottom", fill="x", pady=(5, 18), padx=20)
time_label = tk.Label(player_frame, text="0:00 / 0:00", font=FONT_14_BOLD, fg=SUBTEXT_COLOR, bg=BG_COLOR)
time_label.pack(side="left", padx=(0, 18))
canvas = tk.Canvas(player_frame, height=20, bg=BG_COLOR, highlightthickness=0, cursor="hand2")
canvas.pack(side="left", expand=True, fill="x")
# 마우스 및 터치 이벤트 바인딩
canvas.bind("<ButtonPress-1>", on_press)
canvas.bind("<B1-Motion>", on_drag)
canvas.bind("<ButtonRelease-1>", on_release)

# 초기 화면을 한 번 그려주고 백그라운드 수신 루프 스레드 시작
root.after(100, lambda: update_ui({"text": "대기 중...", "curr": 0, "total": 1}))
threading.Thread(target=receive_loop, daemon=True).start()

# UI 이벤트 메인 루프 진입
root.mainloop()
