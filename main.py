import os
import uuid
import logging
import base64
import time
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, request, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from flask_jwt_extended import (
    JWTManager,
    create_access_token,
    jwt_required,
    get_jwt_identity,
    get_jwt,
    decode_token
)
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY", "supersecret"),
    JWT_SECRET_KEY=os.getenv("JWT_SECRET_KEY", "jwtsecret"),
    JWT_ACCESS_TOKEN_EXPIRES=timedelta(days=1),
    SQLALCHEMY_DATABASE_URI=(
        f"postgresql://{os.getenv('PGUSER')}:{os.getenv('PGPASSWORD')}@"
        f"{os.getenv('PGHOST')}:{os.getenv('PGPORT')}/{os.getenv('PGDATABASE')}"
    ),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    JSON_AS_ASCII=False,
    UPLOAD_FOLDER=os.getenv("UPLOAD_FOLDER", "uploads"),
    ALLOW_NO_DEVICE=os.getenv("ALLOW_NO_DEVICE", "false").lower() == "true"
)

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
logging.basicConfig(level=logging.INFO)

db = SQLAlchemy(app)
migrate = Migrate(app, db)
jwt = JWTManager(app)
CORS(app)

# ---------------------- DATABASE MODELS ----------------------

ignored_users = db.Table(
    "ignored_users",
    db.Column("user", db.String(80), db.ForeignKey("users.username")),
    db.Column("ignored", db.String(80), db.ForeignKey("users.username")),
)

class User(db.Model):
    __tablename__ = "users"
    username = db.Column(db.String(80), primary_key=True)
    password = db.Column(db.String(200), nullable=False)
    lat = db.Column(db.Float, default=0.0)
    lon = db.Column(db.Float, default=0.0)
    last_seen = db.Column(db.Float, default=lambda: time.time())
    current_token = db.Column(db.String(500))
    current_device = db.Column(db.String(100))
    ignored = db.relationship(
        "User",
        secondary=ignored_users,
        primaryjoin=username == ignored_users.c.user,
        secondaryjoin=username == ignored_users.c.ignored,
        backref="ignored_by",
    )
    # Связь с группами (создано для поддержки функционала групп)
    groups = db.relationship("Group", secondary="group_members", back_populates="members")

    def to_json(self):
        return {
            "username": self.username,
            "lat": self.lat,
            "lon": self.lon,
            "last_seen": self.last_seen,
        }

class Message(db.Model):
    __tablename__ = "messages"
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.String(36))
    sender = db.Column(db.String(80), db.ForeignKey("users.username"))
    receiver = db.Column(db.String(80))
    text = db.Column(db.Text, default="")
    audio = db.Column(db.String(200))
    photo = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# Новая модель Group и вспомогательная таблица для членов группы
class Group(db.Model):
    __tablename__ = "groups"
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(100), unique=True, nullable=False)
    members = db.relationship("User", secondary="group_members", back_populates="groups")

group_members = db.Table(
    "group_members",
    db.Column("group_id", db.String(36), db.ForeignKey("groups.id")),
    db.Column("username", db.String(80), db.ForeignKey("users.username")),
)

# ---------------------- HELPERS ----------------------

def single_device_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if app.config["ALLOW_NO_DEVICE"]:
            return fn(*args, **kwargs)
        identity = get_jwt_identity()
        jti = get_jwt().get("jti")
        device = request.headers.get("X-Device-ID")
        user = User.query.get(identity)
        if not user or user.current_token != jti or user.current_device != device:
            return jsonify({"error": "Unauthorized. Active session exists elsewhere."}), 403
        return fn(*args, **kwargs)
    return wrapper

def save_base64(data, ext):
    try:
        decoded = base64.b64decode(data)
        name = f"{uuid.uuid4()}.{ext}"
        path = os.path.join(app.config["UPLOAD_FOLDER"], name)
        with open(path, "wb") as f:
            f.write(decoded)
        return name
    except Exception as e:
        logging.error(f"[Base64 Error] {e}")
        return None

def save_uploaded_file(file_storage, ext):
    try:
        filename = f"{uuid.uuid4()}.{ext}"
        path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        file_storage.save(path)
        return filename
    except Exception as e:
        logging.error(f"[File Save Error] {e}")
        return None

