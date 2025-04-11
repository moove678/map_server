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
from werkzeug.security import generate_password_hash, check_password_hash
from flask_jwt_extended import (
    JWTManager, create_access_token, jwt_required,
    get_jwt_identity, get_jwt
)
from dotenv import load_dotenv

# -------------------------------------------------------------
#  CONFIG & INITIALISATION
# -------------------------------------------------------------
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
)
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

logging.basicConfig(level=logging.INFO)
db = SQLAlchemy(app)
migrate = Migrate(app, db)
jwt = JWTManager(app)

# -------------------------------------------------------------
#  DATABASE MODELS
# -------------------------------------------------------------
ignored_users = db.Table(
    "ignored_users",
    db.Column("user", db.String(80), db.ForeignKey("users.username")),
    db.Column("ignored", db.String(80), db.ForeignKey("users.username")),
)

group_members = db.Table(
    "group_members",
    db.Column("group_id", db.String(36), db.ForeignKey("groups.id")),
    db.Column("username", db.String(80), db.ForeignKey("users.username")),
)


class User(db.Model):
    __tablename__ = "users"
    username      = db.Column(db.String(80), primary_key=True)
    password      = db.Column(db.String(200), nullable=False)
    lat           = db.Column(db.Float, default=0.0)
    lon           = db.Column(db.Float, default=0.0)
    last_seen     = db.Column(db.Float, default=lambda: time.time())
    current_token = db.Column(db.String(500))
    current_device= db.Column(db.String(100))
    ignored       = db.relationship(
        "User",
        secondary=ignored_users,
        primaryjoin=username == ignored_users.c.user,
        secondaryjoin=username == ignored_users.c.ignored,
        backref="ignored_by",
    )

    def to_json(self):
        return {
            "username": self.username,
            "lat": self.lat,
            "lon": self.lon,
            "last_seen": self.last_seen,
        }


class Group(db.Model):
    __tablename__ = "groups"
    id      = db.Column(db.String(36), primary_key=True)
    name    = db.Column(db.String(120))
    owner   = db.Column(db.String(80))
    avatar  = db.Column(db.String(200))
    members = db.relationship("User", secondary=group_members, backref="groups")

    def to_json(self):
        return {
            "id": self.id,
            "name": self.name,
            "owner": self.owner,
            "avatar": self.avatar,
            "members": [m.username for m in self.members],
        }


class Message(db.Model):
    __tablename__ = "messages"
    id         = db.Column(db.Integer, primary_key=True)
    group_id   = db.Column(db.String(36), db.ForeignKey("groups.id"))
    sender     = db.Column(db.String(80), db.ForeignKey("users.username"))
    receiver   = db.Column(db.String(80))
    text       = db.Column(db.Text, default="")
    audio      = db.Column(db.String(200))
    photo      = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_read    = db.Column(db.Boolean, default=False)


class Route(db.Model):
    __tablename__ = "routes"
    id        = db.Column(db.String(36), primary_key=True)
    name      = db.Column(db.String(120))
    username  = db.Column(db.String(80), db.ForeignKey("users.username"))
    distance  = db.Column(db.Float)
    created_at= db.Column(db.DateTime, default=datetime.utcnow)


class RoutePoint(db.Model):
    __tablename__ = "route_points"
    id      = db.Column(db.Integer, primary_key=True)
    route_id= db.Column(db.String(36), db.ForeignKey("routes.id"))
    lat     = db.Column(db.Float)
    lon     = db.Column(db.Float)


class RouteComment(db.Model):
    __tablename__ = "route_comments"
    id      = db.Column(db.Integer, primary_key=True)
    route_id= db.Column(db.String(36), db.ForeignKey("routes.id"))
    lat     = db.Column(db.Float)
    lon     = db.Column(db.Float)
    text    = db.Column(db.Text)
    time    = db.Column(db.String(50))
    photo   = db.Column(db.String(200))

