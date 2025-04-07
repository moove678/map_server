import os
import json
import time
import uuid
from flask import Flask, request, jsonify, send_from_directory
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity

app = Flask(__name__)
app.config["JWT_SECRET_KEY"] = "super-secret-key"
jwt = JWTManager(app)

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

users_file = "users.json"
groups_file = "groups.json"
msgs_file = "messages.json"
priv_msgs_file = "private_messages.json"
sos_file = "sos.json"

# ---------- UTILS ----------
def load_file(path): return json.load(open(path, "r", encoding="utf-8")) if os.path.exists(path) else {}
def save_file(path, data): json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

users = load_file(users_file)
groups = load_file(groups_file)
messages = load_file(msgs_file)
private_messages = load_file(priv_msgs_file)
sos_requests = load_file(sos_file)

# ---------- AUTH ----------
@app.route("/register", methods=["POST"])
def register():
    data = request.json
    if data["username"] in users:
        return {"msg": "User already exists"}, 400
    users[data["username"]] = {
        "password": data["password"],
        "lat": 0, "lon": 0,
        "profile": {
            "name": "",
            "age": "",
            "gender": "",
            "transport": ""
        }
    }
    save_file(users_file, users)
    return {"msg": "Registered"}

@app.route("/login", methods=["POST"])
def login():
    data = request.json
    user = users.get(data["username"])
    if not user or user["password"] != data["password"]:
        return {"msg": "Invalid credentials"}, 401
    token = create_access_token(identity=data["username"])
    return {"token": token}

# ---------- LOCATION ----------
@app.route("/update_location", methods=["POST"])
@jwt_required()
def update_location():
    user = get_jwt_identity()
    data = request.json
    users[user]["lat"] = data["lat"]
    users[user]["lon"] = data["lon"]
    save_file(users_file, users)
    return {"msg": "Location updated"}

@app.route("/get_users")
@jwt_required()
def get_users():
    current = get_jwt_identity()
    my_lat = users[current]["lat"]
    my_lon = users[current]["lon"]
    radius_km = 100

    def dist(a, b):
        from math import radians, sin, cos, sqrt, atan2
        R = 6371
        dlat = radians(b["lat"] - a["lat"])
        dlon = radians(b["lon"] - a["lon"])
        x = sin(dlat/2)**2 + cos(radians(a["lat"])) * cos(radians(b["lat"])) * sin(dlon/2)**2
        return R * 2 * atan2(sqrt(x), sqrt(1 - x))

    result = []
    for username, u in users.items():
        if username == current: continue
        if dist(users[current], u) <= radius_km:
            result.append({
                "username": username,
                "lat": u["lat"],
                "lon": u["lon"],
                **u.get("profile", {})
            })
    return jsonify(result)

# ---------- PROFILE ----------
@app.route("/update_profile", methods=["POST"])
@jwt_required()
def update_profile():
    user = get_jwt_identity()
    users[user]["profile"].update(request.json)
    save_file(users_file, users)
    return {"msg": "Profile updated"}

# ---------- SOS ----------
@app.route("/sos", methods=["POST"])
@jwt_required()
def send_sos():
    user = get_jwt_identity()
    data = request.json
    data["username"] = user
    data["timestamp"] = time.time()
    sos_requests[str(uuid.uuid4())] = data
    save_file(sos_file, sos_requests)
    return {"msg": "SOS received"}

# ---------- GROUP CHAT ----------
@app.route("/create_group", methods=["POST"])
@jwt_required()
def create_group():
    group_id = str(uuid.uuid4())
    data = request.json
    groups[group_id] = {
        "name": data["name"],
        "members": [get_jwt_identity()]
    }
    save_file(groups_file, groups)
    return {"group_id": group_id}

@app.route("/join_group", methods=["POST"])
@jwt_required()
def join_group():
    data = request.json
    group = groups.get(data["group_id"])
    if group and get_jwt_identity() not in group["members"]:
        group["members"].append(get_jwt_identity())
        save_file(groups_file, groups)
        return {"msg": "joined"}
    return {"msg": "invalid"}, 400

@app.route("/leave_group", methods=["POST"])
@jwt_required()
def leave_group():
    user = get_jwt_identity()
    data = request.json
    group = groups.get(data["group_id"])
    if group:
        group["members"] = [m for m in group["members"] if m != user]
        save_file(groups_file, groups)
    return {"msg": "left"}

@app.route("/get_groups")
@jwt_required()
def get_groups():
    return jsonify([{**v, "id": k} for k, v in groups.items()])

# ---------- MESSAGES ----------
@app.route("/send_message", methods=["POST"])
@jwt_required()
def send_message():
    user = get_jwt_identity()
    group_id = request.form.get("group_id")
    private_to = request.form.get("username")
    text = request.form.get("text", "")
    file_audio = request.files.get("audio")
    file_photo = request.files.get("photo")

    file_links = {}

    if file_audio:
        filename = f"{uuid.uuid4()}.wav"
        path = os.path.join(UPLOAD_FOLDER, filename)
        file_audio.save(path)
        file_links["audio"] = f"/uploads/{filename}"

    if file_photo:
        filename = f"{uuid.uuid4()}.jpg"
        path = os.path.join(UPLOAD_FOLDER, filename)
        file_photo.save(path)
        file_links["photo"] = f"/uploads/{filename}"

    message = {
        "id": int(time.time() * 1000),
        "username": user,
        "text": text,
        **file_links
    }

    if group_id:
        messages.setdefault(group_id, []).append(message)
        save_file(msgs_file, messages)
    elif private_to:
        private_messages.setdefault(private_to, {}).setdefault(user, []).append(message)
        private_messages.setdefault(user, {}).setdefault(private_to, []).append(message)
        save_file(priv_msgs_file, private_messages)
    return {"msg": "sent"}

@app.route("/get_messages")
@jwt_required()
def get_messages():
    after = int(request.args.get("after_id", "0"))
    group_id = request.args.get("group_id")
    username = request.args.get("username")
    user = get_jwt_identity()

    if group_id:
        chat = messages.get(group_id, [])
    elif username:
        chat = private_messages.get(user, {}).get(username, [])
    else:
        return jsonify([])

    return jsonify([m for m in chat if m["id"] > after])

# ---------- MEDIA ----------
@app.route("/uploads/<filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

# ---------- START ----------
if __name__ == "__main__":
    app.run(debug=True, port=8000)