@app.route("/uploads/<filename>")
def serve_upload(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

# ---------------------- AUTH ----------------------

@app.route("/register", methods=["POST"])
def register():
    try:
        data = request.json
        if User.query.get(data["username"]):
            return jsonify({"error": "exists"}), 400
        user = User(username=data["username"], password=generate_password_hash(data["password"]))
        db.session.add(user)
        db.session.commit()
        return jsonify({"message": "registered"})
    except Exception as e:
        logging.error(f"[Register Error] {e}")
        return jsonify({"error": "server"}), 500

@app.route("/login", methods=["POST"])
def login():
    try:
        data = request.json
        device_id = data.get("device_id")
        user = User.query.get(data["username"])
        if not user or not check_password_hash(user.password, data["password"]):
            return jsonify({"error": "invalid"}), 401
        if user.current_token and user.current_device != device_id and not app.config["ALLOW_NO_DEVICE"]:
            return jsonify({"error": "User already logged in on another device"}), 403
        token = create_access_token(identity=user.username)
        decoded_token = decode_token(token)
        user.current_token = decoded_token["jti"]
        user.current_device = device_id
        db.session.commit()
        return jsonify({"access_token": token})
    except Exception as e:
        logging.error(f"[Login Error] {e}")
        return jsonify({"error": "server"}), 500

@app.route("/logout", methods=["POST"])
@jwt_required()
def logout():
    user = User.query.get(get_jwt_identity())
    if user:
        user.current_token = None
        user.current_device = None
        db.session.commit()
    return jsonify({"message": "logged out"})

# ---------------------- LOCATION ----------------------

@app.route("/update_location", methods=["POST"])
@jwt_required()
@single_device_required
def update_location():
    data = request.json
    user = User.query.get(get_jwt_identity())
    user.lat = data["lat"]
    user.lon = data["lon"]
    user.last_seen = time.time()
    db.session.commit()
    return jsonify({"status": "ok"})

@app.route("/get_users", methods=["GET"])
@jwt_required()
@single_device_required
def get_users():
    now = time.time()
    current = get_jwt_identity()
    user = User.query.get(current)
    result = []
    for u in User.query.all():
        if u.username == current or u.username in [i.username for i in user.ignored]:
            continue
        if now - u.last_seen > 30:
            continue
        result.append(u.to_json())
    return jsonify(result)

# ---------------------- SOS & CHAT ----------------------

@app.route("/sos", methods=["POST"])
@jwt_required()
@single_device_required
def sos():
    user = get_jwt_identity()
    data = request.json
    logging.warning(f"[SOS] {user}: {data}")
    return jsonify({"message": "SOS received"})

@app.route("/send_message", methods=["POST"])
@jwt_required()
@single_device_required
def send_message():
    sender = get_jwt_identity()
    group_id = None
    receiver = None
    text = ""
    audio_filename = None
    photo_filename = None

    # Если запрос отправлен как multipart/form-data
    if request.content_type.startswith("multipart/form-data"):
        form_data = request.form
        group_id = form_data.get("group_id")
        receiver = form_data.get("receiver")
        text = form_data.get("text", "")
        if "audio" in request.files:
            # Можно определить расширение по типу файла или указать явное, здесь "wav" используется как пример
            audio_filename = save_uploaded_file(request.files["audio"], "wav")
        if "photo" in request.files:
            # Используем "jpg" как пример; при необходимости можно определить динамически
            photo_filename = save_uploaded_file(request.files["photo"], "jpg")
    else:
        # Если запрос приходит в JSON-формате
        data = request.json
        group_id = data.get("group_id")
        receiver = data.get("receiver")
        text = data.get("text", "")
        audio_filename = data.get("audio")
        photo_filename = data.get("photo")

    msg = Message(
        group_id=group_id,
        sender=sender,
        receiver=receiver,
        text=text,
        audio=audio_filename,
        photo=photo_filename,
    )
    db.session.add(msg)
    db.session.commit()
    return jsonify({"message": "sent"})

@app.route("/get_messages", methods=["GET"])
@jwt_required()
@single_device_required
def get_messages():
    group_id = request.args.get("group_id")
    messages = Message.query.filter_by(group_id=group_id).order_by(Message.created_at.asc()).all()
    return jsonify([
        {
            "id": m.id,
            "sender": m.sender,
            "receiver": m.receiver,
            "text": m.text,
            "audio": m.audio,
            "photo": m.photo,
            "created_at": m.created_at.isoformat()
        } for m in messages
    ])

# ---------------------- GROUPS ----------------------
# Роуты для работы с группами

@app.route("/create_group", methods=["POST"])
@jwt_required()
@single_device_required
def create_group():
    data = request.json
    name = data.get("name")
    if Group.query.filter_by(name=name).first():
        return jsonify({"error": "Group already exists"}), 400
    group = Group(name=name)
    user = User.query.get(get_jwt_identity())
    group.members.append(user)
    db.session.add(group)
    db.session.commit()
    return jsonify({"message": "Group created", "group_id": group.id})

@app.route("/join_group", methods=["POST"])
@jwt_required()
@single_device_required
def join_group():
    data = request.json
    group_id = data.get("group_id")
    group = Group.query.get(group_id)
    if not group:
        return jsonify({"error": "Group not found"}), 404
    user = User.query.get(get_jwt_identity())
    if user in group.members:
        return jsonify({"message": "Already a member"})
    group.members.append(user)
    db.session.commit()
    return jsonify({"message": "Joined group"})

@app.route("/leave_group", methods=["POST"])
@jwt_required()
@single_device_required
def leave_group():
    data = request.json
    group_id = data.get("group_id")
    group = Group.query.get(group_id)
    if not group:
        return jsonify({"error": "Group not found"}), 404
    user = User.query.get(get_jwt_identity())
    if user not in group.members:
        return jsonify({"message": "Not a member"})
    group.members.remove(user)
    db.session.commit()
    return jsonify({"message": "Left group"})

@app.route("/my_groups", methods=["GET"])
@jwt_required()
@single_device_required
def my_groups():
    user = User.query.get(get_jwt_identity())
    groups = [{"id": g.id, "name": g.name} for g in user.groups]
    return jsonify(groups)

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
