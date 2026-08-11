import os
import sqlite3
import urllib.request
import urllib.error
import base64
import json
import re
import socket
import http.server
import socketserver
import threading
import secrets
import hashlib
import hmac
from http.cookies import SimpleCookie
from pathlib import Path
from datetime import datetime
from email.parser import BytesFeedParser
from email.policy import default

# ============================================================
# BudgetPet - Windows / Python 3.13+ compatible server
# ============================================================

PORT = int(os.environ.get("PORT", "3000"))

# Gemini key is intentionally read only from the environment. Never commit the
# real key to GitHub.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

BASE_DIR = Path(__file__).resolve().parent
DB_DIR = BASE_DIR / "database"
DB_DIR.mkdir(parents=True, exist_ok=True)
USERS_DIR = DB_DIR / "users"
USERS_DIR.mkdir(parents=True, exist_ok=True)
AUTH_DB_FILE = Path(os.environ.get("AUTH_DB_FILE", str(DB_DIR / "auth.db")))

# Demo target: at most 30 registered accounts.
MAX_DEMO_USERS = 30
SESSION_TTL_SECONDS = 60 * 60 * 24 * 30
PASSWORD_ITERATIONS = 220_000

# The original single-user DB path is kept only as a legacy fallback for the
# old helper functions. All authenticated requests use the current user's DB.
DB_FILE = Path(os.environ.get("DB_FILE", str(DB_DIR / "budgetpet.db")))
TEMPLATES_DIR = BASE_DIR / "templates"

_REQUEST_CONTEXT = threading.local()


def json_response(handler, data, status=200):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def text_response(handler, text, status=200, content_type="text/plain; charset=utf-8"):
    body = text.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def get_connection(db_file=None):
    if db_file is None:
        db_file = getattr(_REQUEST_CONTEXT, "db_file", None) or DB_FILE
    db_file = Path(db_file)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_file), timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.DatabaseError:
        pass
    return conn


def current_user_id():
    return getattr(_REQUEST_CONTEXT, "user_id", None)


def current_user_db():
    return getattr(_REQUEST_CONTEXT, "db_file", None)


def clear_request_context():
    for attr in ("user_id", "db_file"):
        if hasattr(_REQUEST_CONTEXT, attr):
            delattr(_REQUEST_CONTEXT, attr)


def hash_password(password, salt_hex=None):
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return salt.hex(), digest.hex()


def verify_password(password, salt_hex, expected_hash):
    _, actual = hash_password(password, salt_hex)
    return hmac.compare_digest(actual, expected_hash)


def get_auth_connection():
    AUTH_DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(AUTH_DB_FILE), timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    return conn


