import os, uuid, logging, time, math
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, request, jsonify, send_from_directory
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
        f"postgresql://{os.getenv('PGUSER')}:{os.getenv('PGPASSWORD')}"
        f"@{os.getenv('PGHOST')}:{os.getenv('PGPORT')}/{os.getenv('PGDATABASE')}"
    ),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    JSON_AS_ASCII=False,
    UPLOAD_FOLDER=os.getenv("UPLOAD_FOLDER", "uploads"),
    ALLOW_NO_DEVICE=os.getenv("ALLOW_NO_DEVICE", "false").lower() == "true",
)
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

logging.basicConfig(level=logging.INFO)
db = SQLAlchemy(app)
migrate = Migrate(app, db)
jwt = JWTManager(app)
CORS(app)

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
        "User", secondary=ignored_users,
        primaryjoin=username == ignored_users.c.user,
        secondaryjoin=username == ignored_users.c.ignored,
        backref="ignored_by",
    )
    groups = db.relationship("Group", secondary="group_members", back_populates="members")

    def to_json(self):
        return dict(username=self.username, lat=self.lat, lon=self.lon, last_seen=self.last_seen)

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

class Group(db.Model):
    __tablename__ = "groups"
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(100), unique=True, nullable=False)
    lat = db.Column(db.Float, default=0.0)
    lon = db.Column(db.Float, default=0.0)
    is_public = db.Column(db.Boolean, default=True)
    created = db.Column(db.DateTime, default=datetime.utcnow)  # <= добавлено
    members = db.relationship("User", secondary="group_members", back_populates="groups")

group_members = db.Table(
    "group_members",
    db.Column("group_id", db.String(36), db.ForeignKey("groups.id")),
    db.Column("username", db.String(80), db.ForeignKey("users.username")),
)

class Invite(db.Model):
    __tablename__ = "invites"
    id = db.Column(db.Integer, primary_key=True)
    from_user = db.Column(db.String(80))
    to_user = db.Column(db.String(80))
    group_id = db.Column(db.String(36))
    created = db.Column(db.DateTime, default=datetime.utcnow)

class Sos(db.Model):
    __tablename__ = "sos"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), db.ForeignKey("users.username"))
    lat = db.Column(db.Float)
    lon = db.Column(db.Float)
    comment = db.Column(db.Text, default="")
    photo = db.Column(db.String(200))
    created = db.Column(db.DateTime, default=datetime.utcnow)

class Route(db.Model):
    __tablename__ = "routes"
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(120))
    owner = db.Column(db.String(80), db.ForeignKey("users.username"))
    created = db.Column(db.DateTime, default=datetime.utcnow)
    points = db.relationship("RoutePoint", backref="route", cascade="all,delete")
    comments = db.relationship("RouteComment", backref="route", cascade="all,delete")

class RoutePoint(db.Model):
    __tablename__ = "route_points"
    id = db.Column(db.Integer, primary_key=True)
    route_id = db.Column(db.String(36), db.ForeignKey("routes.id"))
    lat = db.Column(db.Float)
    lon = db.Column(db.Float)
    ts = db.Column(db.DateTime, default=datetime.utcnow)

class RouteComment(db.Model):
    __tablename__ = "route_comments"
    id = db.Column(db.Integer, primary_key=True)
    route_id = db.Column(db.String(36), db.ForeignKey("routes.id"))
    lat = db.Column(db.Float)
    lon = db.Column(db.Float)
    text = db.Column(db.Text)
    photo = db.Column(db.String(200))
    ts = db.Column(db.DateTime, default=datetime.utcnow)
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
            return jsonify(error="Unauthorized"), 403
        return fn(*args, **kwargs)
    return wrapper

def save_uploaded_file(file_storage, ext):
    filename = f"{uuid.uuid4()}.{ext}"
    path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    file_storage.save(path)
    return filename

def _store_media_from_request():
    audio_fn = photo_fn = None
    if "audio" in request.files:
        audio_fn = save_uploaded_file(request.files["audio"], "wav")
    if "photo" in request.files:
        photo_fn = save_uploaded_file(request.files["photo"], "jpg")
    return audio_fn, photo_fn

