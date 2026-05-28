# 실제 라즈베리파이 LCD 없이 PC에서 UDP 자막 표시와 제어 기능을 테스트하는 Tkinter 클라이언트입니다.
import json
import os
import socket
import threading
import tkinter as tk

from dotenv import load_dotenv

# 환경변수 로드 (.env)
load_dotenv()

def env_int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default

# ==========================================
# 통신 및 UI 기본 설정 (테스트 모드)
# ==========================================
PC_IP = os.getenv("SUBTITLE_PC_IP", "127.0.0.1").strip() or "127.0.0.1"
MY_PORT = env_int("SUBTITLE_PI_PORT", 5005)           # 수신 포트
PC_PORT = env_int("SUBTITLE_PC_COMMAND_PORT", 5006)   # 송신 포트

LCD_WIDTH, LCD_HEIGHT = 1024, 600
GEOMETRY = os.getenv("SUBTITLE_LCD_GEOMETRY", f"{LCD_WIDTH}x{LCD_HEIGHT}").strip() or f"{LCD_WIDTH}x{LCD_HEIGHT}"
# 💡 실제 파이와 다르게 PC 테스트용이므로 창 모드로 실행
FULLSCREEN = False
FONT_FAMILY = "Malgun Gothic"

# UDP 소켓 초기화 (수신용 / 송신용)
sock_receive = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock_receive.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock_receive.bind(("0.0.0.0", MY_PORT))
sock_send = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

# 전역 상태 변수
last_total_time = 0.1
is_dragging = False
last_display_text = ""
last_source_text = ""
subtitle_pages = [""]

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
    """긴 단어 강제 줄바꿈"""
    return [value[i : i + width] for i in range(0, len(value), width)]

def build_subtitle_pages(text, max_chars_per_line=28):
    """자막 텍스트를 화면 크기에 맞게 단어 단위로 쪼개어 페이지 배열로 반환"""
    if not isinstance(text, str): return [""]
    normalized = " ".join(text.strip().split())
    if not normalized: return [""]

    words = normalized.split(" ")
    lines = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if len(candidate) <= max_chars_per_line:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ""
        if len(word) <= max_chars_per_line:
            current = word
        else:
            lines.extend(chunk_text(word, max_chars_per_line))

    if current: lines.append(current)

    pages = []
    for index in range(0, len(lines), 2):
        first = lines[index]
        second = lines[index + 1] if (index + 1) < len(lines) else ""
        pages.append((first + "\n" + second).rstrip())

    return pages if pages else [""]

# ==========================================
# PC 제어 명령 전송 함수
# ==========================================
def send_command(message):
    try: sock_send.sendto(message.encode("utf-8"), (PC_IP, PC_PORT))
    except OSError: pass

def send_language(lang_code): send_command(f"SET_LANG:{lang_code}")
def send_play(): send_command("CMD:PLAY")
def send_pause(): send_command("CMD:PAUSE")

# 💡 단어 저장 함수
def send_save_word():
    """텍스트 박스에서 드래그한 영역의 텍스트를 PC로 전송하여 단어장에 저장"""
    try:
        selected_text = lcd_text.selection_get().strip()
        if selected_text:
            send_command(f"SAVE_WORD:{selected_text}")
            # 전송 후 선택 영역 시각적 해제
            lcd_text.tag_remove(tk.SEL, "1.0", tk.END)
    except tk.TclError:
        pass # 선택된 텍스트가 없을 때 무시

# ==========================================
# 프로그레스 바 마우스 이벤트 (탐색)
# ==========================================
def on_press(event):
    global is_dragging
    is_dragging = True
    update_drag_ui(event)

def on_drag(event):
    if is_dragging: update_drag_ui(event)

def on_release(event):
    """드래그 종료 시 해당 지점의 시간 비율을 계산해 PC로 이동(SEEK) 명령 전송"""
    global is_dragging
    is_dragging = False
    canvas_w = canvas.winfo_width()
    if canvas_w > 0:
        click_x = max(0, min(event.x, canvas_w))
        target_time = (click_x / canvas_w) * last_total_time
        send_command(f"SEEK:{target_time}")