# -------------------------------------------------------------
#  AUTH HELPERS
# -------------------------------------------------------------
def single_device_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        identity = get_jwt_identity()
        jti      = get_jwt()["jti"]
        device   = request.headers.get("X-Device-ID")
        user     = User.query.get(identity)
        if not user or user.current_token != jti or user.current_device != device:
            return jsonify({"error": "Unauthorized. Active session exists elsewhere."}), 403
        return fn(*args, **kwargs)
    return wrapper

# -------------------------------------------------------------
#  AUTH ROUTES
# -------------------------------------------------------------
@app.route("/register", methods=["POST"])
def register():
    data = request.json
    if User.query.get(data["username"]):
        return jsonify({"error": "exists"}), 400
    user = User(
        username=data["username"],
        password=generate_password_hash(data["password"]),
    )
    db.session.add(user)
    db.session.commit()
    return jsonify({"message": "registered"})

@app.route("/login", methods=["POST"])
def login():
    data = request.json
    device_id = data.get("device_id")
    if not device_id:
        return jsonify({"error": "Missing device_id"}), 400

    user = User.query.get(data["username"])
    if not user or not check_password_hash(user.password, data["password"]):
        return jsonify({"error": "invalid"}), 401

    if user.current_token and user.current_device != device_id:
        return jsonify({"error": "User already logged in on another device"}), 403

    token = create_access_token(identity=user.username)
    user.current_token = get_jwt()["jti"]
    user.current_device = device_id
    db.session.commit()
    return jsonify({"access_token": token})

@app.route("/logout", methods=["POST"])
@jwt_required()
def logout():
    user = User.query.get(get_jwt_identity())
    if user:
        user.current_token = None
        user.current_device = None
        db.session.commit()
    return jsonify({"message": "logged out"})

# -------------------------------------------------------------
#  USER LOCATION & LISTING
# -------------------------------------------------------------
@app.route("/update_location", methods=["POST"])
@jwt_required()
@single_device_required
def update_location():
    user = User.query.get(get_jwt_identity())
    data = request.json
    user.lat = data["lat"]
    user.lon = data["lon"]
    user.last_seen = time.time()
    db.session.commit()
    return jsonify({"status": "ok"})

@app.route("/get_users", methods=["GET"])
@jwt_required()
@single_device_required
def get_users():
    current = get_jwt_identity()
    user    = User.query.get(current)
    now     = time.time()
    visible = []
    for u in User.query.all():
        if u.username == current or u.username in [i.username for i in user.ignored]:
            continue
        if now - u.last_seen > 30:
            continue
        visible.append(u.to_json())
    return jsonify(visible)

# -------------------------------------------------------------
#  IGNORE & SOS
# -------------------------------------------------------------
@app.route("/ignore_user", methods=["POST"])
@jwt_required()
@single_device_required
def ignore_user():
    user   = User.query.get(get_jwt_identity())
    target = request.json.get("username")
    target_user = User.query.get(target)
    if target_user and target_user not in user.ignored:
        user.ignored.append(target_user)
        db.session.commit()
    return jsonify({"ignored": target})

@app.route("/sos", methods=["POST"])
@jwt_required()
@single_device_required
def sos():
    sender = get_jwt_identity()
    logging.warning(f"SOS from {sender}: {request.json}")
    return jsonify({"message": "SOS received"})

# -------------------------------------------------------------
#  CHAT ROUTES
# -------------------------------------------------------------
@app.route("/send_message", methods=["POST"])
@jwt_required()
@single_device_required
def send_message():
    data   = request.json
    sender = get_jwt_identity()
    msg = Message(
        group_id = data.get("group_id"),
        sender   = sender,
        receiver = data.get("receiver"),
        text     = data.get("text", ""),
        audio    = data.get("audio"),
        photo    = data.get("photo"),
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
            "id"        : m.id,
            "sender"    : m.sender,
            "receiver"  : m.receiver,
            "text"      : m.text,
            "audio"     : m.audio,
            "photo"     : m.photo,
            "created_at": m.created_at.isoformat()
        } for m in messages
    ])

# -------------------------------------------------------------
#  FILE UPLOADS
# -------------------------------------------------------------
@app.route("/uploads/<filename>")
def serve_upload(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# -------------------------------------------------------------
#  MAIN
# -------------------------------------------------------------
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
