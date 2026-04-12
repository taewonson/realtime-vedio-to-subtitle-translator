from flask import Flask, request, jsonify
from flask_cors import CORS
import logging

app = Flask(__name__)
CORS(app)

class SharedState:
    subtitles = []
    current_texts = {}
    current_time = 0.0
    total_time = 0.1
    # 💡 명령 대기열 (LCD에서 날아온 명령을 잠시 보관하는 곳)
    pending_command = None 

state = SharedState()

@app.route('/sync', methods=['POST'])
def sync_time():
    data = request.get_json()
    current_time = data.get('time')
    
    if current_time is not None:
        state.current_time = current_time
        found_texts = {}
        for sub in state.subtitles:
            if sub['start'] <= current_time <= sub['end']:
                found_texts = sub['texts']
                break
        state.current_texts = found_texts if found_texts else {}
            
    return jsonify({"status": "success"})

# 💡 크롬 확장 프로그램이 0.5초마다 들러서 명령을 가져가는 창구
@app.route('/get_command')
def get_command():
    if state.pending_command:
        cmd = state.pending_command
        state.pending_command = None # 💡 크롬이 가져가면 명령 삭제 (중복 방지)
        return jsonify(cmd)
    return jsonify({"command": None})

def run_server(subtitles_data):
    state.subtitles = subtitles_data
    if subtitles_data:
        state.total_time = subtitles_data[-1]['end']
        
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    app.run(port=5000, use_reloader=False)