@app.route("/uploads/<filename>")
def serve_upload(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

# ---------------------- AUTH ----------------------

@app.route("/register", methods=["POST"])
def register():
    d = request.json
    if User.query.get(d["username"]):
        return jsonify(error="exists"), 400
    db.session.add(User(username=d["username"], password=generate_password_hash(d["password"])))
    db.session.commit()
    return jsonify(message="registered")

@app.route("/login", methods=["POST"])
def login():
    d = request.json
    device_id = d.get("device_id")
    user = User.query.get(d["username"])
    if not user or not check_password_hash(user.password, d["password"]):
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
    u = User.query.get(get_jwt_identity())
    if u:
        u.current_token = u.current_device = None
        db.session.commit()
    return jsonify(message="logged out")

# ---------------------- LOCATION ----------------------

@app.route("/update_location", methods=["POST"])
@jwt_required()
@single_device_required
def update_location():
    d = request.json
    u = User.query.get(get_jwt_identity())
    u.lat, u.lon = d["lat"], d["lon"]
    u.last_seen = time.time()
    db.session.commit()
    return jsonify(status="ok")

@app.route("/get_users", methods=["GET"])
@jwt_required()
@single_device_required
def get_users():
    now = time.time()
    me = get_jwt_identity()
    cur = User.query.get(me)
    res = []
    for u in User.query.all():
        if u.username == me or u.username in [i.username for i in cur.ignored]:
            continue
        if now - u.last_seen > 180:
            continue
        res.append(u.to_json())
    return jsonify(res)

# ---------------------- GROUP HELPERS ----------------------

def _remove_from_all_groups(user: User):
    for g in list(user.groups):
        g.members.remove(user)
        if len(g.members) == 0:
            if g.created and datetime.utcnow() - g.created < timedelta(minutes=1):
                continue
            db.session.delete(g)

# ---------------------- GROUP ENDPOINTS ----------------------

@app.route("/create_group", methods=["POST"])
@jwt_required()
@single_device_required
def create_group():
    d = request.json
    if Group.query.filter_by(name=d["name"]).first():
        return jsonify(error="exists"), 400
    usr = User.query.get(get_jwt_identity())
    _remove_from_all_groups(usr)
    grp = Group(name=d["name"], lat=d.get("lat", 0.0), lon=d.get("lon", 0.0), is_public=d.get("is_public", True))
    grp.members.append(usr)
    db.session.add(grp)
    db.session.commit()
    return jsonify(group_id=grp.id)

@app.route("/join_group", methods=["POST"])
@jwt_required()
@single_device_required
def join_group():
    d = request.json
    grp = Group.query.get(d["group_id"])
    if not grp:
        return jsonify(error="not_found"), 404
    usr = User.query.get(get_jwt_identity())
    _remove_from_all_groups(usr)
    grp.members.append(usr)
    db.session.commit()
    return jsonify(ok=True)

@app.route("/leave_group", methods=["POST"])
@jwt_required()
@single_device_required
def leave_group():
    d = request.json
    grp = Group.query.get(d["group_id"])
    if not grp:
        return jsonify(error="not_found"), 404
    usr = User.query.get(get_jwt_identity())
    if usr in grp.members:
        grp.members.remove(usr)
    if len(grp.members) == 0 and (not grp.created or datetime.utcnow() - grp.created >= timedelta(minutes=1)):
        db.session.delete(grp)
    db.session.commit()
    return jsonify(ok=True)

@app.route("/my_groups", methods=["GET"])
@jwt_required()
@single_device_required
def my_groups():
    usr = User.query.get(get_jwt_identity())
    return jsonify([{"id": g.id, "name": g.name} for g in usr.groups])
def _haversine_km(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * \
        math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

@app.route("/public_groups", methods=["GET"])
@jwt_required()
@single_device_required
def public_groups():
    lat = float(request.args.get("lat", 0.0))
    lon = float(request.args.get("lon", 0.0))
    radius = float(request.args.get("radius_km", 5))
    groups = []
    for g in Group.query.filter_by(is_public=True).all():
        if _haversine_km(lat, lon, g.lat, g.lon) <= radius:
            groups.append({"id": g.id, "name": g.name,
                           "lat": g.lat, "lon": g.lon,
                           "members": len(g.members)})
    return jsonify(groups)

# ---------------------- MESSAGING ----------------------

@app.route("/send_message", methods=["POST"])
@jwt_required()
@single_device_required
def send_message():
    sender = get_jwt_identity()
    if request.content_type and request.content_type.startswith("multipart"):
        form = request.form
        group_id = form.get("group_id")
        text = form.get("text", "")
        audio_fn, photo_fn = _store_media_from_request()
    else:
        d = request.json
        group_id = d.get("group_id")
        text = d.get("text", "")
        audio_fn = d.get("audio")
        photo_fn = d.get("photo")
    msg = Message(group_id=group_id, sender=sender,
                  text=text, audio=audio_fn, photo=photo_fn)
    db.session.add(msg)
    db.session.commit()
    return jsonify(id=msg.id)

@app.route("/get_messages", methods=["GET"])
@jwt_required()
@single_device_required
def get_messages():
    gid = request.args.get("group_id")
    after = int(request.args.get("after_id", 0))
    q = Message.query.filter_by(group_id=gid)
    if after:
        q = q.filter(Message.id > after)
    msgs = q.order_by(Message.created_at.asc()).all()
    return jsonify([{
        "id": m.id, "from": m.sender, "text": m.text,
        "photo": m.photo, "audio": m.audio,
        "created_at": m.created_at.isoformat()
    } for m in msgs])

@app.route("/send_private_message", methods=["POST"])
@jwt_required()
@single_device_required
def send_private_message():
    sender = get_jwt_identity()
    if request.content_type and request.content_type.startswith("multipart"):
        form = request.form
        to_user = form.get("to_user")
        text = form.get("text", "")
        audio_fn, photo_fn = _store_media_from_request()
    else:
        d = request.json
        to_user = d.get("to_user")
        text = d.get("text", "")
        audio_fn = d.get("audio")
        photo_fn = d.get("photo")
    msg = Message(sender=sender, receiver=to_user,
                  text=text, audio=audio_fn, photo=photo_fn)
    db.session.add(msg)
    db.session.commit()
    return jsonify(id=msg.id)

@app.route("/get_private_messages", methods=["GET"])
@jwt_required()
@single_device_required
def get_private_messages():
    me = get_jwt_identity()
    after = int(request.args.get("after_id", 0))
    q = Message.query.filter_by(receiver=me)
    if after:
        q = q.filter(Message.id > after)
    msgs = q.order_by(Message.created_at.asc()).all()
    return jsonify([{
        "id": m.id, "sender": m.sender, "text": m.text,
        "photo": m.photo, "audio": m.audio,
        "created_at": m.created_at.isoformat()
    } for m in msgs])

# ---------------------- SOS ----------------------

@app.route("/sos", methods=["POST"])
@jwt_required()
@single_device_required
def sos():
    if request.content_type and request.content_type.startswith("multipart"):
        f = request.form
        lat = float(f["lat"])
        lon = float(f["lon"])
        comment = f.get("comment", "")
        photo_fn, _ = _store_media_from_request()
    else:
        d = request.json or {}
        lat = d.get("lat", 0.0)
        lon = d.get("lon", 0.0)
        comment = d.get("comment", "")
        photo_fn = d.get("photo")
    entry = Sos(username=get_jwt_identity(), lat=lat, lon=lon,
                comment=comment, photo=photo_fn)
    db.session.add(entry)
    db.session.commit()
    logging.warning("SOS from %s @ %s,%s", entry.username, entry.lat, entry.lon)
    return jsonify(id=entry.id)

# ---------------------- ROUTES ----------------------

@app.route("/create_route", methods=["POST"])
@jwt_required()
@single_device_required
def create_route():
    d = request.json
    name = d.get("name", f"Route {datetime.utcnow().isoformat()}")
    route = Route(name=name, owner=get_jwt_identity())
    db.session.add(route)
    db.session.commit()
    return jsonify(route_id=route.id)

@app.route("/add_route_point", methods=["POST"])
@jwt_required()
@single_device_required
def add_route_point():
    d = request.json
    pt = RoutePoint(route_id=d["route_id"], lat=d["lat"], lon=d["lon"])
    db.session.add(pt)
    db.session.commit()
    return jsonify(id=pt.id)

@app.route("/add_route_comment", methods=["POST"])
@jwt_required()
@single_device_required
def add_route_comment():
    if request.content_type and request.content_type.startswith("multipart"):
        f = request.form
        route_id = f["route_id"]
        lat = float(f["lat"])
        lon = float(f["lon"])
        text = f.get("text", "")
        photo_fn, _ = _store_media_from_request()
    else:
        d = request.json
        route_id = d["route_id"]
        lat, lon = d["lat"], d["lon"]
        text = d.get("text", "")
        photo_fn = d.get("photo")
    cm = RouteComment(route_id=route_id, lat=lat, lon=lon,
                      text=text, photo=photo_fn)
    db.session.add(cm)
    db.session.commit()
    return jsonify(id=cm.id)

@app.route("/get_route", methods=["GET"])
@jwt_required()
@single_device_required
def get_route():
    rid = request.args.get("route_id")
    route = Route.query.get(rid)
    if not route:
        return jsonify(error="not_found"), 404
    return jsonify(
        id=route.id,
        name=route.name,
        owner=route.owner,
        created=route.created.isoformat(),
        route_points=[dict(lat=p.lat, lon=p.lon, ts=p.ts.isoformat()) for p in route.points],
        route_comments=[dict(lat=c.lat, lon=c.lon, text=c.text,
                             photo=c.photo, ts=c.ts.isoformat()) for c in route.comments]
    )

@app.route("/list_routes", methods=["GET"])
@jwt_required()
@single_device_required
def list_routes():
    me = get_jwt_identity()
    routes = Route.query.filter_by(owner=me).all()
    return jsonify([{
        "id": r.id,
        "name": r.name,
        "created": r.created.isoformat(),
        "points": len(r.points),
        "comments": len(r.comments)
    } for r in routes])

# ---------------------- START APP ----------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

