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

# --- CONFIG ---
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key')
app.config['JWT_SECRET_KEY'] = os.environ.get('JWT_SECRET_KEY', 'dev-jwt-key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///mydb.sqlite').replace("postgres://", "postgresql://")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JSON_AS_ASCII'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)
jwt = JWTManager(app)
migrate = Migrate(app, db)
logging.basicConfig(level=logging.DEBUG)

# --- MODELS ---
ignored_users = db.Table('ignored_users',
    db.Column('user', db.String(80), db.ForeignKey('users.username', name='fk_ignore_user')),
    db.Column('ignored', db.String(80), db.ForeignKey('users.username', name='fk_ignore_ignored'))
)

group_members = db.Table('group_members',
    db.Column('group_id', db.String(36), db.ForeignKey('groups.id', name='fk_group_id')),
    db.Column('username', db.String(80), db.ForeignKey('users.username', name='fk_group_username'))
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
    group_id = db.Column(db.String(36), nullable=True)
    sender = db.Column(db.String(80), db.ForeignKey('users.username', name='fk_msg_sender'))
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
    username = db.Column(db.String(80), db.ForeignKey('users.username', name='fk_route_username'))
    distance = db.Column(db.Float)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class RoutePoint(db.Model):
    __tablename__ = 'route_points'
    id = db.Column(db.Integer, primary_key=True)
    route_id = db.Column(db.String(36), db.ForeignKey('routes.id', name='fk_point_route'))
    lat = db.Column(db.Float)
    lon = db.Column(db.Float)

class RouteComment(db.Model):
    __tablename__ = 'route_comments'
    id = db.Column(db.Integer, primary_key=True)
    route_id = db.Column(db.String(36), db.ForeignKey('routes.id', name='fk_comment_route'))
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

@app.route('/uploads/<filename>')
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
    return jsonify({"message": "registered"}), 200

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    user = User.query.get(data['username'])
    if not user or not check_password_hash(user.password, data['password']):
        return jsonify({"error": "invalid"}), 401
    token = create_access_token(identity=user.username)
    return jsonify({"access_token": token}), 200

# --- USERS ---
@app.route('/update_location', methods=['POST'])
@jwt_required()
def update_location():
    user = User.query.get(get_jwt_identity())
    data = request.json
    user.lat = data['lat']
    user.lon = data['lon']
    db.session.commit()
    return jsonify({"status": "ok"})

@app.route('/get_users', methods=['GET'])
@jwt_required()
def get_users():
    current = get_jwt_identity()
    user = User.query.get(current)
    all_users = User.query.all()
    return jsonify([u.to_json() for u in all_users if u.username not in [i.username for i in user.ignored]])

@app.route('/ignore_user', methods=['POST'])
@jwt_required()
def ignore_user():
    user = User.query.get(get_jwt_identity())
    target = request.json.get('username')
    target_user = User.query.get(target)
    if target_user and target_user not in user.ignored:
        user.ignored.append(target_user)
        db.session.commit()
    return jsonify({"ignored": target})

# --- GROUPS ---
@app.route('/create_group', methods=['POST'])
@jwt_required()
def create_group():
    try:
        current = get_jwt_identity()
        name = request.json.get('name')
        if not name:
            return jsonify({"error": "No name"}), 400
        gid = str(uuid.uuid4())
        g = Group(id=gid, name=name, owner=current)
        db.session.add(g)
        g.members.append(User.query.get(current))
        db.session.commit()
        return jsonify({"group_id": gid})
    except Exception as e:
        logging.exception("Ошибка создания группы")
        return jsonify({"error": "create_group_failed"}), 500

@app.route('/join_group', methods=['POST'])
@jwt_required()
def join_group():
    user = User.query.get(get_jwt_identity())
    gid = request.json.get('group_id')
    g = Group.query.get(gid)
    if g and user not in g.members:
        g.members.append(user)
        db.session.commit()
    return jsonify({"status": "joined"})

@app.route('/leave_group', methods=['POST'])
@jwt_required()
def leave_group():
    user = User.query.get(get_jwt_identity())
    gid = request.json.get('group_id')
    g = Group.query.get(gid)
    if g and user in g.members:
        g.members.remove(user)
        if not g.members:
            db.session.delete(g)
        db.session.commit()
    return jsonify({"status": "left"})

@app.route('/get_groups', methods=['GET'])
@jwt_required()
def get_groups():
    user = User.query.get(get_jwt_identity())
    return jsonify([g.to_json() for g in user.groups])

@app.route('/rename_group', methods=['POST'])
@jwt_required()
def rename_group():
    gid = request.json.get('group_id')
    new_name = request.json.get('new_name')
    group = Group.query.get(gid)
    if group:
        group.name = new_name
        db.session.commit()
    return jsonify({"status": "renamed"})

@app.route('/set_group_avatar', methods=['POST'])
@jwt_required()
def set_group_avatar():
    gid = request.json.get('group_id')
    img_data = request.json.get('avatar_base64')
    group = Group.query.get(gid)
    if group:
        avatar = save_base64(img_data, 'jpg')
        group.avatar = avatar
        db.session.commit()
    return jsonify({"status": "avatar_updated"})

# --- MESSAGES ---
@app.route('/send_message', methods=['POST'])
@jwt_required()
def send_message():
    user = get_jwt_identity()
    data = request.form or request.json or {}
    group_id = data.get('group_id')
    receiver = data.get('receiver')
    text = data.get('text', '')
    audio = save_file('audio') or save_base64(data.get('audio', ''), 'wav')
    photo = save_file('photo') or save_base64(data.get('photo', ''), 'jpg')
    msg = Message(group_id=group_id, sender=user, receiver=receiver, text=text, audio=audio, photo=photo)
    db.session.add(msg)
    db.session.commit()
    return jsonify({"message": "sent"})

@app.route('/get_messages', methods=['GET'])
@jwt_required()
def get_messages():
    current = get_jwt_identity()
    group_id = request.args.get('group_id')
    receiver = request.args.get('receiver')
    after_id = int(request.args.get('after_id', 0))
    q = Message.query.filter(Message.id > after_id)
    if group_id:
        q = q.filter_by(group_id=group_id)
    elif receiver:
        q = q.filter(
            ((Message.sender == current) & (Message.receiver == receiver)) |
            ((Message.sender == receiver) & (Message.receiver == current))
        ).filter(Message.group_id == None)
    else:
        return jsonify([])
    messages = q.order_by(Message.id).all()
    return jsonify([{
        "id": m.id,
        "from": m.sender,
        "to": m.receiver,
        "text": m.text,
        "photo": m.photo,
        "audio": m.audio,
        "is_read": m.is_read,
        "created_at": m.created_at.isoformat()
    } for m in messages])

@app.route('/delete_message', methods=['POST'])
@jwt_required()
def delete_message():
    mid = request.json.get('message_id')
    msg = Message.query.get(mid)
    if msg and msg.sender == get_jwt_identity():
        db.session.delete(msg)
        db.session.commit()
        return jsonify({"status": "deleted"})
    return jsonify({"error": "not found or not permitted"}), 404

# --- ROUTES ---
@app.route('/upload_route', methods=['POST'])
@jwt_required()
def upload_route():
    current = get_jwt_identity()
    data = request.json
    rid = str(uuid.uuid4())
    r = Route(id=rid, name=data.get('route_name', 'Route'), username=current, distance=data.get('distance', 0.0))
    db.session.add(r)
    db.session.commit()
    for p in data.get('route_points', []):
        db.session.add(RoutePoint(route_id=rid, lat=p['lat'], lon=p['lon']))
    for c in data.get('route_comments', []):
        photo = c.get('photo')
        photo_name = save_base64(photo, 'jpg') if photo else None
        db.session.add(RouteComment(
            route_id=rid, lat=c['lat'], lon=c['lon'],
            text=c['text'], time=c['time'], photo=photo_name
        ))
    db.session.commit()
    return jsonify({"status": "uploaded"})

@app.route('/get_routes', methods=['GET'])
@jwt_required()
def get_routes():
    routes = Route.query.order_by(Route.created_at.desc()).all()
    resp = []
    for r in routes:
        comments = RouteComment.query.filter_by(route_id=r.id).all()
        resp.append({
            "name": r.name,
            "distance": r.distance,
            "date": r.created_at.strftime("%Y-%m-%d %H:%M"),
            "comments": [{
                "lat": c.lat, "lon": c.lon,
                "text": c.text, "time": c.time,
                "photo": c.photo
            } for c in comments]
        })
    return jsonify(resp)

@app.route('/sos', methods=['POST'])
@jwt_required()
def sos():
    user = get_jwt_identity()
    data = request.json
    logging.warning(f"SOS from {user}: lat={data.get('lat')}, lon={data.get('lon')}")
    return jsonify({"message": "SOS received"})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