def init_auth_db():
    conn = get_auth_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            display_name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            password_salt TEXT NOT NULL,
            user_db TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
        CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);
    """)
    conn.commit()
    conn.close()


def user_db_path(user_db):
    path = (BASE_DIR / user_db).resolve()
    users_root = USERS_DIR.resolve()
    if users_root not in path.parents:
        raise ValueError("Đường dẫn database người dùng không hợp lệ")
    return path


def create_user_database(path):
    init_user_database(path)


def init_user_database(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(path)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS budgets (
            month_year TEXT PRIMARY KEY,
            monthly_limit REAL DEFAULT 3000000,
            monthly_income REAL DEFAULT 5000000
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            amount INTEGER,
            paid INTEGER,
            icon TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS jars (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            percentage INTEGER
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            merchant TEXT,
            title TEXT,
            amount INTEGER,
            time TEXT,
            category TEXT DEFAULT 'Khác',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            target REAL NOT NULL DEFAULT 0,
            current REAL NOT NULL DEFAULT 0,
            icon TEXT DEFAULT '🎯',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    defaults = {
        "pet_name": "Paws",
        "pet_type": "cat",
        "logo_image": "",
        "display_name": "",
    }
    for key, value in defaults.items():
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))

    cursor.execute("SELECT COUNT(*) AS count FROM bills")
    if cursor.fetchone()["count"] == 0:
        cursor.executemany(
            "INSERT INTO bills (title, amount, paid, icon) VALUES (?, ?, ?, ?)",
            [
                ("Tiền Phòng Trọ", 2500000, 0, "home"),
                ("Điện Nước", 320000, 1, "flash"),
                ("Spotify Student", 29000, 1, "music"),
                ("Internet Wifi", 110000, 0, "wifi"),
            ],
        )
    cursor.execute("SELECT COUNT(*) AS count FROM jars")
    if cursor.fetchone()["count"] == 0:
        cursor.executemany(
            "INSERT INTO jars (name, percentage) VALUES (?, ?)",
            [
                ("Nhu cầu thiết yếu", 55),
                ("Giáo dục", 10),
                ("Tiết kiệm", 0),
                ("Tự do tài chính", 10),
                ("Hưởng thụ", 10),
                ("Từ thiện", 5),
            ],
        )
    conn.commit()
    conn.close()


def create_session(user_id):
    token = secrets.token_urlsafe(48)
    expires = int(datetime.now().timestamp()) + SESSION_TTL_SECONDS
    conn = get_auth_connection()
    conn.execute("INSERT INTO sessions(token, user_id, expires_at) VALUES (?, ?, ?)", (token, user_id, expires))
    conn.commit()
    conn.close()
    return token, expires


def delete_session(token):
    if not token:
        return
    conn = get_auth_connection()
    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    conn.commit()
    conn.close()


def parse_cookie_token(handler):
    cookie = SimpleCookie()
    try:
        cookie.load(handler.headers.get("Cookie", ""))
        return cookie.get("budgetpet_session").value if cookie.get("budgetpet_session") else ""
    except Exception:
        return ""


def session_user(handler):
    token = parse_cookie_token(handler)
    if not token:
        return None
    now = int(datetime.now().timestamp())
    conn = get_auth_connection()
    row = conn.execute("""
        SELECT u.id, u.username, u.display_name, u.user_db, s.expires_at
        FROM sessions s JOIN users u ON u.id = s.user_id
        WHERE s.token = ? AND s.expires_at > ?
    """, (token, now)).fetchone()
    if row is None:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
        conn.close()
        return None
    conn.close()
    try:
        db_path = user_db_path(row["user_db"])
        init_user_database(db_path)
    except Exception:
        return None
    _REQUEST_CONTEXT.user_id = int(row["id"])
    _REQUEST_CONTEXT.db_file = db_path
    return dict(row)


def set_session_cookie(handler, token, expires):
    secure = handler.headers.get("X-Forwarded-Proto", "").lower() == "https"
    parts = [f"budgetpet_session={token}", "Path=/", "HttpOnly", "SameSite=Lax", f"Max-Age={SESSION_TTL_SECONDS}"]
    if secure:
        parts.append("Secure")
    handler.send_header("Set-Cookie", "; ".join(parts))


def clear_session_cookie(handler):
    handler.send_header("Set-Cookie", "budgetpet_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0")


def count_users():
    conn = get_auth_connection()
    count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    return int(count)


def database_is_valid():
    if not DB_FILE.exists() or DB_FILE.stat().st_size == 0:
        return False

    try:
        conn = sqlite3.connect(str(DB_FILE))
        result = conn.execute("PRAGMA integrity_check").fetchone()
        conn.close()
        return bool(result and result[0] == "ok")
    except sqlite3.DatabaseError:
        return False


def prepare_database_file():
    """
    The ZIP supplied with the project may contain a database copied while it
    was being written. If SQLite reports it as corrupt, keep a backup and
    create a fresh database instead of crashing the server.
    """
    if DB_FILE.exists() and not database_is_valid():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = DB_FILE.with_name(f"{DB_FILE.stem}_corrupt_{stamp}{DB_FILE.suffix}")
        try:
            DB_FILE.rename(backup)
            print(f"⚠️ Database không hợp lệ. Đã đổi tên bản cũ thành: {backup.name}")
        except OSError as exc:
            print(f"⚠️ Không thể đổi tên database cũ: {exc}")


def init_sqlite_db():
    init_auth_db()
    # Compatibility helper: initialize the legacy local DB only when explicitly requested.
    if current_user_db():
        init_user_database(current_user_db())


def get_db_data():
    conn = get_connection()
    cursor = conn.cursor()

    current_my = datetime.now().strftime("%m/%Y")
    cursor.execute(
        "SELECT monthly_limit, monthly_income FROM budgets WHERE month_year = ?",
        (current_my,),
    )
    row = cursor.fetchone()

    if row:
        monthly_limit = float(row["monthly_limit"])
        monthly_income = float(row["monthly_income"])
    else:
        cursor.execute("SELECT value FROM settings WHERE key = 'total_budget'")
        srow = cursor.fetchone()
        monthly_limit = float(srow["value"]) if srow else 3000000
        monthly_income = 5000000

    cursor.execute("""
        SELECT id, merchant, title, amount, time, category
        FROM transactions
        ORDER BY id DESC
    """)
    tx_rows = cursor.fetchall()

    transactions = []
    category_summary_map = {}
    total_spent = 0

    for r in tx_rows:
        cat = r["category"] or "Khác"
        amt = r["amount"] or 0
        total_spent += amt

        transactions.append({
            "id": r["id"],
            "merchant": r["merchant"],
            "title": r["title"],
            "amount": amt,
            "time": r["time"],
            "category": cat,
        })

        if cat not in category_summary_map:
            category_summary_map[cat] = {
                "category": cat,
                "total_amount": 0,
                "count": 0,
            }

        category_summary_map[cat]["total_amount"] += amt
        category_summary_map[cat]["count"] += 1

    category_summary = list(category_summary_map.values())

    cursor.execute("SELECT id, title, amount, paid, icon FROM bills ORDER BY id ASC")
    bills = [dict(r) for r in cursor.fetchall()]
    paid_bill_total = sum(int(b["amount"] or 0) for b in bills if int(b["paid"] or 0) == 1)
    unpaid_bill_total = sum(int(b["amount"] or 0) for b in bills if int(b["paid"] or 0) == 0)
    total_spent += paid_bill_total

    cursor.execute("SELECT id, name, percentage FROM jars ORDER BY id ASC")
    jars = [dict(r) for r in cursor.fetchall()]

    cursor.execute("""
        SELECT id, name, target, current, icon
        FROM goals
        ORDER BY id DESC
    """)
    goals = [dict(r) for r in cursor.fetchall()]

    cursor.execute("SELECT key, value FROM settings WHERE key IN ('pet_name','pet_type','logo_image','display_name')")
    settings = {r["key"]: r["value"] for r in cursor.fetchall()}

    conn.close()

    remaining = max(0, monthly_limit - total_spent)
    ramen_index = int(remaining // 4500)

    ratio = total_spent / monthly_limit if monthly_limit > 0 else 0

    pet_type = settings.get("pet_type") or "cat"
    pet_name = settings.get("pet_name") or "Paws"
    if ratio < 0.5:
        quote = f'"Chủ nhân tiết kiệm giỏi quá! {pet_name} cảm thấy rất vui!"'
    elif ratio <= 0.8:
        quote = '"Vẫn trong tầm kiểm soát! Giữ vững phong độ nha!"'
    else:
        quote = '"Cứu! Sắp phải ăn mì tôm cả tháng rồi sếp ơi!!"'
    pet = {
        "type": pet_type,
        "name": pet_name,
        "quote": quote,
        "logo_image": settings.get("logo_image") or "",
    }

    return {
        "total_budget": monthly_limit,
        "monthly_limit": monthly_limit,
        "monthly_income": monthly_income,
        "total_spent": total_spent,
        "paid_bill_total": paid_bill_total,
        "unpaid_bill_total": unpaid_bill_total,
        "ramen_index": ramen_index,
        "pet": pet,
        "display_name": settings.get("display_name") or "",
        "bills": bills,
        "jars": jars,
        "transactions": transactions,
        "category_summary": category_summary,
        "goals": goals,
    }


def parse_json_body(handler):
    content_length = int(handler.headers.get("Content-Length", "0"))
    if content_length <= 0:
        return {}

    raw = handler.rfile.read(content_length)
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def to_amount(value):
    if isinstance(value, str):
        value = value.replace("đ", "").replace("Đ", "").replace("VND", "").replace("vnd", "").replace(".", "").replace(",", "").strip()
    try:
        amount = int(float(value))
    except (TypeError, ValueError):
        raise ValueError("Số tiền không hợp lệ")
    if amount < 0:
        raise ValueError("Số tiền không được âm")
    return amount


def parse_uploaded_file(handler):
    """
    Replacement for the removed cgi.FieldStorage API.
    Uses Python's standard-library email multipart parser.
    """
    content_type = handler.headers.get("Content-Type", "")

    if "multipart/form-data" not in content_type.lower():
        return None, None, "Content-Type must be multipart/form-data"

    content_length = int(handler.headers.get("Content-Length", "0"))
    if content_length <= 0:
        return None, None, "No file uploaded"

    raw_body = handler.rfile.read(content_length)

    parser = BytesFeedParser(policy=default)
    parser.feed(
        (
            f"Content-Type: {content_type}\r\n"
            "MIME-Version: 1.0\r\n"
            "\r\n"
        ).encode("ascii", "surrogateescape")
    )
    parser.feed(raw_body)
    message = parser.close()

    if not message.is_multipart():
        return None, None, "Invalid multipart/form-data request"

    for part in message.iter_parts():
        disposition = part.get("Content-Disposition", "")
        field_name = part.get_param("name", header="content-disposition")

        if field_name == "file" or 'name="file"' in disposition:
            file_bytes = part.get_payload(decode=True)
            if not file_bytes:
                return None, None, "Uploaded file is empty"

            mime_type = part.get_content_type() or "image/jpeg"
            return file_bytes, mime_type, None

    return None, None, "No file uploaded"


def extract_gemini_text(gen_data):
    try:
        candidates = gen_data.get("candidates", [])
        if not candidates:
            raise ValueError("Gemini không trả về candidates")

        content = candidates[0].get("content", {})
        parts = content.get("parts", [])

        if not parts:
            raise ValueError("Gemini không trả về parts")

        text = parts[0].get("text")
        if not text:
            raise ValueError("Gemini không trả về text")

        return text
    except (AttributeError, IndexError, KeyError, TypeError) as exc:
        raise ValueError(f"Phản hồi Gemini không đúng định dạng: {exc}") from exc


def clean_json_text(text):
    text = text.strip()

    # Gemini đôi khi trả JSON trong markdown code fence.
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)

    return text.strip()


class BudgetPetHTTPRequestHandler(http.server.BaseHTTPRequestHandler):

    def _send_json(self, data, status=200):
        json_response(self, data, status)

    def _send_text(self, text, status=200, content_type="text/plain; charset=utf-8"):
        text_response(self, text, status, content_type)

    def do_GET(self):
        clear_request_context()
        path = self.path.split("?", 1)[0]
        user = session_user(self)

        if path in ("/", "/index.html"):
            filename = "index.html" if user else "auth.html"
            template_path = TEMPLATES_DIR / filename
            if not template_path.exists():
                self._send_text(f"Không tìm thấy templates/{filename}", 404)
                return
            try:
                body = template_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except OSError as exc:
                self._send_text(f"Không thể đọc giao diện: {exc}", 500)
            return

        if path in ("/api/health", "/health"):
            self._send_json({
                "status": "ok",
                "language": "Python 3.13+",
                "engine": "BudgetPet Pure Python Server",
                "demo_max_users": MAX_DEMO_USERS,
                "registered_users": count_users(),
            })
            return

        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        if path == "/api/me":
            if not user:
                self._send_json({"authenticated": False}, 401)
                return
            self._send_json({
                "authenticated": True,
                "username": user["username"],
                "display_name": user["display_name"],
            })
            return

        if path == "/api/data":
            if not user:
                self._send_json({"success": False, "error": "Bạn chưa đăng nhập."}, 401)
                return
            try:
                self._send_json(get_db_data())
            except Exception as exc:
                print("Database error:", exc)
                self._send_json({"success": False, "error": f"Lỗi database: {exc}"}, 500)
            return

        self._send_json({"error": "Endpoint not found"}, 404)

    def do_POST(self):
        clear_request_context()
        path = self.path.split("?", 1)[0]

        # ----------------------------------------------------
        # Authentication: register / login / logout
        # ----------------------------------------------------
        if path == "/api/register":
            payload = parse_json_body(self)
            if not isinstance(payload, dict):
                self._send_json({"success": False, "error": "Dữ liệu đăng ký không hợp lệ."}, 400)
                return
            username = str(payload.get("username", "")).strip()
            display_name = str(payload.get("display_name", "")).strip()[:40]
            password = str(payload.get("password", ""))
            confirm = str(payload.get("confirm_password", ""))
            if not re.fullmatch(r"[A-Za-z0-9_.-]{3,24}", username):
                self._send_json({"success": False, "error": "Tên đăng nhập 3-24 ký tự, chỉ gồm chữ, số, ., _ hoặc -."}, 400)
                return
            if len(password) < 6:
                self._send_json({"success": False, "error": "Mật khẩu phải có ít nhất 6 ký tự."}, 400)
                return
            if password != confirm:
                self._send_json({"success": False, "error": "Mật khẩu xác nhận không khớp."}, 400)
                return
            if not display_name:
                display_name = username
            conn = get_auth_connection()
            try:
                conn.execute("BEGIN IMMEDIATE")
                count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                if count >= MAX_DEMO_USERS:
                    conn.rollback()
                    self._send_json({"success": False, "error": f"Bản demo giới hạn {MAX_DEMO_USERS} tài khoản."}, 400)
                    return
                salt, password_hash = hash_password(password)
                db_rel = f"database/users/{secrets.token_hex(16)}.db"
                cur = conn.execute(
                    "INSERT INTO users(username, display_name, password_hash, password_salt, user_db) VALUES (?, ?, ?, ?, ?)",
                    (username, display_name, password_hash, salt, db_rel),
                )
                user_id = cur.lastrowid
                conn.commit()
            except sqlite3.IntegrityError:
                conn.rollback()
                self._send_json({"success": False, "error": "Tên đăng nhập đã tồn tại."}, 409)
                return
            except Exception as exc:
                conn.rollback()
                self._send_json({"success": False, "error": f"Không thể tạo tài khoản: {exc}"}, 500)
                return
            finally:
                conn.close()
            try:
                user_db_file = BASE_DIR / db_rel
                create_user_database(user_db_file)
                user_conn = get_connection(user_db_file)
                user_conn.execute(
                    "INSERT INTO settings(key,value) VALUES('display_name',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (display_name,)
                )
                user_conn.commit()
                user_conn.close()
                token, expires = create_session(user_id)
                self.send_response(200)
                set_session_cookie(self, token, expires)
                body = json.dumps({"success": True, "display_name": display_name}, ensure_ascii=False).encode("utf-8")
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:
                # Remove the user record if its database could not be initialized.
                conn = get_auth_connection()
                conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
                conn.commit(); conn.close()
                self._send_json({"success": False, "error": f"Không thể khởi tạo dữ liệu tài khoản: {exc}"}, 500)
            return

        if path == "/api/login":
            payload = parse_json_body(self)
            if not isinstance(payload, dict):
                self._send_json({"success": False, "error": "Dữ liệu đăng nhập không hợp lệ."}, 400)
                return
            username = str(payload.get("username", "")).strip()
            password = str(payload.get("password", ""))
            conn = get_auth_connection()
            row = conn.execute("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)).fetchone()
            conn.close()
            if not row or not verify_password(password, row["password_salt"], row["password_hash"]):
                self._send_json({"success": False, "error": "Sai tên đăng nhập hoặc mật khẩu."}, 401)
                return
            token, expires = create_session(row["id"])
            self.send_response(200)
            set_session_cookie(self, token, expires)
            body = json.dumps({"success": True, "display_name": row["display_name"]}, ensure_ascii=False).encode("utf-8")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/logout":
            token = parse_cookie_token(self)
            delete_session(token)
            self.send_response(200)
            clear_session_cookie(self)
            body = b'{"success":true}'
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        user = session_user(self)
        if not user:
            self._send_json({"success": False, "error": "Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại."}, 401)
            return

        # ----------------------------------------------------
        # Scan bill with Gemini
        # ----------------------------------------------------
        if path == "/api/scan-bill":
            file_bytes, mime_type, error = parse_uploaded_file(self)

            if error:
                self._send_json({
                    "success": False,
                    "error": error,
                }, 400)
                return

            api_key = GEMINI_API_KEY
            if not api_key:
                self._send_json({
                    "success": False,
                    "error": "Chưa cấu hình Gemini API key.",
                }, 500)
                return

            try:
                img_base64 = base64.b64encode(file_bytes).decode("utf-8")

                prompt_text = """Bạn là chuyên gia kế toán AI chuyên xử lý chứng từ tài chính Việt Nam.
