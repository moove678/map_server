import os
import uuid
import logging
import base64
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from werkzeug.security import generate_password_hash, check_password_hash
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity

# --- Flask app setup ---
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key')
app.config['JWT_SECRET_KEY'] = os.environ.get('JWT_SECRET_KEY', 'dev-jwt-key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///mydb.sqlite').replace("postgres://", "postgresql://")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JSON_AS_ASCII'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# --- Init extensions ---
db = SQLAlchemy(app)
jwt = JWTManager(app)
migrate = Migrate(app, db)
logging.basicConfig(level=logging.DEBUG)

# --- Models ---
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
    ignored = db.relationship(
        'User', secondary=ignored_users,
        primaryjoin=username == ignored_users.c.user,
        secondaryjoin=username == ignored_users.c.ignored,
        backref='ignored_by'
    )
    def to_json(self):
        return {"username": self.username, "lat": self.lat, "lon": self.lon}

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

# --- Helpers ---
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

@app.route('/uploads/<filename>')
def serve_upload(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# --- Auth ---
@app.route('/register', methods=['POST'])
def register():
    data = request.json
    if User.query.get(data['username']):
        return jsonify({"error": "exists"}), 400
    hashed = generate_password_hash(data['password'])
    user = User(username=data['username'], password=hashed)
    db.session.add(user)
    db.session.commit()
    return jsonify({"message": "registered"}), 200

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    user = User.query.get(data['username'])
    if not user or not check_password_hash(user.password, data['password']):
        return jsonify({"error": "invalid"}), 401
    token = create_access_token(identity=user.username)
    return jsonify({"access_token": token}), 200

# --- Group Routes ---
@app.route('/create_group', methods=['POST'])
@jwt_required()
def create_group():
    current = get_jwt_identity()
    name = request.json.get('name')
    if not name:
        return jsonify({"error": "No name"}), 400
    gid = str(uuid.uuid4())
    group = Group(id=gid, name=name, owner=current)
    db.session.add(group)
    group.members.append(User.query.get(current))
    db.session.commit()
    logging.info(f"[create_group] Group '{name}' ({gid}) created by {current}")
    return jsonify({"group_id": gid})

@app.route('/get_groups', methods=['GET'])
@jwt_required()
def get_groups():
    user = User.query.get(get_jwt_identity())
    groups = [g.to_json() for g in user.groups]
    logging.debug(f"[get_groups] {user.username} is in {len(groups)} groups")
    return jsonify(groups)

# --- Route Upload ---
@app.route('/upload_route', methods=['POST'])
@jwt_required()
def upload_route():
    current = get_jwt_identity()
    data = request.json
    rid = str(uuid.uuid4())
    route = Route(id=rid, name=data.get('route_name', 'Route'), username=current, distance=data.get('distance', 0.0))
    db.session.add(route)
    db.session.commit()

    for p in data.get('route_points', []):
        db.session.add(RoutePoint(route_id=rid, lat=p['lat'], lon=p['lon']))

    for c in data.get('route_comments', []):
        photo_name = save_base64(c.get('photo'), 'jpg') if c.get('photo') else None
        db.session.add(RouteComment(
            route_id=rid, lat=c['lat'], lon=c['lon'],
            text=c['text'], time=c['time'], photo=photo_name
        ))
    db.session.commit()
    logging.info(f"[upload_route] User '{current}' uploaded route '{route.name}' with {len(data.get('route_points', []))} points")
    return jsonify({"status": "uploaded"})

@app.route('/get_routes', methods=['GET'])
@jwt_required()
def get_routes():
    routes = Route.query.order_by(Route.created_at.desc()).all()
    result = []
    for r in routes:
        comments = RouteComment.query.filter_by(route_id=r.id).all()
        result.append({
            "name": r.name,
            "distance": r.distance,
            "date": r.created_at.strftime("%Y-%m-%d %H:%M"),
            "comments": [{
                "lat": c.lat, "lon": c.lon, "text": c.text, "time": c.time, "photo": c.photo
            } for c in comments]
        })
    logging.debug(f"[get_routes] {len(result)} routes fetched")
    return jsonify(result)

# --- SOS Ping ---
@app.route('/sos', methods=['POST'])
@jwt_required()
def sos():
    user = get_jwt_identity()
    data = request.json
    logging.warning(f"[SOS] {user}: lat={data.get('lat')}, lon={data.get('lon')}")
    return jsonify({"message": "SOS received"})

# --- Start ---
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
