import os
import uuid
import math
import time
import logging
from datetime import datetime

from flask import Flask, request, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

# JWT-авторизация
from flask_jwt_extended import (
    JWTManager, create_access_token,
    jwt_required, get_jwt_identity
)

#################################################################
# Инициализация приложения Flask
#################################################################

app = Flask(__name__)

# Секретные ключи (Можно переопределить в .env или Railway Variables)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'very-secret-flask-key')
app.config['JWT_SECRET_KEY'] = os.environ.get('JWT_SECRET_KEY', 'very-secret-jwt-key')

# Путь к SQLite
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///mydb.sqlite'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Чтобы юникод в JSON не экранировался
app.config['JSON_AS_ASCII'] = False

# Папка для загрузки файлов
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)
jwt = JWTManager(app)

logging.basicConfig(level=logging.DEBUG)


#################################################################
# Модели БД
#################################################################

class User(db.Model):
    """Пользователи (логин, пароль, координаты)."""
    __tablename__ = 'user'
    username = db.Column(db.String(80), primary_key=True)
    password = db.Column(db.String(200), nullable=False)  # храним хеш
    lat = db.Column(db.Float, default=0.0)
    lon = db.Column(db.Float, default=0.0)

    def to_json(self):
        return {
            "username": self.username,
            "lat": self.lat,
            "lon": self.lon
        }

# Многие-ко-многим "Group <-> User"
GroupMembers = db.Table(
    'group_members',
    db.Column('group_id', db.String(36), db.ForeignKey('group.id'), primary_key=True),
    db.Column('username', db.String(80), db.ForeignKey('user.username'), primary_key=True)
)

class Group(db.Model):
    """Группы (чат)."""
    __tablename__ = 'group'
    id = db.Column(db.String(36), primary_key=True)
    name = db.Column(db.String(120))
    owner = db.Column(db.String(80))  # user.username
    # Участники
    members = db.relationship("User", secondary=GroupMembers, backref="groups")

    def to_json(self):
        return {
            "id": self.id,
            "name": self.name,
            "owner": self.owner
        }

class Message(db.Model):
    """Сообщения в чате."""
    __tablename__ = 'message'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    group_id = db.Column(db.String(36), db.ForeignKey('group.id'))
    username = db.Column(db.String(80), db.ForeignKey('user.username'))
    text = db.Column(db.Text, default="")
    audio = db.Column(db.String(200), default=None)  # имя файла
    photo = db.Column(db.String(200), default=None)  # имя файла
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Route(db.Model):
    """Таблица маршрутов."""
    __tablename__ = 'route'
    id = db.Column(db.String(36), primary_key=True)
    name = db.Column(db.String(120))
    username = db.Column(db.String(80), db.ForeignKey('user.username'))
    distance = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class RoutePoint(db.Model):
    """Точки маршрута."""
    __tablename__ = 'route_point'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    route_id = db.Column(db.String(36), db.ForeignKey('route.id'))
    lat = db.Column(db.Float)
    lon = db.Column(db.Float)

class RouteComment(db.Model):
    """Комментарии к маршруту."""
    __tablename__ = 'route_comment'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    route_id = db.Column(db.String(36), db.ForeignKey('route.id'))
    lat = db.Column(db.Float)
    lon = db.Column(db.Float)
    text = db.Column(db.Text, default="")
    time = db.Column(db.String(50), default="")  # строка с датой
    photo = db.Column(db.String(200), default=None)  # если когда-нибудь будем прикреплять фото к комменту


#################################################################
# Вспомогательные функции
#################################################################

def save_file_if_present(field_name):
    """
    Если в request.files есть файл `field_name`, то сохраняем его в папку UPLOAD_FOLDER
    и возвращаем имя файла. Иначе None.
    """
    if field_name not in request.files:
        return None
    file = request.files[field_name]
    if file.filename == '':
        return None
    # Генерируем уникальное имя
    ext = os.path.splitext(file.filename)[1]
    new_name = f"{uuid.uuid4()}{ext}"
    path = os.path.join(app.config['UPLOAD_FOLDER'], new_name)
    file.save(path)
    return new_name


#################################################################
# Статическая отдача загруженных файлов (фото, аудио)
#################################################################

@app.route('/uploads/<filename>')
def serve_upload(filename):
    """Отдаём из папки 'uploads' запрошенный файл (фото, аудио)."""
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


#################################################################
# Регистрация / Логин
#################################################################

