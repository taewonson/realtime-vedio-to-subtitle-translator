from flask import Flask, request, jsonify
from flask_cors import CORS
import logging

app = Flask(__name__)
CORS(app)

class SharedState:
    subtitles = []
    current_texts = {} # 이제 여러 언어가 담긴 딕셔너리를 저장합니다.

state = SharedState()

@app.route('/sync', methods=['POST'])
def sync_time():
    data = request.get_json()
    current_time = data.get('time')
    
    if current_time is not None:
        found_texts = {}
        for sub in state.subtitles:
            if sub['start'] <= current_time <= sub['end']:
                found_texts = sub['texts']
                break
        
        state.current_texts = found_texts if found_texts else {}
            
    return jsonify({"status": "success"})

def run_server(subtitles_data):
    state.subtitles = subtitles_data
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    app.run(port=5000, use_reloader=False)