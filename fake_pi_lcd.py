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


# 💡 컴퓨터 테스트용 fake_pi_lcd
# 라즈베리파이를 끄고 컴퓨터로 테스트할 때 사용합니다.
# pi_lcd.py와 동일한 기능이지만 fullscreen=False로 시작합니다.
PC_IP = os.getenv("SUBTITLE_PC_IP", "127.0.0.1").strip() or "127.0.0.1"
MY_PORT = env_int("SUBTITLE_PI_PORT", 5005)
PC_PORT = env_int("SUBTITLE_PC_COMMAND_PORT", 5006)
LCD_WIDTH, LCD_HEIGHT = 1024, 600
GEOMETRY = os.getenv("SUBTITLE_LCD_GEOMETRY", f"{LCD_WIDTH}x{LCD_HEIGHT}").strip() or f"{LCD_WIDTH}x{LCD_HEIGHT}"
FULLSCREEN = False  # ← 컴퓨터 테스트용이므로 fullscreen 비활성화
DEFAULT_WRAP = 940
FONT_FAMILY = "Malgun Gothic"

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
    "original": "원본",
    "ko": "한국어",
    "en": "영어",
    "ja": "일본어",
    "zh": "중국어",
    "de": "독일어",
}


def chunk_text(value, width):
    return [value[i : i + width] for i in range(0, len(value), width)]


def build_subtitle_pages(text, max_chars_per_line=28):
    # 긴 자막을 작은 LCD에서도 읽기 좋게 두 줄 단위 페이지로 나눕니다.
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
        title_label.config(text=f"현재 재생: {title}")

    language_label.config(text=f"자막: {LANG_LABELS.get(lang_code, lang_code)}")

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

    if source_key != last_source_text or display_text != last_display_text:
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
    while True:
        try:
            data, _ = sock_receive.recvfrom(4096)
            payload = json.loads(data.decode("utf-8"))
            root.after(0, lambda p=payload: update_ui(p))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            pass


root = tk.Tk()
root.title("Subtitle LCD (Fake Raspberry Pi) - TEST MODE")
root.geometry(GEOMETRY)
root.configure(bg="#151515")
root.attributes("-topmost", True)
if FULLSCREEN:
    root.attributes("-fullscreen", True)
root.bind("<Escape>", lambda _event: root.attributes("-fullscreen", False))
root.bind("q", lambda _event: root.destroy())

lang_frame = tk.Frame(root, bg="#222222")
lang_frame.pack(fill="x")

for label, code in [("한국어", "ko"), ("영어", "en"), ("일본어", "ja"), ("독일어", "de"), ("원본", "original")]:
    btn = tk.Button(
        lang_frame,
        text=label,
        command=lambda c=code: send_language(c),
        bg="#333333",
        fg="white",
        activebackground="#444444",
        activeforeground="white",
        font=(FONT_FAMILY, 14, "bold"),
        relief="flat",
        padx=18,
        pady=12,
        width=8,
    )
    btn.pack(side="left", padx=4, pady=5)

close_btn = tk.Button(
    lang_frame,
    text="✕",
    command=root.destroy,
    bg="#8b0000",
    fg="white",
    font=(FONT_FAMILY, 16, "bold"),
    relief="flat",
    padx=12,
    pady=8,
    width=3,
)
close_btn.pack(side="right", padx=8, pady=5)

title_label = tk.Label(
    root,
    text=f"자막 대기 중...  PC: {PC_IP}  UDP: {MY_PORT}",
    font=(FONT_FAMILY, 13, "bold"),
    fg="#d8d8d8",
    bg="#151515",
)
title_label.pack(fill="x", padx=20, pady=(8, 0))

language_label = tk.Label(
    root,
    text="자막: 원본",
    font=(FONT_FAMILY, 12, "bold"),
    fg="#91c9ff",
    bg="#151515",
)
language_label.pack(fill="x", padx=20, pady=(4, 0))

lcd_label = tk.Label(
    root,
    text="PC 앱을 시작한 후 영상을 재생하세요.",
    font=(FONT_FAMILY, 30, "bold"),
    fg="white",
    bg="#151515",
    wraplength=DEFAULT_WRAP,
    height=3,
    justify="center",
)
lcd_label.pack(expand=True, fill="both", padx=20, pady=5)

media_frame = tk.Frame(root, bg="#151515")
media_frame.pack(fill="x", padx=20, pady=5)

control_frame = tk.Frame(root, bg="#151515")
control_frame.pack(fill="x", padx=20, pady=(5, 10))

play_btn = tk.Button(
    control_frame,
    text="재생",
    bg="#248a3d",
    fg="white",
    font=(FONT_FAMILY, 15, "bold"),
    relief="flat",
    height=2,
    padx=20,
    pady=12,
)
play_btn.config(command=send_play)
play_btn.pack(side="left", expand=True, fill="x", padx=(0, 10))

pause_btn = tk.Button(
    control_frame,
    text="정지",
    bg="#b83232",
    fg="white",
    font=(FONT_FAMILY, 15, "bold"),
    relief="flat",
    height=2,
    padx=20,
    pady=12,
)
pause_btn.config(command=send_pause)
pause_btn.pack(side="left", expand=True, fill="x", padx=(0, 0))

player_frame = tk.Frame(root, bg="#151515")
player_frame.pack(side="bottom", fill="x", pady=(5, 18), padx=20)

time_label = tk.Label(player_frame, text="0:00 / 0:00", font=(FONT_FAMILY, 14, "bold"), fg="#aaaaaa", bg="#151515")
time_label.pack(side="left", padx=(0, 18))

canvas = tk.Canvas(player_frame, height=20, bg="#151515", highlightthickness=0, cursor="hand2")
canvas.pack(side="left", expand=True, fill="x")
canvas.bind("<ButtonPress-1>", on_press)
canvas.bind("<B1-Motion>", on_drag)
canvas.bind("<ButtonRelease-1>", on_release)

root.after(100, lambda: update_ui({"text": "대기 중...", "curr": 0, "total": 1}))
threading.Thread(target=receive_loop, daemon=True).start()

root.mainloop()