@app.route('/register', methods=['POST'])
def register():
    data = request.get_json() or {}
    username = data.get('username')
    password = data.get('password')
    if not username or not password:
        return jsonify({"error": "Username/password required"}), 400

    # Проверим, нет ли уже такого пользователя
    if User.query.filter_by(username=username).first():
        return jsonify({"error": "User already exists"}), 400

    # Создаём пользователя с хешированным паролем
    hashed_pw = generate_password_hash(password)
    user = User(username=username, password=hashed_pw)
    db.session.add(user)
    db.session.commit()

    return jsonify({"message": "Registration success"}), 200


@app.route('/login', methods=['POST'])
def login():
    data = request.get_json() or {}
    username = data.get('username')
    password = data.get('password')

    user = User.query.filter_by(username=username).first()
    if not user:
        return jsonify({"error": "Invalid username or password"}), 401

    # Сравниваем хеш
    if not check_password_hash(user.password, password):
        return jsonify({"error": "Invalid username or password"}), 401

    # Логин успешен => выдаём JWT
    access_token = create_access_token(identity=username)
    return jsonify({"access_token": access_token}), 200


#################################################################
# Работа с геолокациями
#################################################################

@app.route('/update_location', methods=['POST'])
@jwt_required()
def update_location():
    """
    Принимает JSON: {"lat":..., "lon":...}.
    username берём из JWT.
    """
    current_user = get_jwt_identity()
    data = request.get_json() or {}
    lat = data.get('lat')
    lon = data.get('lon')
    if lat is None or lon is None:
        return jsonify({"error": "lat/lon required"}), 400

    user = User.query.filter_by(username=current_user).first()
    if not user:
        return jsonify({"error": "No such user"}), 404

    user.lat = float(lat)
    user.lon = float(lon)
    db.session.commit()
    return jsonify({"status": "ok"}), 200


@app.route('/get_users', methods=['GET'])
@jwt_required()
def get_users():
    """
    Возвращает JSON со списком пользователей и их координат.
    Формат: {"users": [ {username, lat, lon}, ... ]}
    """
    users = User.query.all()
    resp = []
    for u in users:
        resp.append(u.to_json())
    return jsonify({"users": resp}), 200


#################################################################
# Группы / чат
#################################################################

@app.route('/create_group', methods=['POST'])
@jwt_required()
def create_group():
    """
    Принимает: {"name": "..."}
    Создаёт группу с id=uuid и owner = current_user.
    Добавляет owner в участники.
    """
    current_user = get_jwt_identity()
    data = request.get_json() or {}
    name = data.get('name', '').strip()
    if not name:
        return jsonify({"error": "Group name required"}), 400

    group_id = str(uuid.uuid4())
    new_group = Group(id=group_id, name=name, owner=current_user)
    db.session.add(new_group)
    db.session.commit()

    # Добавим создателя в участники
    user = User.query.filter_by(username=current_user).first()
    new_group.members.append(user)
    db.session.commit()

    return jsonify({"group_id": group_id}), 200


@app.route('/join_group', methods=['POST'])
@jwt_required()
def join_group():
    """
    Принимает: {"group_id": "..."}
    Текущий пользователь добавляется в участники группы.
    """
    current_user = get_jwt_identity()
    data = request.get_json() or {}
    group_id = data.get('group_id')
    group = Group.query.filter_by(id=group_id).first()
    if not group:
        return jsonify({"error": "No such group"}), 404

    user = User.query.filter_by(username=current_user).first()
    if not user:
        return jsonify({"error": "No such user"}), 404

    if user in group.members:
        return jsonify({"error": "Already in group"}), 400

    group.members.append(user)
    db.session.commit()
    return jsonify({"message": "Joined group"}), 200


@app.route('/leave_group', methods=['POST'])
@jwt_required()
def leave_group():
    """
    Принимает: {"group_id": "..."}
    Текущий пользователь выходит из группы.
    """
    current_user = get_jwt_identity()
    data = request.get_json() or {}
    group_id = data.get('group_id')
    group = Group.query.filter_by(id=group_id).first()
    if not group:
        return jsonify({"error": "No such group"}), 404

    user = User.query.filter_by(username=current_user).first()
    if not user:
        return jsonify({"error": "No such user"}), 404

    if user not in group.members:
        return jsonify({"error": "Not in group"}), 400

    group.members.remove(user)
    db.session.commit()
    return jsonify({"message": "Left group"}), 200


