import os
import uuid
import math
import logging
import json
from datetime import datetime

from flask import Flask, request, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(message)s")

app = Flask(__name__)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
db_path = os.path.join(BASE_DIR, "database.db")
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

db = SQLAlchemy(app)
migrate = Migrate(app, db)


# --------------------- Модели ---------------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)
    # координаты
    lat = db.Column(db.Float, nullable=True)
    lon = db.Column(db.Float, nullable=True)
    updated_at = db.Column(db.DateTime, nullable=True)

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
    route_points = db.Column(db.Text, nullable=False)  # JSON
    route_comments = db.Column(db.Text, nullable=True) # JSON
    is_public = db.Column(db.Boolean, default=True)
    distance = db.Column(db.Float, default=0)
    duration = db.Column(db.Float, default=0)
    avg_speed = db.Column(db.Float, default=0)
    date = db.Column(db.Date, default=datetime.utcnow().date)


# --------------------- Эндпоинты ---------------------
@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok"}), 200

@app.route("/login", methods=["POST"])
def login():
    data = request.get_json()
    username = data.get("username")
    password = data.get("password")
    user = User.query.filter_by(username=username).first()
    if user and user.password == password:
        return jsonify({"message": "Login successful"}), 200
    return jsonify({"error": "Invalid credentials"}), 401

@app.route("/register", methods=["POST"])
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

@app.route("/update_location", methods=["POST"])
def update_location():
    data = request.get_json()
    username = data.get("username")
    lat = data.get("lat")
    lon = data.get("lon")
    logging.info(f"Location update for {username}: lat={lat}, lon={lon}")

    user = User.query.filter_by(username=username).first()
    if user:
        user.lat = lat
        user.lon = lon
        user.updated_at = datetime.utcnow()
        db.session.commit()

    return jsonify({"message": "Location updated"}), 200

@app.route("/create_group", methods=["POST"])
def create_group():
    data = request.get_json()
    group_id = str(uuid.uuid4())
    grp = Group(
        id=group_id,
        name=data.get("name"),
        owner=data.get("owner")
    )
    db.session.add(grp)
    db.session.commit()
    return jsonify({"group_id": group_id, "name": grp.name}), 200

@app.route("/get_groups", methods=["GET"])
def get_groups():
    grps = Group.query.all()
    result = []
    for g in grps:
        result.append({
            "id": g.id,
            "name": g.name,
            "owner": g.owner
        })
    return jsonify(result), 200

@app.route("/join_group", methods=["POST"])
def join_group():
    data = request.get_json()
    group_id = data.get("group_id")
    grp = Group.query.filter_by(id=group_id).first()
    if not grp:
        return jsonify({"error": "Group not found"}), 404
    # упрощённо
    return jsonify({"message": "Joined group"}), 200

@app.route("/delete_group", methods=["POST"])
def delete_group():
    data = request.get_json()
    group_id = data.get("group_id")
    grp = Group.query.filter_by(id=group_id).first()
    if not grp:
        return jsonify({"error": "Group not found"}), 404
    db.session.delete(grp)
    db.session.commit()
    return jsonify({"message": "Group deleted"}), 200

@app.route("/send_message", methods=["POST"])
def send_message():
    username = request.form.get("username")
    group_id = request.form.get("group_id")
    text = request.form.get("text", "")
    audio = request.files.get("audio")
    photo = request.files.get("photo")

    msg = GroupMessage(
        group_id=group_id,
        username=username,
        text=text,
        timestamp=datetime.utcnow()
    )
    if audio:
        audio_filename = f"audio_{uuid.uuid4().hex}.dat"
        audio_path = os.path.join(app.config['UPLOAD_FOLDER'], audio_filename)
        audio.save(audio_path)
        msg.audio_filename = audio_filename

    if photo:
        photo_filename = f"photo_{uuid.uuid4().hex}.jpg"
        photo_path = os.path.join(app.config['UPLOAD_FOLDER'], photo_filename)
        photo.save(photo_path)
        msg.photo_filename = photo_filename

    db.session.add(msg)
    db.session.commit()
    return jsonify({"message": "Message sent", "id": msg.id}), 200

@app.route("/get_messages", methods=["GET"])
def get_messages():
    group_id = request.args.get("group_id")
    after_id = int(request.args.get("after_id", 0))
    msgs = GroupMessage.query.filter(
        GroupMessage.group_id==group_id,
        GroupMessage.id>after_id
    ).all()
    result = []
    for m in msgs:
        item = {
            "id": m.id,
            "username": m.username,
            "text": m.text,
            "timestamp": m.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        }
        if m.audio_filename:
            item["audio"] = m.audio_filename
        if m.photo_filename:
            item["photo"] = m.photo_filename
        result.append(item)
    return jsonify(result), 200


# ------------ Новый эндпоинт для «других пользователей в радиусе» -----------
@app.route("/get_users_in_radius", methods=["GET"])
def get_users_in_radius():
    lat_str = request.args.get("lat", "0")
    lon_str = request.args.get("lon", "0")
    rad_str = request.args.get("radius_km", "10")
    try:
        lat0 = float(lat_str)
        lon0 = float(lon_str)
        radius_km = float(rad_str)
    except:
        return jsonify([]), 200

    users = User.query.all()
    out = []
    for u in users:
        if u.lat is not None and u.lon is not None:
            dist = distance_km(lat0, lon0, u.lat, u.lon)
            if dist <= radius_km:
                out.append({
                    "username": u.username,
                    "lat": u.lat,
                    "lon": u.lon,
                    "dist_km": round(dist,2)
                })
    return jsonify(out), 200

def distance_km(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat/2)**2 +
         math.cos(math.radians(lat1)) *
         math.cos(math.radians(lat2)) *
         math.sin(dlon/2)**2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R*c


@app.route("/upload_route", methods=["POST"])
def upload_route():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No route data provided"}), 400
    route = Route(
        username=data.get("username","unknown"),
        route_name=data.get("route_name","Unnamed Route"),
        route_points=json.dumps(data.get("route_points",[]), ensure_ascii=False),
        route_comments=json.dumps(data.get("route_comments",[]), ensure_ascii=False),
        is_public=data.get("is_public",True),
        distance=data.get("distance",0),
        duration=data.get("duration",0),
        avg_speed=data.get("avg_speed",0),
        date=datetime.utcnow().date()
    )
    db.session.add(route)
    db.session.commit()
    return jsonify({"message": "Route uploaded"}), 200

@app.route("/get_routes", methods=["GET"])
def get_routes():
    all_r = Route.query.all()
    out = []
    for r in all_r:
        out.append({
            "id": r.id,
            "username": r.username,
            "name": r.route_name,
            "route_points": json.loads(r.route_points),
            "route_comments": json.loads(r.route_comments or "[]"),
            "is_public": r.is_public,
            "distance": r.distance,
            "duration": r.duration,
            "avg_speed": r.avg_speed,
            "date": r.date.strftime("%Y-%m-%d")
        })
    return jsonify(out), 200

@app.route("/uploads/<filename>")
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


if __name__=="__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
