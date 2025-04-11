import os, uuid, logging, base64, time, math
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask, request, jsonify, send_from_directory
)
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from flask_jwt_extended import (
    JWTManager, create_access_token, jwt_required,
    get_jwt_identity, get_jwt, decode_token
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

db      = SQLAlchemy(app)
migrate = Migrate(app, db)
jwt     = JWTManager(app)
CORS(app)

# -------------------------------------------------------------------
# БАЗА
# -------------------------------------------------------------------
ignored_users = db.Table(
    "ignored_users",
    db.Column("user",    db.String(80), db.ForeignKey("users.username")),
    db.Column("ignored", db.String(80), db.ForeignKey("users.username")),
)

class User(db.Model):
    __tablename__ = "users"
    username        = db.Column(db.String(80), primary_key=True)
    password        = db.Column(db.String(200), nullable=False)
    lat             = db.Column(db.Float, default=0.0)
    lon             = db.Column(db.Float, default=0.0)
    last_seen       = db.Column(db.Float, default=lambda: time.time())
    current_token   = db.Column(db.String(500))
    current_device  = db.Column(db.String(100))
    ignored         = db.relationship(
        "User",
        secondary=ignored_users,
        primaryjoin=username == ignored_users.c.user,
        secondaryjoin=username == ignored_users.c.ignored,
        backref="ignored_by",
    )
    groups          = db.relationship("Group", secondary="group_members", back_populates="members")

    def to_json(self):
        return dict(
            username=self.username,
            lat=self.lat,
            lon=self.lon,
            last_seen=self.last_seen
        )

class Message(db.Model):
    __tablename__ = "messages"
    id        = db.Column(db.Integer, primary_key=True)
    group_id  = db.Column(db.String(36))
    sender    = db.Column(db.String(80), db.ForeignKey("users.username"))
    receiver  = db.Column(db.String(80))
    text      = db.Column(db.Text, default="")
    audio     = db.Column(db.String(200))
    photo     = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Group(db.Model):
    __tablename__ = "groups"
    id      = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name    = db.Column(db.String(100), unique=True, nullable=False)
    members = db.relationship("User", secondary="group_members", back_populates="groups")

group_members = db.Table(
    "group_members",
    db.Column("group_id", db.String(36), db.ForeignKey("groups.id")),
    db.Column("username", db.String(80), db.ForeignKey("users.username")),
)

# ---------- SOS ----------
class Sos(db.Model):
    __tablename__ = "sos"
    id        = db.Column(db.Integer, primary_key=True)
    username  = db.Column(db.String(80), db.ForeignKey("users.username"))
    lat       = db.Column(db.Float)
    lon       = db.Column(db.Float)
    comment   = db.Column(db.Text, default="")
    created   = db.Column(db.DateTime, default=datetime.utcnow)

# ---------- ROUTES ----------
class Route(db.Model):
    __tablename__ = "routes"
    id        = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name      = db.Column(db.String(120))
    owner     = db.Column(db.String(80), db.ForeignKey("users.username"))
    created   = db.Column(db.DateTime, default=datetime.utcnow)
    points    = db.relationship("RoutePoint",  backref="route", cascade="all,delete")
    comments  = db.relationship("RouteComment", backref="route", cascade="all,delete")

class RoutePoint(db.Model):
    __tablename__ = "route_points"
    id       = db.Column(db.Integer, primary_key=True)
    route_id = db.Column(db.String(36), db.ForeignKey("routes.id"))
    lat      = db.Column(db.Float)
    lon      = db.Column(db.Float)
    ts       = db.Column(db.DateTime, default=datetime.utcnow)

class RouteComment(db.Model):
    __tablename__ = "route_comments"
    id       = db.Column(db.Integer, primary_key=True)
    route_id = db.Column(db.String(36), db.ForeignKey("routes.id"))
    lat      = db.Column(db.Float)
    lon      = db.Column(db.Float)
    text     = db.Column(db.Text)
    photo    = db.Column(db.String(200))
    ts       = db.Column(db.DateTime, default=datetime.utcnow)

# -------------------------------------------------------------------
# HELPERS
# -------------------------------------------------------------------
def single_device_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if app.config["ALLOW_NO_DEVICE"]:
            return fn(*args, **kwargs)
        identity = get_jwt_identity()
        jti      = get_jwt().get("jti")
        device   = request.headers.get("X-Device-ID")
        user     = User.query.get(identity)
        if not user or user.current_token != jti or user.current_device != device:
            return jsonify(error="Unauthorized"), 403
        return fn(*args, **kwargs)
    return wrapper

def save_base64(data: str, ext: str):
    try:
        filename = f"{uuid.uuid4()}.{ext}"
        path     = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))
        return filename
    except Exception as e:
        logging.error(f"save_base64: {e}")
        return None

