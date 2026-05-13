import json
import os
import socket
import threading
import tkinter as tk

from dotenv import load_dotenv

load_dotenv()


def env_int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# 네트워크/화면 설정은 .env에서 읽습니다.
# 그래서 실제 라즈베리파이 LCD와 로컬 테스트 환경에서 같은 스크립트를 사용할 수 있습니다.
PC_IP = os.getenv("SUBTITLE_PC_IP", "127.0.0.1").strip() or "127.0.0.1"
MY_PORT = env_int("SUBTITLE_PI_PORT", 5005)
PC_PORT = env_int("SUBTITLE_PC_COMMAND_PORT", 5006)
LCD_WIDTH, LCD_HEIGHT = 1024, 600
GEOMETRY = os.getenv("SUBTITLE_LCD_GEOMETRY", f"{LCD_WIDTH}x{LCD_HEIGHT}").strip() or f"{LCD_WIDTH}x{LCD_HEIGHT}"
FULLSCREEN = os.getenv("SUBTITLE_LCD_FULLSCREEN", "1").strip().lower() in {"1", "true", "yes", "on"}
DEFAULT_WRAP = 940

# PC에서 라즈베리파이로 오는 자막 데이터는 MY_PORT로 받습니다.
# 라즈베리파이의 버튼 명령은 PC_PORT로 다시 보내며, 둘 다 UDP를 사용합니다.
sock_receive = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock_receive.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock_receive.bind(("0.0.0.0", MY_PORT))
sock_send = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

last_total_time = 0.1
is_dragging = False
last_display_text = ""
last_source_text = ""
subtitle_pages = [""]

LANG_LABELS = {
    "original": "Original",
    "ko": "Korean",
    "en": "English",
    "ja": "Japanese",
    "zh": "Chinese",
    "de": "German",
}


def chunk_text(value, width):
    return [value[i : i + width] for i in range(0, len(value), width)]


def build_subtitle_pages(text, max_chars_per_line=28):
    # 긴 자막을 작은 LCD에서도 읽기 좋게 두 줄 단위 페이지로 나눕니다.
    # update_ui에서 현재 자막 구간 진행률에 맞춰 보여줄 페이지를 고릅니다.
    if not isinstance(text, str):
        return [""]

    normalized = " ".join(text.strip().split())
    if not normalized:
        return [""]

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

    if current:
        lines.append(current)

    pages = []
    for index in range(0, len(lines), 2):
        first = lines[index]
        second = lines[index + 1] if (index + 1) < len(lines) else ""
        pages.append((first + "\n" + second).rstrip())

    return pages if pages else [""]


def send_command(message):
    # LCD 화면의 버튼 입력을 PC 앱이 이해할 수 있는 간단한 명령 문자열로 보냅니다.
    try:
        sock_send.sendto(message.encode("utf-8"), (PC_IP, PC_PORT))
    except OSError:
        pass


def send_language(lang_code):
    send_command(f"SET_LANG:{lang_code}")


def send_play():
    send_command("CMD:PLAY")


def send_pause():
    send_command("CMD:PAUSE")


def on_press(event):
    global is_dragging
    is_dragging = True
    update_drag_ui(event)


def on_drag(event):
    if is_dragging:
        update_drag_ui(event)


def on_release(event):
    global is_dragging
    is_dragging = False
    canvas_w = canvas.winfo_width()
    if canvas_w > 0:
        click_x = max(0, min(event.x, canvas_w))
        target_time = (click_x / canvas_w) * last_total_time
        # 진행 바에서 선택한 위치를 영상 시간으로 바꾼 뒤 PC 앱으로 보냅니다.
        # 실제 유튜브 탐색은 PC 앱과 Chrome 확장 프로그램이 처리합니다.
        send_command(f"SEEK:{target_time}")


def update_drag_ui(event):
    canvas_w = canvas.winfo_width()
    if canvas_w <= 0:
        return

    click_x = max(0, min(event.x, canvas_w))
    temp_time = (click_x / canvas_w) * last_total_time
    curr_m, curr_s = divmod(int(temp_time), 60)
    tot_m, tot_s = divmod(int(last_total_time), 60)
    time_label.config(text=f"{curr_m}:{curr_s:02d} / {tot_m}:{tot_s:02d}")

    canvas.delete("all")
    canvas.create_rectangle(0, 8, canvas_w, 12, fill="#555555", outline="")
    canvas.create_rectangle(0, 8, click_x, 12, fill="#ff2f2f", outline="")
    canvas.create_oval(click_x - 6, 4, click_x + 6, 16, fill="#ff2f2f", outline="")


