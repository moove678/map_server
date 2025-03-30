import os
import uuid
import logging
from datetime import datetime

from flask import Flask, request, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from flask_jwt_extended import (
    JWTManager, create_access_token,
    jwt_required, get_jwt_identity
)

# ------------------ Конфигурация приложения ------------------
app = Flask(__name__)
app.config['SECRET_KEY'] = 'very-secret-flask-key'
app.config['JWT_SECRET_KEY'] = 'very-secret-jwt-key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///mydb.sqlite'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JSON_AS_ASCII'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)
jwt = JWTManager(app)
logging.basicConfig(level=logging.DEBUG)

# ------------------ Модели ------------------

class User(db.Model):
    __tablename__ = 'user'
    username = db.Column(db.String(80), primary_key=True)
    password = db.Column(db.String(200), nullable=False)  # хэш пароля
    lat = db.Column(db.Float, default=0.0)
    lon = db.Column(db.Float, default=0.0)

    def to_json(self):
        return {
            "username": self.username,
            "lat": self.lat,
            "lon": self.lon
        }

GroupMembers = db.Table(
    'group_members',
    db.Column('group_id', db.String(36), db.ForeignKey('group.id'), primary_key=True),
    db.Column('username', db.String(80), db.ForeignKey('user.username'), primary_key=True)
)

class Group(db.Model):
    __tablename__ = 'group'
    id = db.Column(db.String(36), primary_key=True)
    name = db.Column(db.String(120))
    owner = db.Column(db.String(80))  # username владельца

    members = db.relationship("User", secondary=GroupMembers, backref="groups")

    def to_json(self):
        return {
            "id": self.id,
            "name": self.name,
            "owner": self.owner
        }

class Message(db.Model):
    __tablename__ = 'message'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    group_id = db.Column(db.String(36), db.ForeignKey('group.id'))
    username = db.Column(db.String(80), db.ForeignKey('user.username'))
    text = db.Column(db.Text, default="")
    audio = db.Column(db.String(200), default=None)
    photo = db.Column(db.String(200), default=None)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Route(db.Model):
    __tablename__ = 'route'
    id = db.Column(db.String(36), primary_key=True)
    name = db.Column(db.String(120))
    username = db.Column(db.String(80), db.ForeignKey('user.username'))
    distance = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class RoutePoint(db.Model):
    __tablename__ = 'route_point'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    route_id = db.Column(db.String(36), db.ForeignKey('route.id'))
    lat = db.Column(db.Float)
    lon = db.Column(db.Float)

class RouteComment(db.Model):
    __tablename__ = 'route_comment'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    route_id = db.Column(db.String(36), db.ForeignKey('route.id'))
    lat = db.Column(db.Float)
    lon = db.Column(db.Float)
    text = db.Column(db.Text, default="")
    time = db.Column(db.String(50), default="")
    photo = db.Column(db.String(200), default=None)

# ------------------ Вспомогательные функции ------------------

def save_file_if_present(field_name):
    """
    Если файл присутствует в запросе, сохраняет его в UPLOAD_FOLDER и возвращает имя файла.
    """
    if field_name not in request.files:
        return None
    f = request.files[field_name]
    if not f.filename:
        return None
    ext = os.path.splitext(f.filename)[1]
    new_name = f"{uuid.uuid4()}{ext}"
    path = os.path.join(app.config['UPLOAD_FOLDER'], new_name)
    f.save(path)
    return new_name

