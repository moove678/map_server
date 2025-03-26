import os
import json
import time
import logging
import threading
import math
import uuid
from flask import Flask, request, jsonify
from werkzeug.security import generate_password_hash, check_password_hash

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

app = Flask(__name__)

# ====== КОНСТАНТЫ ======
USERS_FILE = "users.json"
ROUTES_DIR = "routes"
OFFLINE_ROUTES_FILE = "offline_routes.json"
SOS_FILE = "sos.json"          # Храним список SOS
SOS_PHOTOS_DIR = "sos_photos"  # Папка для фото SOS

# >>> НОВЫЙ ФАЙЛ ДЛЯ ГРУПП <<<
GROUPS_FILE = "groups.json"

# Глобальные структуры данных
ONLINE_USERS = set()
active_routes = {}

users_lock = threading.Lock()
groups_lock = threading.Lock()

# Создаём нужные папки, если нет
if not os.path.exists(ROUTES_DIR):
    os.makedirs(ROUTES_DIR)
if not os.path.exists(SOS_PHOTOS_DIR):
    os.makedirs(SOS_PHOTOS_DIR)

# ====== ФУНКЦИИ РАБОТЫ С JSON ======
def load_json(file_path, default_data):
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            logging.error(f"JSON decode error in {file_path}, используем по умолчанию.")
            return default_data
    return default_data

def save_json(file_path, data):
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        logging.error(f"Ошибка при сохранении JSON в {file_path}: {e}")

# Загружаем пользователей
with users_lock:
    users = load_json(USERS_FILE, [])

# >>> Загружаем группы <<<
with groups_lock:
    groups_data = load_json(GROUPS_FILE, {"groups": []})
    # структура: {"groups": [ {id, name, owner, private, members, messages}, ... ]}

# Оффлайн-маршруты и SOS
offline_routes = load_json(OFFLINE_ROUTES_FILE, [])
sos_entries = load_json(SOS_FILE, [])

# ====== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ======
def haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2)**2 +
         math.cos(phi1)*math.cos(phi2)*math.sin(dlambda / 2)**2)
    c = 2*math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R*c

def filter_route(points, threshold=5):
    if not points:
        return points
    filtered = [points[0]]
    for point in points[1:]:
        last_point = filtered[-1]
        dist = haversine_distance(last_point[0], last_point[1], point[0], point[1])
        if dist >= threshold:
            filtered.append(point)
    return filtered


# ====== ЭНДПОИНТЫ ======
@app.route("/")
def index():
    return "Сервер Flask работает!"

@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok"}), 200

# ====== Регистрация / Логин ======
@app.route("/register", methods=["POST"])
def register():
    data = request.get_json()
    if not data or "username" not in data or "password" not in data:
        return jsonify({"error": "Insufficient data"}), 400

    username = data["username"]
    password = data["password"]
    with users_lock:
        for user in users:
            if user["username"] == username:
                return jsonify({"error": "User already exists"}), 409
        hashed_password = generate_password_hash(password)
        users.append({
            "username": username,
            "password": hashed_password,
            "lat": None,
            "lon": None,
            "altitude": None
        })
        save_json(USERS_FILE, users)

    return jsonify({"message": "Registration successful!"}), 200

@app.route("/login", methods=["POST"])
def login():
    data = request.get_json()
    if not data or "username" not in data or "password" not in data:
        return jsonify({"error": "Insufficient data"}), 400
    username = data["username"]
    password = data["password"]

    with users_lock:
        for user in users:
            if user["username"] == username and check_password_hash(user["password"], password):
                ONLINE_USERS.add(username)
                return jsonify({"message": "Login successful!"}), 200

    return jsonify({"error": "Invalid username or password"}), 401

# ====== Обновление координат ======
@app.route("/update_location", methods=["POST"])
def update_location():
    data = request.get_json()
    if "username" not in data or "lat" not in data or "lon" not in data:
        return jsonify({"error": "Missing data"}), 400
    username = data["username"]
    altitude = data.get("altitude", None)  # может отсутствовать

    with users_lock:
        for user in users:
            if user["username"] == username:
                user["lat"] = data["lat"]
                user["lon"] = data["lon"]
                if altitude is not None:
                    user["altitude"] = altitude
                save_json(USERS_FILE, users)
                return jsonify({"message": "Location updated"}), 200

    return jsonify({"error": "User not found"}), 404

@app.route("/get_users", methods=["GET"])
def get_users():
    with users_lock:
        active_users = []
        for u in users:
            if u["username"] in ONLINE_USERS and u["lat"] is not None and u["lon"] is not None:
                active_users.append({
                    "username": u["username"],
                    "lat": u["lat"],
                    "lon": u["lon"]
                    # altitude тоже можно отдать, если хочешь
                })
    return jsonify(active_users), 200

