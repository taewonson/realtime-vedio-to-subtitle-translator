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

@app.route('/get_command')
def get_command():
    if state.pending_command:
        cmd = state.pending_command
        state.pending_command = None 
        return jsonify(cmd)
    return jsonify({"command": None})

# 💡 actual_duration 파라미터를 추가로 받습니다.
def run_server(subtitles_data, actual_duration=0.1):
    state.subtitles = subtitles_data
    
    # 💡 실제 길이가 있으면 그걸 쓰고, 없으면 (예외 상황) 자막의 마지막 시간 사용
    if actual_duration > 0:
        state.total_time = actual_duration
    elif subtitles_data:
        state.total_time = subtitles_data[-1]['end']
        
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    app.run(port=5000, use_reloader=False)