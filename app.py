from flask import Flask, request, jsonify, send_from_directory, Response
from flask_cors import CORS
from functools import wraps
import sqlite3
import uuid
import os
from datetime import datetime

app = Flask(__name__)
CORS(app)

# ========== 路径配置（绝对路径，兼容本地和云端）==========
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
PUBLIC_FOLDER = os.path.join(BASE_DIR, "public")
DB_PATH = os.path.join(BASE_DIR, "database.db")

# ========== 认证配置 ==========
# 优先从 config.py 读取，读不到就用环境变量
try:
    from config import ADMIN_USER, ADMIN_PASS
except ImportError:
    ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
    ADMIN_PASS = os.environ.get("ADMIN_PASS", "")
    if not ADMIN_PASS:
        raise RuntimeError("请创建 config.py 文件设置 ADMIN_PASS，或设置环境变量 ADMIN_PASS")

def check_auth(username, password):
    return username == ADMIN_USER and password == ADMIN_PASS

def requires_auth(f):
    """需要 Basic Auth 才能访问的装饰器"""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return Response(
                "🔒 需要登录才能访问管理界面\n",
                401,
                {"WWW-Authenticate": 'Basic realm="Note Admin"'}
            )
        return f(*args, **kwargs)
    return decorated

# ========== 文件夹初始化 ==========
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ========== 数据库初始化 ==========
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
        tags TEXT DEFAULT ''
    )
    ''')
    try:
        c.execute("ALTER TABLE notes ADD COLUMN tags TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()

init_db()

def get_db_conn():
    return sqlite3.connect(DB_PATH)

def row_to_dict(r):
    return {
        "id": r[0],
        "title": r[1],
        "content": r[2],
        "create_time": r[3],
        "update_time": r[4],
        "img_path": r[5],
        "audio_path": r[6],
        "is_public": r[7],
        "tags": r[8] if len(r) > 8 else ""
    }

# ========== API 接口 ==========

# 创建笔记（需要认证）
@app.route("/api/note", methods=["POST"])
@requires_auth
def create_note():
    note_id = request.form.get("id")
    title = request.form.get("title", "")
    content = request.form.get("content", "")
    is_public = int(request.form.get("is_public", 0))
    tags = request.form.get("tags", "")
    now = datetime.utcnow().isoformat()

    img_file = request.files.get("img")
    audio_file = request.files.get("audio")
    img_path = ""
    audio_path = ""

    if img_file and img_file.filename:
        ext = os.path.splitext(img_file.filename)[1] or ".jpg"
        save_name = f"{uuid.uuid4()}{ext}"
        img_file.save(os.path.join(UPLOAD_FOLDER, save_name))
        img_path = save_name

    if audio_file and audio_file.filename:
        ext = os.path.splitext(audio_file.filename)[1] or ".webm"
        save_name = f"{uuid.uuid4()}{ext}"
        audio_file.save(os.path.join(UPLOAD_FOLDER, save_name))
        audio_path = save_name

    conn = get_db_conn()
    c = conn.cursor()
    try:
        c.execute('''
            INSERT INTO notes(id,title,content,create_time,update_time,img_path,audio_path,is_public,tags)
            VALUES (?,?,?,?,?,?,?,?,?)
        ''', (note_id, title, content, now, now, img_path, audio_path, is_public, tags))
        conn.commit()
        return jsonify({"code": 0, "msg": "创建成功"})
    except Exception as e:
        return jsonify({"code": -1, "msg": str(e)})
    finally:
        conn.close()

# 列表（需要认证）
@app.route("/api/note/list", methods=["GET"])
@requires_auth
def list_notes():
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM notes ORDER BY create_time DESC")
    rows = c.fetchall()
    conn.close()
    return jsonify({"code": 0, "data": [row_to_dict(r) for r in rows]})

# 获取单条（公开；带 admin=1 时需要认证）
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
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return Response(
                "🔒 需要登录\n",
                401,
                {"WWW-Authenticate": 'Basic realm="Note Admin"'}
            )

    if row[7] == 0 and not admin:
        return jsonify({"code": -1, "msg": "该卡片未公开"})

    return jsonify({"code": 0, "data": row_to_dict(row)})

# 修改（需要认证）
@app.route("/api/note/<note_id>", methods=["PUT"])
@requires_auth
def update_note(note_id):
    data = request.get_json()
    title = data.get("title", "")
    content = data.get("content", "")
    is_public = int(data.get("is_public", 0))
    tags = data.get("tags", "")
    now = datetime.utcnow().isoformat()
    conn = get_db_conn()
    c = conn.cursor()
    c.execute('''
        UPDATE notes SET title=?,content=?,is_public=?,tags=?,update_time=? WHERE id=?
    ''', (title, content, is_public, tags, now, note_id))
    conn.commit()
    conn.close()
    return jsonify({"code": 0})

# 删除（需要认证）
@app.route("/api/note/<note_id>", methods=["DELETE"])
@requires_auth
def del_note(note_id):
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("DELETE FROM notes WHERE id=?", (note_id,))
    conn.commit()
    conn.close()
    return jsonify({"code": 0})

# ========== 静态资源 ==========
@app.route('/uploads/<filename>')
def upload_static(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

# ========== 前端页面路由 ==========
# 管理界面（需要认证）
@app.route('/')
@requires_auth
def index_page():
    return send_from_directory(PUBLIC_FOLDER, "index.html")

# 卡片展示页（公开，扫码可访问）
@app.route('/card.html')
def card_page():
    return send_from_directory(PUBLIC_FOLDER, "card.html")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000, debug=True)