def save_uploaded_file(file_storage, ext):
    try:
        filename = f"{uuid.uuid4()}.{ext}"
        path     = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        file_storage.save(path)
        return filename
    except Exception as e:
        logging.error(f"save_uploaded_file: {e}")
        return None

@app.route("/uploads/<filename>")
def serve_upload(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

# -------------------------------------------------------------------
# AUTH
# -------------------------------------------------------------------
@app.route("/register", methods=["POST"])
def register():
    data = request.json
    if User.query.get(data["username"]):
        return jsonify(error="exists"), 400
    user = User(username=data["username"],
                password=generate_password_hash(data["password"]))
    db.session.add(user)
    db.session.commit()
    return jsonify(message="registered")

@app.route("/login", methods=["POST"])
def login():
    data      = request.json
    device_id = data.get("device_id")
    user      = User.query.get(data["username"])
    if not user or not check_password_hash(user.password, data["password"]):
        return jsonify(error="invalid"), 401
    if user.current_token and user.current_device != device_id and not app.config["ALLOW_NO_DEVICE"]:
        return jsonify(error="already_logged"), 403
    token = create_access_token(identity=user.username)
    user.current_token = decode_token(token)["jti"]
    user.current_device = device_id
    db.session.commit()
    return jsonify(access_token=token)

@app.route("/logout", methods=["POST"])
@jwt_required()
def logout():
    user = User.query.get(get_jwt_identity())
    if user:
        user.current_token = None
        user.current_device = None
        db.session.commit()
    return jsonify(message="logged out")

# -------------------------------------------------------------------
# LOCATION
# -------------------------------------------------------------------
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
    return jsonify(status="ok")

@app.route("/get_users", methods=["GET"])
@jwt_required()
@single_device_required
def get_users():
    now      = time.time()
    current  = get_jwt_identity()
    user     = User.query.get(current)
    result   = []
    for u in User.query.all():
        if u.username == current or u.username in [i.username for i in user.ignored]:
            continue
        if now - u.last_seen > 30:
            continue
        result.append(u.to_json())
    return jsonify(result)

# -------------------------------------------------------------------
# SOS
# -------------------------------------------------------------------
@app.route("/sos", methods=["POST"])
@jwt_required()
@single_device_required
def sos():
    data = request.json or {}
    entry = Sos(
        username=get_jwt_identity(),
        lat=data.get("lat", 0.0),
        lon=data.get("lon", 0.0),
        comment=data.get("comment", "")
    )
    db.session.add(entry)
    db.session.commit()
    logging.warning(f"SOS from {entry.username} @ {entry.lat},{entry.lon}")
    return jsonify(message="sos_saved", id=entry.id)

# -------------------------------------------------------------------
# CHAT (групповой и личный)  —  send_message / get_messages уже есть
# -------------------------------------------------------------------
def _store_media_from_request():
    """Возвращает (audio_filename, photo_filename) из multipart запроса"""
    audio_fn = photo_fn = None
    if "audio" in request.files:
        audio_fn = save_uploaded_file(request.files["audio"], "wav")
    if "photo" in request.files:
        photo_fn = save_uploaded_file(request.files["photo"], "jpg")
    return audio_fn, photo_fn

@app.route("/send_message", methods=["POST"])
@jwt_required()
@single_device_required
def send_message():
    sender = get_jwt_identity()
    if request.content_type.startswith("multipart"):
        form = request.form
        group_id = form.get("group_id")
        receiver = form.get("receiver")
        text     = form.get("text", "")
        audio_fn, photo_fn = _store_media_from_request()
    else:
        data     = request.json
        group_id = data.get("group_id")
        receiver = data.get("receiver")
        text     = data.get("text", "")
        audio_fn = data.get("audio")
        photo_fn = data.get("photo")
    msg = Message(group_id=group_id, sender=sender, receiver=receiver,
                  text=text, audio=audio_fn, photo=photo_fn)
    db.session.add(msg)
    db.session.commit()
    return jsonify(message="sent", id=msg.id)

@app.route("/get_messages", methods=["GET"])
@jwt_required()
@single_device_required
def get_messages():
    group_id = request.args.get("group_id")
    after_id = int(request.args.get("after_id", 0))
    q = Message.query.filter_by(group_id=group_id)
    if after_id:
        q = q.filter(Message.id > after_id)
    msgs = q.order_by(Message.created_at.asc()).all()
    return jsonify([{
        "id": m.id, "sender": m.sender, "receiver": m.receiver,
        "text": m.text, "audio": m.audio, "photo": m.photo,
        "created_at": m.created_at.isoformat()
    } for m in msgs])

# -------------------------------------------------------------------
# ROUTES
# -------------------------------------------------------------------
@app.route("/create_route", methods=["POST"])
@jwt_required()
@single_device_required
def create_route():
    data  = request.json
    name  = data.get("name", f"Route {datetime.utcnow().isoformat()}")
    route = Route(name=name, owner=get_jwt_identity())
    db.session.add(route)
    db.session.commit()
    return jsonify(message="route_created", route_id=route.id)

@app.route("/add_route_point", methods=["POST"])
@jwt_required()
@single_device_required
def add_route_point():
    data     = request.json
    route_id = data["route_id"]
    lat      = data["lat"]
    lon      = data["lon"]
    pt = RoutePoint(route_id=route_id, lat=lat, lon=lon)
    db.session.add(pt)
    db.session.commit()
    return jsonify(message="point_added", id=pt.id)

@app.route("/add_route_comment", methods=["POST"])
@jwt_required()
@single_device_required
def add_route_comment():
    # поддержка multipart (фото) и JSON
    if request.content_type.startswith("multipart"):
        form       = request.form
        route_id   = form["route_id"]
        lat, lon   = float(form["lat"]), float(form["lon"])
        text       = form.get("text", "")
        photo_fn,_ = _store_media_from_request()
    else:
        data       = request.json
        route_id   = data["route_id"]
        lat, lon   = data["lat"], data["lon"]
        text       = data.get("text", "")
        photo_fn   = data.get("photo")
    cm = RouteComment(route_id=route_id, lat=lat, lon=lon, text=text, photo=photo_fn)
    db.session.add(cm)
    db.session.commit()
    return jsonify(message="comment_added", id=cm.id)

@app.route("/get_route", methods=["GET"])
@jwt_required()
@single_device_required
def get_route():
    rid   = request.args.get("route_id")
    route = Route.query.get(rid)
    if not route:
        return jsonify(error="not_found"), 404
    return jsonify(
        id=route.id,
        name=route.name,
        owner=route.owner,
        created=route.created.isoformat(),
        route_points=[dict(lat=p.lat, lon=p.lon, ts=p.ts.isoformat()) for p in route.points],
        route_comments=[dict(lat=c.lat, lon=c.lon, text=c.text, photo=c.photo, ts=c.ts.isoformat()) for c in route.comments]
    )

@app.route("/list_routes", methods=["GET"])
@jwt_required()
@single_device_required
def list_routes():
    user = get_jwt_identity()
    routes = Route.query.filter_by(owner=user).all()
    return jsonify([{
        "id": r.id, "name": r.name, "created": r.created.isoformat(),
        "points": len(r.points), "comments": len(r.comments)
    } for r in routes])

# -------------------------------------------------------------------
# GROUPS  (остались без изменений)
# -------------------------------------------------------------------
@app.route("/create_group", methods=["POST"])
@jwt_required()
@single_device_required
def create_group():
    data = request.json
    name = data.get("name")
    if Group.query.filter_by(name=name).first():
        return jsonify(error="exists"), 400
    group = Group(name=name)
    group.members.append(User.query.get(get_jwt_identity()))
    db.session.add(group)
    db.session.commit()
    return jsonify(message="group_created", group_id=group.id)

@app.route("/join_group", methods=["POST"])
@jwt_required()
@single_device_required
def join_group():
    data = request.json
    group = Group.query.get(data.get("group_id"))
    if not group:
        return jsonify(error="not_found"), 404
    user = User.query.get(get_jwt_identity())
    if user not in group.members:
        group.members.append(user)
        db.session.commit()
    return jsonify(message="joined")

@app.route("/leave_group", methods=["POST"])
@jwt_required()
@single_device_required
def leave_group():
    data  = request.json
    group = Group.query.get(data.get("group_id"))
    if not group:
        return jsonify(error="not_found"), 404
    user = User.query.get(get_jwt_identity())
    if user in group.members:
        group.members.remove(user)
        db.session.commit()
    return jsonify(message="left")

@app.route("/my_groups", methods=["GET"])
@jwt_required()
@single_device_required
def my_groups():
    user = User.query.get(get_jwt_identity())
    return jsonify([{"id": g.id, "name": g.name} for g in user.groups])

# -------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
