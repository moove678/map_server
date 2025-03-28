import os
import uuid
import logging
import json  # <--- добавил
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate

# Настройка логирования
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(message)s")

# Создаем приложение Flask
app = Flask(__name__)

# Конфигурация базы данных
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
db_path = os.path.join(BASE_DIR, 'database.db')
app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{db_path}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Папка для загрузок файлов
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# Инициализация базы данных и миграций
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# ==================== Модели ====================

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)

class Group(db.Model):
    id = db.Column(db.String(36), primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    owner = db.Column(db.String(80), db.ForeignKey('user.username'), nullable=False)

class GroupMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.String(36), db.ForeignKey('group.id'), nullable=False)
    username = db.Column(db.String(80), db.ForeignKey('user.username'), nullable=False)
    text = db.Column(db.Text, nullable=True)
    audio_filename = db.Column(db.String(200), nullable=True)
    photo_filename = db.Column(db.String(200), nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class Route(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), db.ForeignKey('user.username'), nullable=False)
    route_name = db.Column(db.String(100), nullable=False)
    route_points = db.Column(db.Text, nullable=False)  # JSON сериализованный список точек
    route_comments = db.Column(db.Text, nullable=True) # JSON сериализованный список комментариев
    is_public = db.Column(db.Boolean, default=True)
    distance = db.Column(db.Float, default=0)  # в км
    duration = db.Column(db.Float, default=0)  # в секундах
    avg_speed = db.Column(db.Float, default=0) # в км/ч
    date = db.Column(db.Date, default=datetime.utcnow().date)

# ==================== Эндпоинты ====================

@app.route('/ping', methods=['GET'])
def ping():
    return jsonify({"status": "ok"}), 200

@app.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    username = data.get("username")
    password = data.get("password")
    user = User.query.filter_by(username=username).first()
    if user and user.password == password:
        return jsonify({"message": "Login successful"}), 200
    return jsonify({"error": "Invalid credentials"}), 401

@app.route('/register', methods=['POST'])
def register():
    data = request.get_json()
    username = data.get("username")
    password = data.get("password")
    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400
    if User.query.filter_by(username=username).first():
        return jsonify({"error": "User already exists"}), 400
    user = User(username=username, password=password)
    db.session.add(user)
    db.session.commit()
    return jsonify({"message": "Registration successful"}), 200

@app.route('/update_location', methods=['POST'])
def update_location():
    data = request.get_json()
    username = data.get("username")
    lat = data.get("lat")
    lon = data.get("lon")
    logging.info(f"Location update for {username}: {lat}, {lon}")
    return jsonify({"message": "Location updated"}), 200

# Группы и групповой чат
@app.route('/create_group', methods=['POST'])
def create_group():
    data = request.get_json()
    group_name = data.get("name")
    owner = data.get("owner")
    if not group_name or not owner:
        return jsonify({"error": "Group name and owner required"}), 400
    group_id = str(uuid.uuid4())
    group = Group(id=group_id, name=group_name, owner=owner)
    db.session.add(group)
    db.session.commit()
    return jsonify({"group_id": group_id, "name": group_name}), 200

@app.route('/get_groups', methods=['GET'])
def get_groups():
    groups_query = Group.query.all()
    groups_list = [{"id": g.id, "name": g.name, "owner": g.owner} for g in groups_query]
    return jsonify(groups_list), 200

@app.route('/join_group', methods=['POST'])
def join_group():
    data = request.get_json()
    username = data.get("username")
    group_id = data.get("group_id")
    group = Group.query.filter_by(id=group_id).first()
    if not group:
        return jsonify({"error": "Group not found"}), 404
    # Для простоты, если пользователь уже является владельцем или участником, ничего не делаем.
    # Реальная реализация может использовать таблицу связей.
    return jsonify({"message": "Joined group"}), 200

@app.route('/delete_group', methods=['POST'])
def delete_group():
    data = request.get_json()
    group_id = data.get("group_id")
    group = Group.query.filter_by(id=group_id).first()
    if group:
        db.session.delete(group)
        db.session.commit()
        return jsonify({"message": "Group deleted"}), 200
    return jsonify({"error": "Group not found"}), 404

@app.route('/send_message', methods=['POST'])
def send_message():
    username = request.form.get("username")
    group_id = request.form.get("group_id")
    text = request.form.get("text", "")
    audio = request.files.get("audio")
    photo = request.files.get("photo")
    message = GroupMessage(
        group_id=group_id,
        username=username,
        text=text,
        timestamp=datetime.utcnow()
    )
    if audio:
        audio_filename = f"audio_{uuid.uuid4().hex}.dat"
        audio_path = os.path.join(app.config['UPLOAD_FOLDER'], audio_filename)
        audio.save(audio_path)
        message.audio_filename = audio_filename
    if photo:
        photo_filename = f"photo_{uuid.uuid4().hex}.jpg"
        photo_path = os.path.join(app.config['UPLOAD_FOLDER'], photo_filename)
        photo.save(photo_path)
        message.photo_filename = photo_filename
    db.session.add(message)
    db.session.commit()
    return jsonify({"message": "Message sent", "id": message.id}), 200

@app.route('/get_messages', methods=['GET'])
def get_messages():
    group_id = request.args.get("group_id")
    after_id = int(request.args.get("after_id", 0))
    messages = GroupMessage.query.filter(GroupMessage.group_id==group_id, GroupMessage.id > after_id).all()
    msgs = []
    for m in messages:
        msg = {
            "id": m.id,
            "username": m.username,
            "text": m.text,
            "timestamp": m.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        }
        if m.audio_filename:
            msg["audio"] = m.audio_filename
        if m.photo_filename:
            msg["photo"] = m.photo_filename
        msgs.append(msg)
    return jsonify(msgs), 200

# Маршруты
@app.route('/upload_route', methods=['POST'])
def upload_route():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No route data provided"}), 400
    # Ожидаем, что data содержит: route_name, route_points (JSON), route_comments (JSON), is_public, distance, duration, avg_speed
    data["date"] = datetime.now().strftime("%Y-%m-%d")
    route = Route(
        username=data.get("username", "unknown"),
        route_name=data.get("route_name", "Unnamed Route"),
        route_points=json.dumps(data.get("route_points", []), ensure_ascii=False),  # <--- ensure_ascii=False
        route_comments=json.dumps(data.get("route_comments", []), ensure_ascii=False),
        is_public=data.get("is_public", True),
        distance=data.get("distance", 0),
        duration=data.get("duration", 0),
        avg_speed=data.get("avg_speed", 0),
        date=datetime.utcnow().date()
    )
    db.session.add(route)
    db.session.commit()
    return jsonify({"message": "Route uploaded"}), 200

@app.route('/get_routes', methods=['GET'])
def get_routes():
    routes_query = Route.query.all()
    routes_list = []
    for r in routes_query:
        routes_list.append({
            "id": r.id,
            "username": r.username,
            "name": r.route_name,
            "route_points": json.loads(r.route_points),
            "route_comments": json.loads(r.route_comments) if r.route_comments else [],
            "is_public": r.is_public,
            "distance": r.distance,
            "duration": r.duration,
            "avg_speed": r.avg_speed,
            "date": r.date.strftime("%Y-%m-%d")
        })
    return jsonify(routes_list), 200

@app.route('/uploads/<filename>', methods=['GET'])
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