def update_drag_ui(event):
    """마우스 드래그 중인 위치에 맞게 UI 바와 시간 텍스트 업데이트"""
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
# UI 렌더링 및 UDP 패킷 수신 루프
# ==========================================
def update_ui(payload):
    """PC로부터 받은 JSON 데이터를 파싱하여 화면을 갱신 (자동 페이징 처리 포함)"""
    global last_total_time, last_display_text, last_source_text, subtitle_pages

    text = payload.get("text", "")
    overlay_text = payload.get("overlay_text", "")
    title = payload.get("title", "")
    lang_code = payload.get("lang", "original")
    curr = payload.get("curr", 0.0)
    total = payload.get("total", 0.1)
    cue_start = payload.get("cue_start")
    cue_end = payload.get("cue_end")

    if not isinstance(curr, (int, float)): curr = 0.0
    if not isinstance(total, (int, float)) or total <= 0: total = 0.1
    curr = max(0.0, min(float(curr), float(total)))
    last_total_time = float(total)

    if title and isinstance(title, str):
        title_label.config(text=f"현재 재생: {title}")

    language_label.config(text=f"자막: {LANG_LABELS.get(lang_code, lang_code)}")

    # 오버레이 텍스트(다른 영상 재생 중 등) 우선 표시
    if isinstance(overlay_text, str) and overlay_text.strip():
        display_text = overlay_text.strip()
        subtitle_pages = [""]
        source_key = "__overlay__"
    else:
        if text != last_source_text:
            subtitle_pages = build_subtitle_pages(text)

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

    # 변경점이 있을 때만 텍스트 갱신 (깜빡임 방지)
    if source_key != last_source_text or display_text != last_display_text:
        # 💡 tk.Text 위젯 텍스트 업데이트 로직
        lcd_text.config(state="normal")
        lcd_text.delete("1.0", tk.END)
        lcd_text.insert("1.0", display_text)
        lcd_text.tag_add("center", "1.0", "end")
        lcd_text.config(state="disabled")
        last_source_text = source_key
        last_display_text = display_text

    # 드래그 중이 아닐 때만 프로그레스 바 갱신
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
    """백그라운드에서 UDP 패킷 수신"""
    while True:
        try:
            data, _ = sock_receive.recvfrom(4096)
            payload = json.loads(data.decode("utf-8"))
            root.after(0, lambda p=payload: update_ui(p))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            pass

# ==========================================
# Tkinter 메인 화면 구성
# ==========================================
# Apply neon theme to fake LCD window
root = tk.Tk()
root.title("Subtitle LCD (Fake Raspberry Pi) - TEST MODE")
root.geometry(GEOMETRY)
root.configure(bg=BG_COLOR)
# root.attributes("-topmost", True)
if FULLSCREEN: root.attributes("-fullscreen", True)
root.bind("<Escape>", lambda _event: root.attributes("-fullscreen", False))
root.bind("q", lambda _event: root.destroy())

# 안전한 폰트 객체 생성
import tkinter.font as tkfont
FONT_30_BOLD = tkfont.Font(root=root, family=FONT_FAMILY, size=30, weight="bold")
FONT_16_BOLD = tkfont.Font(root=root, family=FONT_FAMILY, size=16, weight="bold")
FONT_15_BOLD = tkfont.Font(root=root, family=FONT_FAMILY, size=15, weight="bold")
FONT_14_BOLD = tkfont.Font(root=root, family=FONT_FAMILY, size=14, weight="bold")
FONT_13_BOLD = tkfont.Font(root=root, family=FONT_FAMILY, size=13, weight="bold")
FONT_12_BOLD = tkfont.Font(root=root, family=FONT_FAMILY, size=12, weight="bold")

lang_frame = tk.Frame(root, bg=PANEL_COLOR, bd=0, highlightthickness=1, highlightbackground=BORDER_COLOR)
lang_frame.pack(fill="x")