Hãy phân tích ảnh và trả về JSON hợp lệ theo đúng định dạng sau:

{
  "vendor": "Tên nơi chi tiền hoặc nền tảng mua sắm",
  "amount": 100000,
  "category": "Ăn uống | Mua sắm | Di chuyển | Học tập | Vui chơi | Hóa đơn | Khác",
  "date": "YYYY-MM-DD",
  "note": "Tóm tắt ngắn gọn món đồ/dịch vụ đã mua"
}

HƯỚNG DẪN:
1. PHIẾU GIAO HÀNG / COD:
   - amount: ưu tiên "Tiền thu Người nhận" hoặc "COD".
   - vendor: tên sàn như Shopee, Lazada...
   - note: tóm tắt món đồ.
   - category: phân loại theo món hàng.

2. NHÀ HÀNG / QUÁN ĂN / SIÊU THỊ:
   - amount: lấy "Thành tiền" hoặc "Tổng thanh toán" cuối cùng.
   - vendor: tên quán/cửa hàng.
   - note: tóm tắt 2-3 món chính.
   - category: Ăn uống hoặc Mua sắm.

3. SỐ TIỀN:
   - Trả về số nguyên, không có ký hiệu đ, VND, dấu chấm hoặc dấu phẩy.
"""

                req_data = {
                    "contents": [{
                        "parts": [
                            {
                                "inlineData": {
                                    "mimeType": mime_type,
                                    "data": img_base64,
                                }
                            },
                            {"text": prompt_text},
                        ]
                    }],
                    "generationConfig": {
                        "responseMimeType": "application/json",
                        "temperature": 0.1,
                    },
                }

                url = (
                    "https://generativelanguage.googleapis.com/v1beta/"
                    "models/gemini-2.5-flash:generateContent"
                    f"?key={api_key}"
                )

                req = urllib.request.Request(
                    url,
                    data=json.dumps(req_data).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )

                with urllib.request.urlopen(req, timeout=None) as response:
                    res_body = response.read()
                    gen_data = json.loads(res_body)

                ans_text = clean_json_text(extract_gemini_text(gen_data))
                result = json.loads(ans_text)

                valid_categories = [
                    "Ăn uống",
                    "Mua sắm",
                    "Vui chơi",
                    "Học tập",
                    "Hóa đơn",
                    "Di chuyển",
                    "Khác",
                ]

                final_category = result.get("category", "Khác")
                if final_category not in valid_categories:
                    final_category = "Khác"

                amount_val = result.get("amount", 0)

                if isinstance(amount_val, str):
                    try:
                        amount_val = int(
                            amount_val
                            .replace("đ", "")
                            .replace("Đ", "")
                            .replace("VND", "")
                            .replace("vnd", "")
                            .replace(".", "")
                            .replace(",", "")
                            .strip()
                        )
                    except ValueError:
                        amount_val = 0

                try:
                    amount_val = int(float(amount_val))
                except (TypeError, ValueError):
                    amount_val = 0

                current_time = datetime.now().strftime("%H:%M")

                conn = get_connection()
                cursor = conn.cursor()

                cursor.execute(
                    """
                    INSERT INTO transactions
                    (merchant, title, amount, time, category)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        result.get("vendor", "Giao dịch mới"),
                        result.get("note", "Chi tiêu"),
                        amount_val,
                        current_time,
                        final_category,
                    ),
                )

                tx_id = cursor.lastrowid
                conn.commit()
                conn.close()

                tx = {
                    "id": tx_id,
                    "merchant": result.get("vendor", "Giao dịch mới"),
                    "title": result.get("note", "Chi tiêu"),
                    "amount": amount_val,
                    "time": current_time,
                    "category": final_category,
                }

                full_data = get_db_data()

                self._send_json({
                    "success": True,
                    "transaction": tx,
                    "pet_status": full_data["pet"],
                })
                return

            except urllib.error.HTTPError as exc:
                try:
                    detail = exc.read().decode("utf-8", errors="replace")
                except Exception:
                    detail = str(exc)

                print("Gemini HTTP error:", detail)
                self._send_json({
                    "success": False,
                    "error": f"Gemini API HTTP {exc.code}: {detail}",
                }, 500)
                return

            except Exception as exc:
                print("Gemini error:", exc)
                self._send_json({
                    "success": False,
                    "error": f"Lỗi xử lý OCR: {exc}",
                }, 500)
                return

        # ----------------------------------------------------
        # Avatar upload is multipart, so it must be parsed before the JSON body reader.
        # ----------------------------------------------------
        if path == "/api/upload-logo":
            file_bytes, mime_type, error = parse_uploaded_file(self)
            if error:
                self._send_json({"success": False, "error": error}, 400); return
            if not mime_type.startswith("image/"):
                self._send_json({"success": False, "error": "Chỉ hỗ trợ ảnh"}, 400); return
            if len(file_bytes) > 5 * 1024 * 1024:
                self._send_json({"success": False, "error": "Ảnh tối đa 5MB"}, 400); return
            data_url = "data:" + mime_type + ";base64," + base64.b64encode(file_bytes).decode("ascii")
            conn = get_connection(); cursor = conn.cursor()
            cursor.execute("INSERT INTO settings(key,value) VALUES('logo_image',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (data_url,))
            conn.commit(); conn.close()
            self._send_json({"success": True, "logo_image": data_url}); return

        # ----------------------------------------------------
        # All remaining endpoints use JSON
        # ----------------------------------------------------
        payload = parse_json_body(self)

        if payload is None:
            self._send_json({
                "success": False,
                "error": "JSON không hợp lệ",
            }, 400)
            return

        if path == "/api/set-budget":
            monthly_limit = payload.get("monthly_limit")
            monthly_income = payload.get("monthly_income")

            if monthly_limit is None or monthly_income is None:
                self._send_json({"error": "Missing params"}, 400)
                return

            current_my = datetime.now().strftime("%m/%Y")

            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO budgets
                    (month_year, monthly_limit, monthly_income)
                VALUES (?, ?, ?)
                ON CONFLICT(month_year) DO UPDATE SET
                    monthly_limit = excluded.monthly_limit,
                    monthly_income = excluded.monthly_income
                """,
                (current_my, float(monthly_limit), float(monthly_income)),
            )
            conn.commit()
            conn.close()

            self._send_json({"success": True})
            return

        if path == "/api/toggle-bill":
            bill_id = payload.get("id")

            if not bill_id:
                self._send_json({"error": "Missing bill id"}, 400)
                return

            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE bills SET paid = CASE WHEN paid = 1 THEN 0 ELSE 1 END WHERE id = ?",
                (bill_id,),
            )
            conn.commit()
            conn.close()

            self._send_json({"success": True})
            return

        if path == "/api/add-bill":
            title = str(payload.get("title", "")).strip()
            icon = str(payload.get("icon", "home")).strip() or "home"
            try:
                amount = to_amount(payload.get("amount"))
            except ValueError as exc:
                self._send_json({"success": False, "error": str(exc)}, 400)
                return
            if not title or amount <= 0:
                self._send_json({"success": False, "error": "Tên hóa đơn và số tiền là bắt buộc"}, 400)
                return
            conn = get_connection(); cursor = conn.cursor()
            cursor.execute("INSERT INTO bills (title, amount, paid, icon) VALUES (?, ?, 0, ?)", (title, amount, icon))
            bill_id = cursor.lastrowid
            conn.commit(); conn.close()
            self._send_json({"success": True, "id": bill_id}); return

        if path == "/api/update-bill":
            bill_id = payload.get("id")
            title = str(payload.get("title", "")).strip()
            icon = str(payload.get("icon", "home")).strip() or "home"
            try:
                amount = to_amount(payload.get("amount"))
            except ValueError as exc:
                self._send_json({"success": False, "error": str(exc)}, 400); return
            if not bill_id or not title or amount <= 0:
                self._send_json({"success": False, "error": "Thông tin hóa đơn không hợp lệ"}, 400); return
            conn = get_connection(); cursor = conn.cursor()
            cursor.execute("UPDATE bills SET title=?, amount=?, icon=? WHERE id=?", (title, amount, icon, bill_id))
            conn.commit(); conn.close()
            self._send_json({"success": True}); return

        if path == "/api/delete-bill":
            bill_id = payload.get("id")
            if not bill_id:
                self._send_json({"success": False, "error": "Missing bill id"}, 400); return
            conn = get_connection(); cursor = conn.cursor()
            cursor.execute("DELETE FROM bills WHERE id=?", (bill_id,))
            conn.commit(); conn.close()
            self._send_json({"success": True}); return

        if path == "/api/set-pet":
            name = str(payload.get("name", "Paws")).strip()[:30] or "Paws"
            pet_type = str(payload.get("pet_type", "cat")).strip() or "cat"
            allowed = {"cat", "dog", "rabbit", "bear", "fox", "panda", "hamster", "penguin", "capybara", "frog", "axolotl"}
            if pet_type not in allowed:
                pet_type = "cat"
            conn = get_connection(); cursor = conn.cursor()
            cursor.execute("INSERT INTO settings(key,value) VALUES('pet_name',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (name,))
            cursor.execute("INSERT INTO settings(key,value) VALUES('pet_type',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (pet_type,))
            conn.commit(); conn.close()
            self._send_json({"success": True}); return

        if path == "/api/set-profile":
            name = str(payload.get("display_name", "")).strip()[:40]
            if not name:
                self._send_json({"success": False, "error": "Tên người dùng không được để trống"}, 400)
                return
            # Keep the account profile and the per-user settings in sync so the
            # greeting always uses the name chosen at registration/profile edit.
            user = session_user(self)
            if not user:
                self._send_json({"success": False, "error": "Phiên đăng nhập đã hết hạn."}, 401)
                return
            conn = get_connection(); cursor = conn.cursor()
            cursor.execute("INSERT INTO settings(key,value) VALUES('display_name',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (name,))
            conn.commit(); conn.close()
            auth = get_auth_connection()
            auth.execute("UPDATE users SET display_name=? WHERE id=?", (name, user["id"]))
            auth.commit(); auth.close()
            self._send_json({"success": True, "display_name": name}); return

        if path == "/api/remove-logo":
            conn = get_connection(); cursor = conn.cursor()
            cursor.execute("INSERT INTO settings(key,value) VALUES('logo_image','') ON CONFLICT(key) DO UPDATE SET value='' ")
            conn.commit(); conn.close()
            self._send_json({"success": True}); return

        if path in ("/api/update-jar", "/api/update-jars"):
            name = payload.get("name")
            percentage = payload.get("percentage")

            if not name or percentage is None:
                self._send_json({
                    "error": "Invalid jar parameters"
                }, 400)
                return

            try:
                percentage = int(percentage)
            except (TypeError, ValueError):
                self._send_json({
                    "error": "Percentage must be an integer"
                }, 400)
                return

            percentage = max(0, min(100, percentage))

            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE jars SET percentage = ? WHERE name = ?",
                (percentage, name),
            )
            conn.commit()
            conn.close()

            self._send_json({"success": True})
            return

        if path == "/api/delete-tx":
            tx_id = payload.get("id")

            if not tx_id:
                self._send_json({"error": "Missing tx id"}, 400)
                return

            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM transactions WHERE id = ?",
                (tx_id,),
            )
            conn.commit()
            conn.close()

            self._send_json({"success": True})
            return

        # ----------------------------------------------------
        # Goals - these endpoints are used by index.html
        # ----------------------------------------------------
        if path == "/api/add-goal":
            title = str(payload.get("title", "")).strip()
            target_amount = payload.get("target_amount")

            if not title:
                self._send_json({
                    "success": False,
                    "error": "Tên mục tiêu không được để trống",
                }, 400)
                return

            try:
                target_amount = float(target_amount)
            except (TypeError, ValueError):
                self._send_json({
                    "success": False,
                    "error": "Số tiền mục tiêu không hợp lệ",
                }, 400)
                return

            if target_amount <= 0:
                self._send_json({
                    "success": False,
                    "error": "Số tiền mục tiêu phải lớn hơn 0",
                }, 400)
                return

            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO goals (name, target, current, icon)
                VALUES (?, ?, 0, '🎯')
                """,
                (title, target_amount),
            )
            goal_id = cursor.lastrowid
            conn.commit()
            conn.close()

            self._send_json({
                "success": True,
                "id": goal_id,
            })
            return

        if path == "/api/add-to-goal":
            goal_id = payload.get("id")
            amount = payload.get("amount")

            if not goal_id or amount is None:
                self._send_json({
                    "success": False,
                    "error": "Missing goal parameters",
                }, 400)
                return

            try:
                amount = float(amount)
            except (TypeError, ValueError):
                self._send_json({
                    "success": False,
                    "error": "Số tiền không hợp lệ",
                }, 400)
                return

            if amount <= 0:
                self._send_json({
                    "success": False,
                    "error": "Số tiền phải lớn hơn 0",
                }, 400)
                return

            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE goals
                SET current = MIN(target, current + ?)
                WHERE id = ?
                """,
                (amount, goal_id),
            )
            conn.commit()
            conn.close()

            self._send_json({"success": True})
            return

        self._send_json({
            "error": "Unknown POST endpoint"
        }, 404)


class ReusableThreadingTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def run_server():
    init_sqlite_db()

    server_address = ("0.0.0.0", PORT)
    httpd = ReusableThreadingTCPServer(
        server_address,
        BudgetPetHTTPRequestHandler,
    )

    print("=" * 60)
    print("🚀 BudgetPet đang chạy")
    print(f"🌐 Trên máy tính: http://localhost:{PORT}")
    print("📱 Điện thoại cùng Wi-Fi: dùng IP LAN của máy tính, ví dụ http://192.168.1.10:%d" % PORT)
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip and not ip.startswith("127."):
            print(f"   IP LAN phát hiện: {ip}")
    except Exception:
        pass
    print(f"💾 Auth database: {AUTH_DB_FILE}")
    print(f"👥 Demo limit: {MAX_DEMO_USERS} tài khoản; mỗi tài khoản có SQLite riêng trong {USERS_DIR}")
    print("⛔ Nhấn Ctrl+C để dừng server")
    print("=" * 60, flush=True)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    run_server()