@app.route('/get_groups', methods=['GET'])
@jwt_required()
def get_groups():
    """
    Отдаёт список всех групп в формате [{"id":..., "name":..., "owner":...}, ...]
    """
    all_groups = Group.query.all()
    resp = []
    for g in all_groups:
        resp.append(g.to_json())
    return jsonify(resp), 200


@app.route('/send_message', methods=['POST'])
@jwt_required()
def send_message():
    """
    Принимает форму (multipart/form-data) c полями:
      - group_id (в form-data)
      - text (в form-data)
      - photo (в files) - необязательно
      - audio (в files) - необязательно
    """
    current_user = get_jwt_identity()

    group_id = request.form.get('group_id')
    text = request.form.get('text', '')

    group = Group.query.filter_by(id=group_id).first()
    if not group:
        return jsonify({"error": "No such group"}), 404

    # Проверка, что пользователь состоит в группе
    user = User.query.filter_by(username=current_user).first()
    if user not in group.members:
        return jsonify({"error": "You are not in this group"}), 403

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


@app.route('/get_messages', methods=['GET'])
@jwt_required()
def get_messages():
    """
    Параметры: group_id, after_id=0
    Возвращаем список сообщений.
    """
    current_user = get_jwt_identity()
    group_id = request.args.get('group_id')
    after_id = request.args.get('after_id', 0, type=int)

    group = Group.query.filter_by(id=group_id).first()
    if not group:
        return jsonify([]), 200

    # Проверка, что user в группе
    user = User.query.filter_by(username=current_user).first()
    if user not in group.members:
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
            "text": m.text,
        }
        if m.photo:
            item["photo"] = m.photo
        if m.audio:
            item["audio"] = m.audio
        resp.append(item)

    return jsonify(resp), 200


#################################################################
# Маршруты
#################################################################

@app.route('/upload_route', methods=['POST'])
@jwt_required()
def upload_route():
    """
    Принимаем JSON:
    {
      "route_name": "...",
      "distance": float,
      "route_points": [{"lat":..., "lon":...}],
      "route_comments": [{"lat":..., "lon":..., "text":"...", "time":"..."}]
    }
    Создаём в БД Route, Points, Comments.
    """
    current_user = get_jwt_identity()
    data = request.get_json() or {}

    route_name = data.get('route_name') or "Unnamed"
    distance = float(data.get('distance', 0.0))
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

    # Точки
    for p in route_points:
        lat = p.get("lat")
        lon = p.get("lon")
        if lat is not None and lon is not None:
            rp = RoutePoint(route_id=route_id, lat=lat, lon=lon)
            db.session.add(rp)

    # Комментарии
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


@app.route('/get_routes', methods=['GET'])
@jwt_required()
def get_routes():
    """
    Можно фильтровать по ?radius_km=... (не реализовано, при желании дописать).
    Возвращаем [{"name","distance","date","comments":[...]}]
    """
    # current_user = get_jwt_identity()  # если вдруг нужно использовать

    # radius_km = request.args.get('radius_km', None, type=float)
    # ... при желании можно делать фильтрацию ...

    routes = Route.query.order_by(Route.created_at.desc()).all()
    resp = []
    for rt in routes:
        comms = RouteComment.query.filter_by(route_id=rt.id).all()
        comm_list = []
        for c in comms:
            cc = {
                "lat": c.lat,
                "lon": c.lon,
                "text": c.text,
                "time": c.time
            }
            if c.photo:
                cc["photo"] = c.photo
            comm_list.append(cc)

        item = {
            "name": rt.name,
            "distance": rt.distance,
            "date": rt.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "comments": comm_list
        }
        resp.append(item)

    return jsonify(resp), 200


#################################################################
# SOS
#################################################################

@app.route('/sos', methods=['POST'])
@jwt_required()
def sos():
    """
    Принимает: {"lat":..., "lon":...}, username из токена.
    Логируем SOS, дальше логика — на ваше усмотрение.
    """
    current_user = get_jwt_identity()
    data = request.get_json() or {}
    lat = data.get('lat')
    lon = data.get('lon')
    logging.warning(f"SOS from {current_user}: lat={lat}, lon={lon}")
    # Здесь можно сохранить в БД или уведомлять...
    return jsonify({"message": "SOS received"}), 200


#################################################################
# Инициализация БД (без before_first_request!)
#################################################################

# Сразу после создания всего "app" и "db" — создаём таблицы
with app.app_context():
    db.create_all()


#################################################################
# Запуск (локально)
#################################################################
if __name__ == '__main__':
    # При локальном запуске (python main.py) поднимаем dev-сервер Flask
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True, host='0.0.0.0', port=port)
