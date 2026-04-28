from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

app = Flask(__name__)

DB_USER = "joalstjr"
DB_PASS = "0000"
DB_HOST = "35.190.229.11"
DB_NAME = "testdb"

app.config['SQLALCHEMY_DATABASE_URI'] = f'mysql+pymysql://{DB_USER}:{DB_PASS}@{DB_HOST}:3306/{DB_NAME}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'gcp-security-key-2026'

db = SQLAlchemy(app)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(120))
    created_at = db.Column(db.DateTime, default=datetime.now)

@app.route('/register', methods=['POST'])
def register():
    data = request.json
    if User.query.filter_by(username=data['username']).first():
        return jsonify({"msg": "이미 존재하는 ID입니다."}), 400
    hashed_pw = generate_password_hash(data['password'], method='pbkdf2:sha256')
    new_user = User(
        username=data['username'], 
        password=hashed_pw, 
        email=data.get('email')
    )
    db.session.add(new_user)
    db.session.commit()
    return jsonify({"msg": f"회원가입 성공\nID: {data['username']}"}), 201

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    user = User.query.filter_by(username=data['username']).first()
    if user and check_password_hash(user.password, data['password']):
        return jsonify({
            "msg": "로그인 성공", 
            "email": user.email,
            "username": user.username
        }), 200
    return jsonify({"msg": "아이디 또는 비밀번호 불일치"}), 401

@app.route('/update', methods=['POST'])
def update():
    data = request.json
    user = User.query.filter_by(username=data['username']).first()
    if user and check_password_hash(user.password, data['current_password']):
        if data.get('new_password'):
            user.password = generate_password_hash(data['new_password'], method='pbkdf2:sha256')
        if data.get('new_email'):
            user.email = data['new_email']
        db.session.commit()
        return jsonify({"msg": "회원정보 수정 완료"}), 200
    return jsonify({"msg": "기존 비밀번호를 확인해주세요"}), 401

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, port=5000)