def update_ui(payload):
    global last_total_time, last_display_text, last_source_text, subtitle_pages

    # payload는 PC 앱에서 만들어 보낸 자막 패킷입니다.
    # 현재 자막, 재생 시간, 전체 길이, 자막 구간 시작/끝 시간이 들어 있습니다.
    text = payload.get("text", "")
    overlay_text = payload.get("overlay_text", "")
    title = payload.get("title", "")
    lang_code = payload.get("lang", "original")
    curr = payload.get("curr", 0.0)
    total = payload.get("total", 0.1)
    cue_start = payload.get("cue_start")
    cue_end = payload.get("cue_end")

    if not isinstance(curr, (int, float)):
        curr = 0.0
    if not isinstance(total, (int, float)) or total <= 0:
        total = 0.1

    curr = max(0.0, min(float(curr), float(total)))
    last_total_time = float(total)

    if title and isinstance(title, str):
        title_label.config(text=f"Now playing: {title}")

    language_label.config(text=f"Subtitle: {LANG_LABELS.get(lang_code, lang_code)}")

    if isinstance(overlay_text, str) and overlay_text.strip():
        # overlay_text는 다른 영상 재생 감지 같은 특수 상태를 표시할 때 사용합니다.
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
                # 하나의 자막 구간 동안 페이지가 한 번만 순서대로 넘어가게 합니다.
                cue_duration = max(0.0, float(cue_end) - float(cue_start))
                elapsed = max(0.0, min(curr - float(cue_start), cue_duration))
                per_page = max(cue_duration / page_count, 0.001)
                page_index = min(int(elapsed // per_page), page_count - 1)
            display_text = subtitle_pages[page_index]
        else:
            display_text = ""
        source_key = text

    if source_key != last_source_text or display_text != last_display_text:
        # 화면에 보이는 문구가 바뀌었을 때만 라벨을 갱신합니다.
        lcd_label.config(text=display_text)
        last_source_text = source_key
        last_display_text = display_text

    if not is_dragging:
        curr_m, curr_s = divmod(int(curr), 60)
        tot_m, tot_s = divmod(int(total), 60)
        time_label.config(text=f"{curr_m}:{curr_s:02d} / {tot_m}:{tot_s:02d}")

        canvas_w = max(canvas.winfo_width(), 600)
        canvas.delete("all")
        canvas.create_rectangle(0, 8, canvas_w, 12, fill="#555555", outline="")
        fill_w = max(0, min(canvas_w, (curr / total) * canvas_w))
        canvas.create_rectangle(0, 8, fill_w, 12, fill="#ff2f2f", outline="")
        canvas.create_oval(fill_w - 6, 4, fill_w + 6, 16, fill="#ff2f2f", outline="")


def receive_loop():
    # UDP 수신은 대기 시간이 생길 수 있으므로 Tkinter 메인 스레드와 분리합니다.
    # Tkinter 위젯은 스레드에 안전하지 않아서 root.after로 UI 갱신을 예약합니다.
    while True:
        try:
            data, _ = sock_receive.recvfrom(4096)
            payload = json.loads(data.decode("utf-8"))
            root.after(0, lambda p=payload: update_ui(p))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            pass


root = tk.Tk()
root.title("Raspberry Pi Subtitle LCD")
root.geometry(GEOMETRY)
root.configure(bg="#151515")
root.attributes("-topmost", True)
if FULLSCREEN:
    root.attributes("-fullscreen", True)
root.bind("<Escape>", lambda _event: root.attributes("-fullscreen", False))
root.bind("q", lambda _event: root.destroy())

lang_frame = tk.Frame(root, bg="#222222")
lang_frame.pack(fill="x")

# 언어 버튼은 이후 자막 패킷에 어떤 언어를 담을지만 PC에 알려줍니다.
# 실제 번역 작업은 PC에서 처리합니다.
for label, code in [("KO", "ko"), ("EN", "en"), ("JA", "ja"), ("DE", "de"), ("ORG", "original")]:
    btn = tk.Button(
        lang_frame,
        text=label,
        command=lambda c=code: send_language(c),
        bg="#333333",
        fg="white",
        activebackground="#444444",
        activeforeground="white",
        font=("DejaVu Sans", 12, "bold"),
        relief="flat",
        padx=18,
        pady=9,
    )
    btn.pack(side="left", padx=4, pady=5)

title_label = tk.Label(
    root,
    text=f"Waiting for subtitles...  PC: {PC_IP}  UDP: {MY_PORT}",
    font=("DejaVu Sans", 13, "bold"),
    fg="#d8d8d8",
    bg="#151515",
)
title_label.pack(fill="x", padx=20, pady=(8, 0))

language_label = tk.Label(
    root,
    text="Subtitle: Original",
    font=("DejaVu Sans", 12, "bold"),
    fg="#91c9ff",
    bg="#151515",
)
language_label.pack(fill="x", padx=20, pady=(4, 0))

lcd_label = tk.Label(
    root,
    text="Start the PC app, then play the selected video.",
    font=("DejaVu Sans", 30, "bold"),
    fg="white",
    bg="#151515",
    wraplength=DEFAULT_WRAP,
    height=3,
    justify="center",
)
lcd_label.pack(expand=True, fill="both", padx=20, pady=5)

media_frame = tk.Frame(root, bg="#151515")
media_frame.pack(fill="x", padx=20, pady=5)

play_btn = tk.Button(
    media_frame,
    text="Play",
    bg="#248a3d",
    fg="white",
    font=("DejaVu Sans", 12, "bold"),
    relief="flat",
    width=12,
)
play_btn.bind("<ButtonPress-1>", lambda _event: send_play())
play_btn.pack(side="left", padx=(0, 10))

pause_btn = tk.Button(
    media_frame,
    text="Pause",
    bg="#b83232",
    fg="white",
    font=("DejaVu Sans", 12, "bold"),
    relief="flat",
    width=12,
)
pause_btn.bind("<ButtonPress-1>", lambda _event: send_pause())
pause_btn.pack(side="left", padx=(0, 20))

player_frame = tk.Frame(root, bg="#151515")
player_frame.pack(side="bottom", fill="x", pady=(5, 18), padx=20)

time_label = tk.Label(player_frame, text="0:00 / 0:00", font=("DejaVu Sans Mono", 14, "bold"), fg="#aaaaaa", bg="#151515")
time_label.pack(side="left", padx=(0, 18))

canvas = tk.Canvas(player_frame, height=20, bg="#151515", highlightthickness=0, cursor="hand2")
canvas.pack(side="left", expand=True, fill="x")
canvas.bind("<ButtonPress-1>", on_press)
canvas.bind("<B1-Motion>", on_drag)
canvas.bind("<ButtonRelease-1>", on_release)

root.after(100, lambda: update_ui({"text": "Waiting...", "curr": 0, "total": 1}))
threading.Thread(target=receive_loop, daemon=True).start()

root.mainloop()
