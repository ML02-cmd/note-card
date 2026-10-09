from flask import Flask, request, jsonify, send_from_directory, session
from flask_cors import CORS
from functools import wraps
import sqlite3
import uuid
import os
import json
from datetime import datetime

app = Flask(__name__)
app.secret_key = "please-change-this-to-a-long-random-string-2026"
CORS(app, supports_credentials=True)

# ========== 路径配置 ==========
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
PUBLIC_FOLDER = os.path.join(BASE_DIR, "public")
DB_PATH = os.path.join(BASE_DIR, "database.db")

# ========== 账号配置 ==========
try:
    from config import USERS
except ImportError:
    USERS = {
        os.environ.get("ADMIN_USER", "admin"): os.environ.get("ADMIN_PASS", "")
    }
    if not list(USERS.values())[0]:
        raise RuntimeError("请创建 config.py 并配置 USERS 字典")

def check_login(username, password):
    return username in USERS and USERS[username] == password

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("username"):
            return jsonify({"code": -1, "msg": "未登录"}), 401
        return f(*args, **kwargs)
    return decorated

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ========== 数据库 ==========
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
    CREATE TABLE IF NOT EXISTS notes (
        id TEXT PRIMARY KEY,
        title TEXT,
        content TEXT,
        create_time TEXT,
        update_time TEXT,
        img_path TEXT,
        audio_path TEXT,
        is_public INTEGER DEFAULT 0,
        tags TEXT DEFAULT '',
        img_paths TEXT DEFAULT '[]',
        owner TEXT DEFAULT ''
    )
    ''')
    for col, definition in [
        ("tags", "TEXT DEFAULT ''"),
        ("img_paths", "TEXT DEFAULT '[]'"),
        ("owner", "TEXT DEFAULT ''"),
    ]:
        try:
            c.execute(f"ALTER TABLE notes ADD COLUMN {col} {definition}")
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()

init_db()

def get_db_conn():
    return sqlite3.connect(DB_PATH)

def row_to_dict(r):
    img_paths_raw = r[9] if len(r) > 9 else ""
    try:
        img_paths = json.loads(img_paths_raw) if img_paths_raw else []
    except Exception:
        img_paths = []
    if not img_paths and r[5]:
        img_paths = [r[5]]
    return {
        "id": r[0], "title": r[1], "content": r[2],
        "create_time": r[3], "update_time": r[4],
        "img_path": r[5], "img_paths": img_paths,
        "audio_path": r[6], "is_public": r[7],
        "tags": r[8] if len(r) > 8 else "",
        "owner": r[10] if len(r) > 10 else ""
    }

# ========== 认证 API ==========
@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json() or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if check_login(username, password):
        session["username"] = username
        return jsonify({"code": 0, "msg": "登录成功", "username": username})
    return jsonify({"code": -1, "msg": "账号或密码错误"})

@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"code": 0, "msg": "已退出"})

@app.route("/api/me", methods=["GET"])
def me():
    username = session.get("username")
    if username:
        return jsonify({"code": 0, "username": username})
    return jsonify({"code": -1, "msg": "未登录"}), 401

# ========== 卡片 API ==========
@app.route("/api/note", methods=["POST"])
@login_required
def create_note():
    owner = session["username"]
    note_id = request.form.get("id")
    title = request.form.get("title", "").strip()
    content = request.form.get("content", "").strip()
    is_public = int(request.form.get("is_public", 0))
    tags = request.form.get("tags", "")
    now = datetime.utcnow().isoformat()

    img_files = request.files.getlist("img")
    audio_file = request.files.get("audio")

    has_text = bool(title or content)
    has_img = any(f and f.filename for f in img_files)
    has_audio = bool(audio_file and audio_file.filename)
    if not (has_text or has_img or has_audio):
        return jsonify({"code": -1, "msg": "标题、正文、图片、语音至少要有一样"})

    img_paths = []
    for f in img_files[:9]:
        if f and f.filename:
            ext = os.path.splitext(f.filename)[1] or ".jpg"
            save_name = f"{uuid.uuid4()}{ext}"
            f.save(os.path.join(UPLOAD_FOLDER, save_name))
            img_paths.append(save_name)

    audio_path = ""
    if audio_file and audio_file.filename:
        ext = os.path.splitext(audio_file.filename)[1] or ".webm"
        save_name = f"{uuid.uuid4()}{ext}"
        audio_file.save(os.path.join(UPLOAD_FOLDER, save_name))
        audio_path = save_name

    conn = get_db_conn()
    c = conn.cursor()
    try:
        c.execute('''
            INSERT INTO notes(id,title,content,create_time,update_time,
                              img_path,audio_path,is_public,tags,img_paths,owner)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ''', (
            note_id, title, content, now, now,
            img_paths[0] if img_paths else "",
            audio_path, is_public, tags,
            json.dumps(img_paths, ensure_ascii=False),
            owner
        ))
        conn.commit()
        return jsonify({"code": 0, "msg": "创建成功"})
    except Exception as e:
        return jsonify({"code": -1, "msg": str(e)})
    finally:
        conn.close()

@app.route("/api/note/list", methods=["GET"])
@login_required
def list_notes():
    owner = session["username"]
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM notes WHERE owner=? ORDER BY create_time DESC", (owner,))
    rows = c.fetchall()
    conn.close()
    return jsonify({"code": 0, "data": [row_to_dict(r) for r in rows]})

@app.route("/api/note/<note_id>", methods=["GET"])
def get_note(note_id):
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM notes WHERE id=?", (note_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return jsonify({"code": -1, "msg": "不存在"})

    admin = request.args.get("admin") == "1"
    if admin:
        if not session.get("username"):
            return jsonify({"code": -1, "msg": "未登录"}), 401
        if row[10] != session["username"]:
            return jsonify({"code": -1, "msg": "无权访问"}), 403

    if row[7] == 0 and not admin:
        return jsonify({"code": -1, "msg": "该卡片未公开"})

    return jsonify({"code": 0, "data": row_to_dict(row)})

@app.route("/api/note/<note_id>", methods=["PUT"])
@login_required
def update_note(note_id):
    owner = session["username"]
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("SELECT owner FROM notes WHERE id=?", (note_id,))
    row = c.fetchone()
    if not row or row[0] != owner:
        conn.close()
        return jsonify({"code": -1, "msg": "无权修改"}), 403

    data = request.get_json()
    title = data.get("title", "").strip()
    content = data.get("content", "").strip()
    is_public = int(data.get("is_public", 0))
    tags = data.get("tags", "")
    now = datetime.utcnow().isoformat()

    c.execute('''
        UPDATE notes SET title=?,content=?,is_public=?,tags=?,update_time=? WHERE id=?
    ''', (title, content, is_public, tags, now, note_id))
    conn.commit()
    conn.close()
    return jsonify({"code": 0})

@app.route("/api/note/<note_id>", methods=["DELETE"])
@login_required
def del_note(note_id):
    owner = session["username"]
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("SELECT owner FROM notes WHERE id=?", (note_id,))
    row = c.fetchone()
    if not row or row[0] != owner:
        conn.close()
        return jsonify({"code": -1, "msg": "无权删除"}), 403
    c.execute("DELETE FROM notes WHERE id=?", (note_id,))
    conn.commit()
    conn.close()
    return jsonify({"code": 0})

@app.route('/uploads/<filename>')
def upload_static(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/')
def index_page():
    return send_from_directory(PUBLIC_FOLDER, "index.html")

@app.route('/card.html')
def card_page():
    return send_from_directory(PUBLIC_FOLDER, "card.html")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000, debug=True)