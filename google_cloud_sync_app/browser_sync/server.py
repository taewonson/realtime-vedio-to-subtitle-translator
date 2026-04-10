from __future__ import annotations
"""브라우저 재생 동기화를 위한 로컬 HTTP 엔드포인트.

Chrome 확장 프로그램이 주기적으로 현재 재생 메타데이터를 전송하면,
이 서버는 payload를 최소한으로 검증/정규화한 뒤 코디네이터가 읽는
스레드 안전 큐로 전달합니다.
"""

import json
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from .models import BrowserState


class BrowserSyncServer:
    """확장 프로그램의 재생 상태를 수신하는 경량 로컬 HTTP 서버.

    확장 프로그램은 /sync 로 JSON payload를 보내고 /health 로 상태를 확인합니다.
    이 서버는 브라우저 상태를 역직렬화해 코디네이터용 스레드 안전 큐에
    넣는 역할만 담당합니다.
    """

    def __init__(self, host: str, port: int, state_queue: queue.Queue, status_queue: queue.Queue):
        self.host = host
        self.port = port
        self.state_queue = state_queue
        self.status_queue = status_queue
        self.server: Optional[ThreadingHTTPServer] = None
        self.thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """/health, /sync 엔드포인트를 가진 스레드형 HTTP 서버를 시작합니다."""
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _send_json(self, code: int, payload: dict) -> None:
                # 확장 프로그램 fetch(CORS 포함)까지 고려해
                # 모든 엔드포인트 응답 헤더를 일관되게 맞추는 헬퍼입니다.
                body = json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_OPTIONS(self) -> None:
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
                self.end_headers()

            def do_GET(self) -> None:
                if self.path == "/health":
                    self._send_json(200, {"ok": True})
                    return
                self._send_json(404, {"ok": False, "error": "not found"})

            def do_POST(self) -> None:
                if self.path != "/sync":
                    self._send_json(404, {"ok": False, "error": "not found"})
                    return

                try:
                    # content script가 짧은 주기로 보내는 JSON payload를
                    # 명시적 타입의 BrowserState로 변환합니다.
                    length = int(self.headers.get("Content-Length", "0"))
                    raw = self.rfile.read(length)
                    payload = json.loads(raw.decode("utf-8"))

                    state = BrowserState(
                        session_id=str(payload.get("sessionId", "")).strip(),
                        url=str(payload.get("url", "")).strip(),
                        current_time=float(payload.get("currentTime", 0.0)),
                        paused=bool(payload.get("paused", False)),
                        title=str(payload.get("title", "")).strip(),
                        playback_rate=float(payload.get("playbackRate", 1.0) or 1.0),
                        received_monotonic=time.monotonic(),
                    )
                    outer.state_queue.put(state)
                    self._send_json(200, {"ok": True})
                except Exception as exc:  # pragma: no cover - defensive error path
                    outer.status_queue.put(f"브라우저 상태 수신 오류: {exc}")
                    self._send_json(400, {"ok": False, "error": str(exc)})

            def log_message(self, format: str, *args) -> None:  # noqa: A003
                return

        self.server = ThreadingHTTPServer((self.host, self.port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.status_queue.put(f"브라우저 동기화 서버 시작: http://{self.host}:{self.port}/sync")

    def stop(self) -> None:
        """서버를 안전하게 종료합니다. 여러 번 호출해도 안전합니다."""
        if self.server:
            try:
                self.server.shutdown()
                self.server.server_close()
            finally:
                self.server = None