# ====== Работа с маршрутами ======
@app.route("/start_route", methods=["POST"])
def start_route():
    data = request.get_json()
    if "username" not in data:
        return jsonify({"error": "Missing data"}), 400

    username = data["username"]
    route_name = f"{username}_route_{int(time.time())}"
    route_file = os.path.join(ROUTES_DIR, f"{route_name}.json")

    try:
        with open(route_file, "w", encoding="utf-8") as f:
            json.dump({"coordinates": [], "markers": []}, f, ensure_ascii=False)
    except Exception as e:
        logging.error(f"Error creating route file {route_file}: {e}")
        return jsonify({"error": "Failed to start route recording"}), 500

    active_routes[username] = route_name
    return jsonify({"message": "Route recording started", "route_name": route_name}), 200

@app.route("/record_route", methods=["POST"])
def record_route():
    data = request.get_json()
    if "username" not in data or "route_name" not in data or "lat" not in data or "lon" not in data:
        return jsonify({"error": "Missing data"}), 400

    route_file = os.path.join(ROUTES_DIR, f"{data['route_name']}.json")
    if not os.path.exists(route_file):
        return jsonify({"error": "Route not found"}), 404

    try:
        with open(route_file, "r", encoding="utf-8") as f:
            route_data = json.load(f)
        route_data["coordinates"].append({
            "lat": data["lat"],
            "lon": data["lon"],
            "timestamp": time.time()
        })
        with open(route_file, "w", encoding="utf-8") as f:
            json.dump(route_data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        logging.error(f"Error recording route point: {e}")
        return jsonify({"error": "Failed to add point"}), 500

    return jsonify({"message": "Point added"}), 200

@app.route("/load_route", methods=["POST"])
def load_route():
    data = request.get_json()
    if "route_name" not in data:
        return jsonify({"error": "Missing data"}), 400

    route_file = os.path.join(ROUTES_DIR, f"{data['route_name']}.json")
    if not os.path.exists(route_file):
        return jsonify({"error": "Route not found"}), 404

    try:
        with open(route_file, "r", encoding="utf-8") as f:
            route_data = json.load(f)
    except Exception as e:
        logging.error(f"Error loading route {route_file}: {e}")
        return jsonify({"error": "Failed to load route"}), 500

    return jsonify(route_data), 200

@app.route("/delete_route", methods=["POST"])
def delete_route():
    data = request.get_json()
    if "route_name" not in data:
        return jsonify({"error": "Missing data"}), 400

    route_file = os.path.join(ROUTES_DIR, f"{data['route_name']}.json")
    if os.path.exists(route_file):
        try:
            os.remove(route_file)
            return jsonify({"message": f"Route {data['route_name']} deleted."}), 200
        except Exception as e:
            logging.error(f"Error deleting route {route_file}: {e}")
            return jsonify({"error": "Failed to delete route"}), 500
    return jsonify({"error": "Route not found"}), 404

@app.route("/add_note", methods=["POST"])
def add_note():
    data = request.get_json()
    if "route_name" not in data or "lat" not in data or "lon" not in data or "text" not in data:
        return jsonify({"error": "Missing data"}), 400

    route_file = os.path.join(ROUTES_DIR, f"{data['route_name']}.json")
    if not os.path.exists(route_file):
        return jsonify({"error": "Route not found"}), 404

    try:
        with open(route_file, "r", encoding="utf-8") as f:
            route_data = json.load(f)
        note = {
            "lat": data["lat"],
            "lon": data["lon"],
            "text": data["text"],
            "photo": data.get("photo")
        }
        route_data["markers"].append(note)
        with open(route_file, "w", encoding="utf-8") as f:
            json.dump(route_data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        logging.error(f"Error adding note: {e}")
        return jsonify({"error": "Failed to add note"}), 500

    return jsonify({"message": "Note added"}), 200

@app.route("/get_routes", methods=["GET"])
def get_routes():
    routes = []
    try:
        for filename in os.listdir(ROUTES_DIR):
            if filename.endswith(".json"):
                routes.append(filename[:-5])
    except Exception as e:
        logging.error(f"Error listing routes: {e}")
        return jsonify({"error": "Failed to list routes"}), 500
    return jsonify(routes), 200

@app.route("/save_route", methods=["POST"])
def save_route():
    data = request.get_json()
    if "username" not in data or "route_name" not in data or "coordinates" not in data:
        return jsonify({"error": "Missing data"}), 400

    route_file = os.path.join(ROUTES_DIR, f"{data['route_name']}.json")
    try:
        points = [(pt["lat"], pt["lon"]) for pt in data["coordinates"]]
        filtered_points = filter_route(points, threshold=5)
        with open(route_file, "w", encoding="utf-8") as f:
            json.dump({
                "coordinates": [{"lat": lat, "lon": lon} for lat, lon in filtered_points],
                "markers": data.get("notes", [])
            }, f, indent=4, ensure_ascii=False)
    except Exception as e:
        logging.error(f"Error saving route {data['route_name']}: {e}")
        return jsonify({"error": "Failed to save route"}), 500

    return jsonify({"message": "Route saved"}), 200

# ====== НОВЫЙ ФУНКЦИОНАЛ: ГРУППЫ И СООБЩЕНИЯ ======

"""
groups_data в файле groups.json выглядит так:
{
  "groups": [
    {
      "id": "d2ff7c53a3b84e2ba72f",
      "name": "MyGroup",
      "owner": "User1",
      "private": false,
      "members": ["User1","User2"],
      "messages": [
        {
          "id": 1,
          "username": "User1",
          "text": "Привет",
          "timestamp": 1680000000.0
        }
      ]
    }
  ]
}
"""

@app.route("/create_group", methods=["POST"])
def create_group():
    """
    Принимает JSON:
    {
      "name": "имя группы",
      "owner": "username"  (клиент сам шлёт, чтобы знать кто создаёт)
    }
    Создаёт группу, возвращает { "id": ..., "message": "Group created" }
    """
    data = request.get_json()
    if not data or "name" not in data or "owner" not in data:
        return jsonify({"error": "Missing 'name' or 'owner'"}), 400

    group_name = data["name"]
    owner = data["owner"]

    with groups_lock:
        # создаём новый объект группы
        group_id = uuid.uuid4().hex[:10]
        new_group = {
            "id": group_id,
            "name": group_name,
            "owner": owner,
            "private": False,
            "members": [owner],
            "messages": []
        }
        groups_data["groups"].append(new_group)
        save_json(GROUPS_FILE, groups_data)

    return jsonify({"id": group_id, "message": "Group created"}), 200

@app.route("/get_groups", methods=["GET"])
def get_groups():
    """
    Отдаёт список групп. Пример ответа:
    [
      { "id": "...", "name": "...", "owner": "...", "members_count": 2 },
      ...
    ]
    """
    with groups_lock:
        result = []
        for g in groups_data["groups"]:
            result.append({
                "id": g["id"],
                "name": g["name"],
                "owner": g["owner"],
                "members_count": len(g["members"])
            })
    return jsonify(result), 200

@app.route("/join_group", methods=["POST"])
def join_group():
    """
    Принимает JSON:
    {
      "username": "...",
      "group_id": "..."
    }
    """
    data = request.get_json()
    if not data or "username" not in data or "group_id" not in data:
        return jsonify({"error": "Missing username or group_id"}), 400

    username = data["username"]
    group_id = data["group_id"]

    with groups_lock:
        for g in groups_data["groups"]:
            if g["id"] == group_id:
                if username not in g["members"]:
                    g["members"].append(username)
                else:
                    logging.info(f"[join_group] Пользователь {username} уже в группе {g['name']}")
                save_json(GROUPS_FILE, groups_data)
                return jsonify({"message": f"User {username} joined group {g['name']}"}), 200

    return jsonify({"error": "Group not found"}), 404

@app.route("/leave_group", methods=["POST"])
def leave_group():
    data = request.get_json()
    if not data or "username" not in data or "group_id" not in data:
        return jsonify({"error": "Missing username or group_id"}), 400

    username = data["username"]
    group_id = data["group_id"]

    with groups_lock:
        for g in groups_data["groups"]:
            if g["id"] == group_id:
                if username in g["members"]:
                    g["members"].remove(username)
                    save_json(GROUPS_FILE, groups_data)
                    return jsonify({"message": f"User {username} left group {g['name']}"}), 200
                else:
                    return jsonify({"error": "User not in group"}), 400

    return jsonify({"error": "Group not found"}), 404

@app.route("/send_message", methods=["POST"])
def send_message():
    """
    Принимает multipart/form-data или x-www-form-urlencoded:
      - username
      - group_id
      - text
      - photo (опционально) -- не обязательно использовать
    Генерим message_id как счётчик (len(messages)+1) или uuid
    """
    username = request.form.get("username") or request.json.get("username") if request.json else None
    group_id = request.form.get("group_id") or request.json.get("group_id") if request.json else None
    text = request.form.get("text") or request.json.get("text") if request.json else None

    if not username or not group_id or not text:
        return jsonify({"error": "Missing username, group_id or text"}), 400

    photo_file = request.files.get("photo", None)
    photo_path = None
    if photo_file:
        # сохраняем фото
        photo_name = f"chat_{uuid.uuid4().hex}.jpg"
        photo_dir = "chat_photos"
        if not os.path.exists(photo_dir):
            os.makedirs(photo_dir)
        photo_path = os.path.join(photo_dir, photo_name)
        photo_file.save(photo_path)
        logging.info(f"[send_message] Фото сохранено: {photo_path}")

    with groups_lock:
        for g in groups_data["groups"]:
            if g["id"] == group_id:
                # создаём ID для сообщения
                msg_id = len(g["messages"]) + 1  # простой способ
                new_msg = {
                    "id": msg_id,
                    "username": username,
                    "text": text,
                    "timestamp": time.time()
                }
                if photo_path:
                    new_msg["photo"] = photo_path

                g["messages"].append(new_msg)
                save_json(GROUPS_FILE, groups_data)

                return jsonify({"message": "Message sent"}), 200

    return jsonify({"error": "Group not found"}), 404

@app.route("/get_messages", methods=["GET"])
def get_messages():
    """
    Параметры: ?group_id=XXX&after_id=NNN
    Возвращает [{"id":..., "username":..., "text":..., "timestamp":..., "photo":...}, ...]
    """
    group_id = request.args.get("group_id", None)
    after_id = request.args.get("after_id", 0, type=int)

    if not group_id:
        return jsonify({"error": "Missing group_id"}), 400

    with groups_lock:
        for g in groups_data["groups"]:
            if g["id"] == group_id:
                filtered = []
                for msg in g["messages"]:
                    if msg["id"] > after_id:
                        filtered.append(msg)
                return jsonify(filtered), 200

    return jsonify({"error": "Group not found"}), 404


# ====== SOS ======
"""
Храним в sos_entries (список словарей):
{
  "id": "uuid",
  "username": "...",
  "lat": 55.75,
  "lon": 37.61,
  "desc": "...",
  "transport": "...",
  "contact": "...",
  "photo": "sos_photos/filename.jpg",
  "timestamp": 1670000000.0
}
"""

@app.route("/sos", methods=["POST"])
def sos():
    username = request.form.get("username")
    lat = request.form.get("lat")
    lon = request.form.get("lon")
    info_str = request.form.get("info")

    if not username or not lat or not lon or not info_str:
        return jsonify({"error": "Missing fields (username, lat, lon, info)"}), 400

    try:
        lat = float(lat)
        lon = float(lon)
    except:
        return jsonify({"error": "lat/lon must be float"}), 400

    try:
        info = json.loads(info_str)
    except:
        info = {"desc": "", "transport": "", "contact": ""}

    photo_file = request.files.get("photo")
    photo_path = None
    if photo_file:
        photo_name = f"sos_{uuid.uuid4().hex}.jpg"
        photo_path = os.path.join(SOS_PHOTOS_DIR, photo_name)
        photo_file.save(photo_path)
        logging.info(f"Фото SOS сохранено: {photo_path}")

    entry_id = uuid.uuid4().hex
    new_entry = {
        "id": entry_id,
        "username": username,
        "lat": lat,
        "lon": lon,
        "desc": info.get("desc",""),
        "transport": info.get("transport",""),
        "contact": info.get("contact",""),
        "photo": photo_path if photo_path else None,
        "timestamp": time.time()
    }

    sos_entries.append(new_entry)
    save_json(SOS_FILE, sos_entries)

    return jsonify({"message": "SOS received", "id": entry_id}), 200

@app.route("/get_sos", methods=["GET"])
def get_sos():
    lat = request.args.get("lat", None, type=float)
    lon = request.args.get("lon", None, type=float)
    radius = request.args.get("radius", None, type=float)

    # Удаляем старые SOS (старше 12 ч = 43200 сек)
    cutoff = time.time() - 43200
    global sos_entries
    changed = False
    sos_entries = [entry for entry in sos_entries if entry["timestamp"] > cutoff]
    save_json(SOS_FILE, sos_entries)

    result = []
    for entry in sos_entries:
        if (lat is not None) and (lon is not None) and (radius is not None):
            dist = haversine_distance(lat, lon, entry["lat"], entry["lon"])
            if dist <= radius:
                result.append(entry)
        else:
            result.append(entry)

    return jsonify(result), 200


# ====== ЗАПУСК ======
if __name__ == "__main__":
    port = 5000
    logging.info(f"Запуск сервера на порту {port}...")
    app.run(host="0.0.0.0", port=port)