for label, code in [("한국어", "ko"), ("영어", "en"), ("일본어", "ja"), ("독일어", "de"), ("원본", "original")]:
    btn = make_button(lang_frame, text=label, command=lambda c=code: send_language(c), bg=PANEL_COLOR, fg=TEXT_COLOR, font=FONT_14_BOLD, width=8)
    btn.pack(side="left", padx=4, pady=5)

close_btn = make_button(lang_frame, text="✕", command=root.destroy, bg=ACCENT2_COLOR, fg=BG_COLOR, font=FONT_16_BOLD, width=3, pad_x=12, pad_y=8)
close_btn.pack(side="right", padx=8, pady=5)

# 상단 상태 라벨 (테스트 모드임을 명시적으로 출력)
title_label = tk.Label(root, text=f"자막 대기 중...  PC: {PC_IP}  UDP: {MY_PORT}", font=FONT_13_BOLD, fg=ACCENT_COLOR, bg=BG_COLOR)
title_label.pack(fill="x", padx=20, pady=(8, 0))

language_label = tk.Label(root, text="자막: 원본", font=FONT_12_BOLD, fg=ACCENT_COLOR, bg=BG_COLOR)
language_label.pack(fill="x", padx=20, pady=(4, 0))

# 💡 텍스트 표시 영역 (사용자가 마우스로 드래그 선택 가능)
lcd_text = tk.Text(
    root, font=FONT_30_BOLD, fg=TEXT_COLOR, bg=PANEL_COLOR,
    wrap="word", height=3, bd=0, highlightthickness=1, highlightbackground=BORDER_COLOR, insertbackground=TEXT_COLOR,
    cursor="ibeam"
)
lcd_text.tag_configure("center", justify='center')
lcd_text.insert("1.0", "PC 앱을 시작한 후 영상을 재생하세요.")
lcd_text.tag_add("center", "1.0", "end")
lcd_text.config(state="disabled") # 기본적으로 타이핑 불가(읽기 전용)
lcd_text.pack(expand=True, fill="both", padx=20, pady=5)

media_frame = tk.Frame(root, bg=BG_COLOR)
media_frame.pack(fill="x", padx=20, pady=5)

control_frame = tk.Frame(root, bg=BG_COLOR)
control_frame.pack(fill="x", padx=20, pady=(5, 10))
play_btn = make_button(control_frame, text="재생", command=send_play, bg=ACCENT_COLOR, fg=BG_COLOR, font=FONT_15_BOLD)
play_btn.configure(height=2)
play_btn.pack(side="left", expand=True, fill="x", padx=(0, 10))

pause_btn = make_button(control_frame, text="정지", command=send_pause, bg=PAUSE_COLOR, fg=BG_COLOR, font=FONT_15_BOLD)
pause_btn.configure(height=2)
pause_btn.pack(side="left", expand=True, fill="x", padx=(0, 10))

# 💡 단어 저장 버튼 추가
save_word_btn = make_button(control_frame, text="단어 저장", command=send_save_word, bg=ACCENT2_COLOR, fg=BG_COLOR, font=FONT_15_BOLD)
save_word_btn.configure(height=2)
save_word_btn.pack(side="left", expand=True, fill="x", padx=(0, 0))

player_frame = tk.Frame(root, bg=BG_COLOR)
player_frame.pack(side="bottom", fill="x", pady=(5, 18), padx=20)

time_label = tk.Label(player_frame, text="0:00 / 0:00", font=FONT_14_BOLD, fg=SUBTEXT_COLOR, bg=BG_COLOR)
time_label.pack(side="left", padx=(0, 18))

canvas = tk.Canvas(player_frame, height=20, bg=BG_COLOR, highlightthickness=0, cursor="hand2")
canvas.pack(side="left", expand=True, fill="x")
canvas.bind("<ButtonPress-1>", on_press)
canvas.bind("<B1-Motion>", on_drag)
canvas.bind("<ButtonRelease-1>", on_release)

root.after(100, lambda: update_ui({"text": "대기 중...", "curr": 0, "total": 1}))
threading.Thread(target=receive_loop, daemon=True).start()

root.mainloop()