@app.route('/uploads/<filename>')
def serve_upload(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# ------------------ Эндпоинты ------------------

# Регистрация
@app.route('/register', methods=['POST'])
def register():
    data = request.get_json() or {}
    username = data.get('username')
    password = data.get('password')
    if not username or not password:
        return jsonify({"error": "Username/password required"}), 400

    if User.query.filter_by(username=username).first():
        return jsonify({"error": "User already exists"}), 400

    hashed_pw = generate_password_hash(password)
    user = User(username=username, password=hashed_pw)
    db.session.add(user)
    db.session.commit()
    return jsonify({"message": "Registration success"}), 200

# Логин
@app.route('/login', methods=['POST'])
def login():
    data = request.get_json() or {}
    username = data.get('username')
    password = data.get('password')
    if not username or not password:
        return jsonify({"error": "No user/pass"}), 400

    user = User.query.filter_by(username=username).first()
    if not user or not check_password_hash(user.password, password):
        return jsonify({"error": "Invalid username or password"}), 401

    access_token = create_access_token(identity=username)
    return jsonify({"access_token": access_token}), 200

# Обновление местоположения
@app.route('/update_location', methods=['POST'])
@jwt_required()
def update_location():
    current_user = get_jwt_identity()
    data = request.get_json() or {}
    lat = data.get('lat')
    lon = data.get('lon')
    if lat is None or lon is None:
        return jsonify({"error": "lat/lon needed"}), 400

    u = User.query.filter_by(username=current_user).first()
    if not u:
        return jsonify({"error": "No such user"}), 404

    u.lat = float(lat)
    u.lon = float(lon)
    db.session.commit()
    return jsonify({"status": "ok"}), 200

# Получение списка пользователей
@app.route('/get_users', methods=['GET'])
@jwt_required()
def get_users():
    users = User.query.all()
    resp = [u.to_json() for u in users]
    return jsonify({"users": resp}), 200

# Создание группы
@app.route('/create_group', methods=['POST'])
@jwt_required()
def create_group():
    current_user = get_jwt_identity()
    data = request.get_json() or {}
    name = data.get('name', '').strip()
    if not name:
        return jsonify({"error": "Group name required"}), 400

    group_id = str(uuid.uuid4())
    g = Group(id=group_id, name=name, owner=current_user)
    db.session.add(g)
    db.session.commit()

    user = User.query.filter_by(username=current_user).first()
    g.members.append(user)
    db.session.commit()

    return jsonify({"group_id": group_id}), 200

# Присоединение к группе
@app.route('/join_group', methods=['POST'])
@jwt_required()
def join_group():
    current_user = get_jwt_identity()
    data = request.get_json() or {}
    group_id = data.get('group_id')
    g = Group.query.filter_by(id=group_id).first()
    if not g:
        return jsonify({"error": "No such group"}), 404

    user = User.query.filter_by(username=current_user).first()
    if user in g.members:
        return jsonify({"error": "Already in group"}), 400

    g.members.append(user)
    db.session.commit()
    return jsonify({"message": "Joined group"}), 200

# Выход из группы
@app.route('/leave_group', methods=['POST'])
@jwt_required()
def leave_group():
    current_user = get_jwt_identity()
    data = request.get_json() or {}
    group_id = data.get('group_id')
    g = Group.query.filter_by(id=group_id).first()
    if not g:
        return jsonify({"error": "No such group"}), 404

    user = User.query.filter_by(username=current_user).first()
    if user not in g.members:
        return jsonify({"error": "Not in group"}), 400

    g.members.remove(user)
    db.session.commit()
    return jsonify({"message": "Left group"}), 200

# Получение списка групп
@app.route('/get_groups', methods=['GET'])
@jwt_required()
def get_groups():
    groups = Group.query.all()
    data = [g.to_json() for g in groups]
    return jsonify(data), 200

# Отправка сообщения в группе
@app.route('/send_message', methods=['POST'])
@jwt_required()
def send_message():
    current_user = get_jwt_identity()
    group_id = request.form.get('group_id')
    text = request.form.get('text', '')

    g = Group.query.filter_by(id=group_id).first()
    if not g:
        return jsonify({"error": "No such group"}), 404

    user = User.query.filter_by(username=current_user).first()
    if user not in g.members:
        return jsonify({"error": "User not in group"}), 403

    photo_name = save_file_if_present('photo')
    audio_name = save_file_if_present('audio')

    msg = Message(
        group_id=group_id,
        username=current_user,
        text=text,
        photo=photo_name,
        audio=audio_name
    )
    db.session.add(msg)
    db.session.commit()
    return jsonify({"message": "ok"}), 200

# Получение сообщений чата
@app.route('/get_messages', methods=['GET'])
@jwt_required()
def get_messages():
    current_user = get_jwt_identity()
    group_id = request.args.get('group_id')
    after_id = request.args.get('after_id', 0, type=int)

    g = Group.query.filter_by(id=group_id).first()
    if not g:
        return jsonify([]), 200

    user = User.query.filter_by(username=current_user).first()
    if user not in g.members:
        return jsonify([]), 200

    msgs = Message.query.filter(
        Message.group_id == group_id,
        Message.id > after_id
    ).order_by(Message.id.asc()).all()

    resp = []
    for m in msgs:
        item = {
            "id": m.id,
            "username": m.username,
            "text": m.text
        }
        if m.photo:
            item["photo"] = m.photo
        if m.audio:
            item["audio"] = m.audio
        resp.append(item)
    return jsonify(resp), 200

# Загрузка маршрута
@app.route('/upload_route', methods=['POST'])
@jwt_required()
def upload_route():
    current_user = get_jwt_identity()
    data = request.get_json() or {}

    route_name = data.get('route_name', 'Unnamed')
    try:
        distance = float(data.get('distance', 0.0))
    except (ValueError, TypeError):
        distance = 0.0
    route_points = data.get('route_points', [])
    route_comments = data.get('route_comments', [])

    route_id = str(uuid.uuid4())
    r = Route(
        id=route_id,
        name=route_name,
        username=current_user,
        distance=distance
    )
    db.session.add(r)
    db.session.commit()

    for p in route_points:
        lat = p.get("lat")
        lon = p.get("lon")
        if lat is not None and lon is not None:
            rp = RoutePoint(route_id=route_id, lat=lat, lon=lon)
            db.session.add(rp)

    for c in route_comments:
        lat = c.get("lat")
        lon = c.get("lon")
        text = c.get("text", "")
        time_str = c.get("time", "")
        rc = RouteComment(
            route_id=route_id,
            lat=lat,
            lon=lon,
            text=text,
            time=time_str
        )
        db.session.add(rc)

    db.session.commit()
    return jsonify({"message": "route uploaded"}), 200

# Получение маршрутов
@app.route('/get_routes', methods=['GET'])
@jwt_required()
def get_routes():
    routes = Route.query.order_by(Route.created_at.desc()).all()
    resp = []
    for rt in routes:
        comments = RouteComment.query.filter_by(route_id=rt.id).all()
        comment_list = []
        for c in comments:
            cc = {
                "lat": c.lat,
                "lon": c.lon,
                "text": c.text,
                "time": c.time
            }
            if c.photo:
                cc["photo"] = c.photo
            comment_list.append(cc)
        item = {
            "name": rt.name,
            "distance": rt.distance,
            "date": rt.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "comments": comment_list
        }
        resp.append(item)
    return jsonify(resp), 200

# SOS-эндпоинт
@app.route('/sos', methods=['POST'])
@jwt_required()
def sos():
    current_user = get_jwt_identity()
    data = request.get_json() or {}
    lat = data.get('lat')
    lon = data.get('lon')
    logging.warning(f"SOS from {current_user}: lat={lat}, lon={lon}")
    return jsonify({"message": "SOS received"}), 200

# ------------------ Инициализация БД ------------------

def init_db():
    db.create_all()

# ------------------ Запуск приложения ------------------
if __name__ == "__main__":
    with app.app_context():
        init_db()
    app.run(debug=True, host="0.0.0.0", port=5000)
