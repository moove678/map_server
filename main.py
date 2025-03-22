import os
import json
import time
import logging
import threading
import math
from flask import Flask, request, jsonify
from werkzeug.security import generate_password_hash, check_password_hash

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

app = Flask(__name__)

# ====== КОНСТАНТЫ ======
USERS_FILE = "users.json"
ROUTES_DIR = "routes"
OFFLINE_ROUTES_FILE = "offline_routes.json"

# Глобальные структуры данных и блокировки
ONLINE_USERS = set()
active_routes = {}
users_lock = threading.Lock()

# Создаём папку для маршрутов, если её нет
if not os.path.exists(ROUTES_DIR):
    os.makedirs(ROUTES_DIR)

# ====== ФУНКЦИИ РАБОТЫ С JSON ======
def load_json(file_path, default_data):
    if os.path.exists(file_path):
        try:
            with open(file_path, "r") as f:
                return json.load(f)
        except json.JSONDecodeError:
            logging.error(f"JSON decode error in {file_path}, используем данные по умолчанию.")
            return default_data
    return default_data

def save_json(file_path, data):
    try:
        with open(file_path, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        logging.error(f"Ошибка при сохранении JSON в {file_path}: {e}")

# Загружаем пользователей и маршруты
with users_lock:
    users = load_json(USERS_FILE, [])
offline_routes = load_json(OFFLINE_ROUTES_FILE, [])

# ====== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ======
def haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (math.sin(delta_phi / 2) ** 2 +
         math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def filter_route(points, threshold=5):
    if not points:
        return points
    filtered = [points[0]]
    for point in points[1:]:
        last_point = filtered[-1]
        if haversine_distance(last_point[0], last_point[1], point[0], point[1]) >= threshold:
            filtered.append(point)
    return filtered

# ====== ЭНДПОИНТЫ ======

@app.route("/")
def index():
    return "Сервер Flask работает!"

@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok"}), 200

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
        users.append({"username": username, "password": hashed_password, "lat": None, "lon": None})
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

@app.route("/update_location", methods=["POST"])
def update_location():
    data = request.get_json()
    if "username" not in data or "lat" not in data or "lon" not in data:
        return jsonify({"error": "Missing data"}), 400
    username = data["username"]
    with users_lock:
        for user in users:
            if user["username"] == username:
                user["lat"] = data["lat"]
                user["lon"] = data["lon"]
                save_json(USERS_FILE, users)
                return jsonify({"message": "Location updated"}), 200
    return jsonify({"error": "User not found"}), 404

@app.route("/get_users", methods=["GET"])
def get_users():
    with users_lock:
        active_users = [
            {"username": u["username"], "lat": u["lat"], "lon": u["lon"]}
            for u in users
            if u["username"] in ONLINE_USERS and u["lat"] is not None and u["lon"] is not None
        ]
    return jsonify(active_users), 200

@app.route("/start_route", methods=["POST"])
def start_route():
    data = request.get_json()
    if "username" not in data:
        return jsonify({"error": "Missing data"}), 400
    username = data["username"]
    route_name = f"{username}_route_{int(time.time())}"
    route_file = os.path.join(ROUTES_DIR, f"{route_name}.json")
    try:
        with open(route_file, "w") as f:
            json.dump({"coordinates": [], "markers": []}, f)
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
        with open(route_file, "r") as f:
            route_data = json.load(f)
        route_data["coordinates"].append({
            "lat": data["lat"], "lon": data["lon"], "timestamp": time.time()
        })
        with open(route_file, "w") as f:
            json.dump(route_data, f, indent=4)
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
        with open(route_file, "r") as f:
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
        with open(route_file, "r") as f:
            route_data = json.load(f)
        note = {
            "lat": data["lat"], "lon": data["lon"],
            "text": data["text"], "photo": data.get("photo")
        }
        route_data["markers"].append(note)
        with open(route_file, "w") as f:
            json.dump(route_data, f, indent=4)
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
        with open(route_file, "w") as f:
            json.dump({
                "coordinates": [{"lat": lat, "lon": lon} for lat, lon in filtered_points],
                "markers": data.get("notes", [])
            }, f, indent=4)
    except Exception as e:
        logging.error(f"Error saving route {data['route_name']}: {e}")
        return jsonify({"error": "Failed to save route"}), 500
    return jsonify({"message": "Route saved"}), 200

# ====== ЗАПУСК ======
if __name__ == "__main__":
    port = 5000
    logging.info(f"Запуск сервера на порту {port}...")
    app.run(host="0.0.0.0", port=port)
