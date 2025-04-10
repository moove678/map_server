import os
import uuid
import logging
import base64
import time
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from werkzeug.security import generate_password_hash, check_password_hash
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from dotenv import load_dotenv

# --- LOAD ENV ---
load_dotenv()
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")

# --- CONFIG ---
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv("SECRET_KEY", "supersecret")
app.config['JWT_SECRET_KEY'] = os.getenv("JWT_SECRET_KEY", "jwtsecret")
app.config['SQLALCHEMY_DATABASE_URI'] = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JSON_AS_ASCII'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# --- INIT ---
db = SQLAlchemy(app)
migrate = Migrate(app, db)
jwt = JWTManager(app)
logging.basicConfig(level=logging.DEBUG)

# --- MODELS ---
ignored_users = db.Table('ignored_users',
    db.Column('user', db.String(80), db.ForeignKey('users.username')),
    db.Column('ignored', db.String(80), db.ForeignKey('users.username'))
)

group_members = db.Table('group_members',
    db.Column('group_id', db.String(36), db.ForeignKey('groups.id')),
    db.Column('username', db.String(80), db.ForeignKey('users.username'))
)

class User(db.Model):
    __tablename__ = 'users'
    username = db.Column(db.String(80), primary_key=True)
    password = db.Column(db.String(200), nullable=False)
    lat = db.Column(db.Float, default=0.0)
    lon = db.Column(db.Float, default=0.0)
    last_seen = db.Column(db.Float, default=lambda: time.time())
    ignored = db.relationship(
        'User', secondary=ignored_users,
        primaryjoin=username == ignored_users.c.user,
        secondaryjoin=username == ignored_users.c.ignored,
        backref='ignored_by')

    def to_json(self):
        return {
            "username": self.username,
            "lat": self.lat,
            "lon": self.lon,
            "last_seen": self.last_seen
        }

class Group(db.Model):
    __tablename__ = 'groups'
    id = db.Column(db.String(36), primary_key=True)
    name = db.Column(db.String(120))
    owner = db.Column(db.String(80))
    avatar = db.Column(db.String(200))
    members = db.relationship("User", secondary=group_members, backref="groups")

    def to_json(self):
        return {
            "id": self.id,
            "name": self.name,
            "owner": self.owner,
            "avatar": self.avatar,
            "members": [m.username for m in self.members]
        }

class Message(db.Model):
    __tablename__ = 'messages'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.String(36), db.ForeignKey('groups.id'), nullable=True)
    sender = db.Column(db.String(80), db.ForeignKey('users.username'))
    receiver = db.Column(db.String(80), nullable=True)
    text = db.Column(db.Text, default="")
    audio = db.Column(db.String(200))
    photo = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_read = db.Column(db.Boolean, default=False)

class Route(db.Model):
    __tablename__ = 'routes'
    id = db.Column(db.String(36), primary_key=True)
    name = db.Column(db.String(120))
    username = db.Column(db.String(80), db.ForeignKey('users.username'))
    distance = db.Column(db.Float)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class RoutePoint(db.Model):
    __tablename__ = 'route_points'
    id = db.Column(db.Integer, primary_key=True)
    route_id = db.Column(db.String(36), db.ForeignKey('routes.id'))
    lat = db.Column(db.Float)
    lon = db.Column(db.Float)

class RouteComment(db.Model):
    __tablename__ = 'route_comments'
    id = db.Column(db.Integer, primary_key=True)
    route_id = db.Column(db.String(36), db.ForeignKey('routes.id'))
    lat = db.Column(db.Float)
    lon = db.Column(db.Float)
    text = db.Column(db.Text)
    time = db.Column(db.String(50))
    photo = db.Column(db.String(200))

# --- HELPERS ---
def save_file(field):
    if field not in request.files:
        return None
    f = request.files[field]
    if not f.filename:
        return None
    ext = os.path.splitext(f.filename)[1]
    new_name = f"{uuid.uuid4()}{ext}"
    f.save(os.path.join(app.config['UPLOAD_FOLDER'], new_name))
    return new_name

def save_base64(data, ext):
    try:
        decoded = base64.b64decode(data)
        name = f"{uuid.uuid4()}.{ext}"
        with open(os.path.join(app.config['UPLOAD_FOLDER'], name), "wb") as f:
            f.write(decoded)
        return name
    except Exception as e:
        logging.error(f"[Base64 error] {e}")
        return None

@app.route('/uploads/')
def serve_upload(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# --- AUTH ---
@app.route('/register', methods=['POST'])
def register():
    data = request.json
    if User.query.get(data['username']):
        return jsonify({"error": "exists"}), 400
    hashed = generate_password_hash(data['password'])
    user = User(username=data['username'], password=hashed)
    db.session.add(user)
    db.session.commit()
    return jsonify({"message": "registered"})

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    user = User.query.get(data['username'])
    if not user or not check_password_hash(user.password, data['password']):
        return jsonify({"error": "invalid"}), 401
    token = create_access_token(identity=user.username)
    return jsonify({"access_token": token})

# --- SOS ---
@app.route('/sos', methods=['POST'])
@jwt_required()
def sos():
    user = get_jwt_identity()
    data = request.json
    logging.warning(f"SOS from {user}: {data}")
    return jsonify({"message": "SOS received"})

# --- MAIN ---
if __name__ == '__main__':
    app.run(debug=True, host="0.0.0.0", port=5000)
