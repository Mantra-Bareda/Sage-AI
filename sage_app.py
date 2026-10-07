import os
import json
import uuid
import sqlite3
from datetime import datetime, timedelta
from functools import wraps

from dotenv import load_dotenv

# 1. Automatically find the exact folder where this app.py file lives
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 2. LOAD ENV VARS IMMEDIATELY (Before importing Gemini/Firebase)
load_dotenv(os.path.join(BASE_DIR, '.env'))

from flask import Flask, g, render_template, request, jsonify, session, Response, redirect, url_for
from flask import send_from_directory, abort
from werkzeug.utils import secure_filename

import bleach

import firebase_admin
from firebase_admin import credentials, auth

# 3. NOW import custom services safely
from services.pdf_parser import extract_pdf_text, extract_docx_text
from services.gemini_client import (
    generate_unit_notes,
    generate_topic_notes,
    generate_mcq_questions,
    generate_flashcards,
    generate_practice_questions,
    generate_mixed_questions,
    get_units_list,
    extract_structured_syllabus,
    generate_extra_notes,
    generate_podcast_script,
)
from services.groq_client import generate_mastery_mcqs

# Safely attach our database and uploads folder to that exact path
DATABASE_PATH = os.path.join(BASE_DIR, "app.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
MAX_CONTENT_LENGTH_MB = int(os.environ.get("MAX_CONTENT_LENGTH_MB", "20"))

# Initialize Firebase Admin SDK
try:
    cred = credentials.Certificate(os.path.join(BASE_DIR, "firebase-adminsdk.json"))
    firebase_admin.initialize_app(cred)
except Exception as e:
    print(f"Warning: Firebase Admin SDK initialization failed. {e}")


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "sage-ai-secret-key-dev")
    app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH_MB * 1024 * 1024

    os.makedirs(UPLOAD_DIR, exist_ok=True)


    @app.before_request
    def _db_connect():
        conn = sqlite3.connect(DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        g.db = conn

    @app.teardown_request
    def _db_close(_exc):
        conn = getattr(g, "db", None)
        if conn is not None:
            conn.close()

    def ensure_tables() -> None:
        conn = sqlite3.connect(DATABASE_PATH)
        cur = conn.cursor()

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            )
            """
        )

        # Update existing database to support auth seamlessly
        cur.execute("PRAGMA table_info(users)")
        columns = [col[1] for col in cur.fetchall()]
        if "username" not in columns:
            cur.execute("ALTER TABLE users ADD COLUMN username TEXT")
            cur.execute("ALTER TABLE users ADD COLUMN credits REAL NOT NULL DEFAULT 10")
            cur.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
        if "email" not in columns:
            cur.execute("ALTER TABLE users ADD COLUMN email TEXT")

        # We no longer require usernames to be unique since we use Firebase UID for login
        cur.execute("DROP INDEX IF EXISTS idx_users_username")

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS syllabi (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                course_name TEXT NOT NULL,
                class_name TEXT NOT NULL,
                language TEXT NOT NULL,
                created_at TEXT NOT NULL,
                pdf_path TEXT NOT NULL,
                raw_text TEXT,
                meta_json TEXT,
                notes_json TEXT,
                practice_json TEXT,
                study_json TEXT,
                extra_notes_json TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )

        cur.execute("PRAGMA table_info(syllabi)")
        columns = [col[1] for col in cur.fetchall()]
        if "extra_notes_json" not in columns:
            cur.execute("ALTER TABLE syllabi ADD COLUMN extra_notes_json TEXT")
        if "cheat_sheet" not in columns:
            cur.execute("ALTER TABLE syllabi ADD COLUMN cheat_sheet TEXT")
        if "podcast_json" not in columns:
            cur.execute("ALTER TABLE syllabi ADD COLUMN podcast_json TEXT")

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS extra_materials (
                id TEXT PRIMARY KEY,
                syllabus_id TEXT NOT NULL,
                material_type TEXT NOT NULL,
                file_path TEXT,
                original_name TEXT,
                text_content TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(syllabus_id) REFERENCES syllabi(id)
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS mcq_attempts (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                syllabus_id TEXT NOT NULL,
                unit_index INTEGER NOT NULL,
                topic_index INTEGER NOT NULL,
                question_count INTEGER NOT NULL,
                score INTEGER NOT NULL,
                time_seconds INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS chats (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                syllabus_id TEXT NOT NULL,
                unit_name TEXT NOT NULL,
                role TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS flashcard_reviews (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                syllabus_id TEXT NOT NULL,
                unit_name TEXT NOT NULL,
                front_text TEXT NOT NULL,
                next_review_date TEXT NOT NULL,
                ease_factor REAL NOT NULL,
                interval INTEGER NOT NULL,
                repetitions INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS units (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                syllabus_id TEXT NOT NULL,
                unit_name TEXT NOT NULL,
                world_order INTEGER DEFAULT 0,
                world_theme TEXT DEFAULT 'default',
                unit_exp_reward INTEGER DEFAULT 100,
                FOREIGN KEY(syllabus_id) REFERENCES syllabi(id) ON DELETE CASCADE
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS topics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                unit_id INTEGER NOT NULL,
                topic_name TEXT NOT NULL,
                node_order INTEGER DEFAULT 0,
                is_boss_level BOOLEAN DEFAULT 0,
                topic_exp_reward INTEGER DEFAULT 20,
                FOREIGN KEY(unit_id) REFERENCES units(id) ON DELETE CASCADE
            )
            """
        )

        cur.execute("PRAGMA table_info(units)")
        unit_cols = [col[1] for col in cur.fetchall()]
        if "world_order" not in unit_cols: cur.execute("ALTER TABLE units ADD COLUMN world_order INTEGER DEFAULT 0")
        if "world_theme" not in unit_cols: cur.execute("ALTER TABLE units ADD COLUMN world_theme TEXT DEFAULT 'default'")
        if "unit_exp_reward" not in unit_cols: cur.execute("ALTER TABLE units ADD COLUMN unit_exp_reward INTEGER DEFAULT 100")

        cur.execute("PRAGMA table_info(topics)")
        topic_cols = [col[1] for col in cur.fetchall()]
        if "node_order" not in topic_cols: cur.execute("ALTER TABLE topics ADD COLUMN node_order INTEGER DEFAULT 0")
        if "is_boss_level" not in topic_cols: cur.execute("ALTER TABLE topics ADD COLUMN is_boss_level BOOLEAN DEFAULT 0")
        if "topic_exp_reward" not in topic_cols: cur.execute("ALTER TABLE topics ADD COLUMN topic_exp_reward INTEGER DEFAULT 20")

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS user_stats (
                user_id TEXT PRIMARY KEY,
                total_exp INTEGER DEFAULT 0,
                current_level INTEGER DEFAULT 1,
                current_streak INTEGER DEFAULT 0,
                last_active_date TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS topic_progress (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                topic_id INTEGER NOT NULL,
                status TEXT DEFAULT 'locked',
                exp_awarded INTEGER DEFAULT 0,
                current_streak INTEGER DEFAULT 0,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(topic_id) REFERENCES topics(id) ON DELETE CASCADE
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS boss_fight_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                unit_id INTEGER NOT NULL,
                starting_hp INTEGER DEFAULT 3,
                ending_hp INTEGER,
                result TEXT,
                questions_seen TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(unit_id) REFERENCES units(id) ON DELETE CASCADE
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS note_customization_preferences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                syllabus_id TEXT,
                exam_oriented BOOLEAN DEFAULT 0,
                code_heavy BOOLEAN DEFAULT 0,
                eli5_analogies BOOLEAN DEFAULT 0,
                step_by_step BOOLEAN DEFAULT 0,
                custom_prompt_text TEXT,
                is_default_profile BOOLEAN DEFAULT 0,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(syllabus_id) REFERENCES syllabi(id) ON DELETE CASCADE
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS note_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic_id INTEGER NOT NULL,
                sequence_order INTEGER DEFAULT 0,
                chunk_type TEXT,
                title TEXT,
                content TEXT,
                FOREIGN KEY(topic_id) REFERENCES topics(id) ON DELETE CASCADE
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS card_review_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                chunk_id INTEGER NOT NULL,
                signal TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(chunk_id) REFERENCES note_chunks(id) ON DELETE CASCADE
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS boss_fight_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                unit_id TEXT NOT NULL,
                hp INTEGER DEFAULT 3,
                result TEXT DEFAULT 'in_progress',
                questions_json TEXT,
                failed_questions_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS skills (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                skill_name TEXT NOT NULL,
                category TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS skill_nodes (
                id TEXT PRIMARY KEY,
                skill_id TEXT NOT NULL,
                node_order INTEGER NOT NULL,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'locked',
                theory_text TEXT,
                practice_q TEXT,
                youtube_query TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(skill_id) REFERENCES skills(id)
            )
            """
        )

        conn.commit()
        conn.close()

    ensure_tables()

    def login_required(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if "user_id" not in session:
                if request.path.startswith('/api/'):
                    return jsonify({"ok": False, "error": "Unauthorized"}), 401
                return redirect(url_for('login'))
            return f(*args, **kwargs)
        return decorated_function

    def admin_required(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if "user_id" not in session:
                return redirect(url_for('login'))
            if not g.user or g.user['role'] != 'admin':
                if request.path.startswith('/api/'):
                    return jsonify({"ok": False, "error": "Admin access required"}), 403
                return abort(403)
            return f(*args, **kwargs)
        return decorated_function

    @app.before_request
    def _ensure_user():
        g.user = None
        g.user_stats = None
        if "user_id" in session:
            g.user = g.db.execute("SELECT id, username, role, credits FROM users WHERE id = ?", (session["user_id"],)).fetchone()
            if g.user:
                g.user_stats = g.db.execute("SELECT total_exp, current_level FROM user_stats WHERE user_id = ?", (session["user_id"],)).fetchone()

    @app.route("/login", methods=["GET", "POST"])
    def login():
        return render_template("login.html")

    @app.post("/api/auth/google")
    def api_auth_google():
        data = request.get_json(force=True, silent=True) or {}
        id_token = data.get("token")
        if not id_token:
            return jsonify({"ok": False, "error": "No token provided"}), 400

        try:
            # Validate Token via Firebase Admin
            decoded_token = auth.verify_id_token(id_token)
            uid = decoded_token['uid']
            email = decoded_token.get('email', '')
            username = decoded_token.get('name', email.split('@')[0])

            # Instead of ADMIN_USERNAME, we use an ADMIN_EMAIL environment variable
            admin_email = os.environ.get("ADMIN_EMAIL")
            role = 'admin' if admin_email and email == admin_email else 'user'

            user = g.db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()

            if not user:
                # New User: Setup exactly 10 credits
                g.db.execute(
                    "INSERT INTO users (id, username, email, role, credits, created_at) VALUES (?, ?, ?, ?, 10, ?)",
                    (uid, username, email, role, datetime.utcnow().isoformat())
                )
                g.db.commit()
            else:
                # Existing User: Elevate to admin if emails matched env var
                if role == 'admin' and user['role'] != 'admin':
                    g.db.execute("UPDATE users SET role = 'admin' WHERE id = ?", (uid,))
                    g.db.commit()

            session["user_id"] = uid
            return jsonify({"ok": True})
        except Exception as e:
            print(f"Firebase Auth Error: {e}")
            return jsonify({"ok": False, "error": "Invalid authentication token or session expired. Please try again."}), 401

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    def require_syllabus():
        syllabus_id = request.args.get("syllabus_id") or request.json.get("syllabus_id") if request.is_json else None
        if not syllabus_id:
            return None
        row = g.db.execute("SELECT * FROM syllabi WHERE id = ? AND user_id = ?", (syllabus_id, session["user_id"])).fetchone()
        if row is None:
            return None
        return row

    def sanitize_text(text: str) -> str:
        return bleach.clean(text or "", tags=[], attributes={}, strip=True)

    def api_route(path, methods):
        def decorator(fn):
            endpoint_name = fn.__name__
            app.add_url_rule(path, endpoint_name, fn, methods=methods)
            return fn

        return decorator

    @app.get("/")
    def index():
        return render_template("index.html", user=g.user)

    @app.get("/upload")
    @login_required
    def upload_page():
        return render_template("upload.html")

    @app.get("/contact")
    def contact():
        return render_template("contact.html")

    @app.get("/api/user/me")
    def api_user_me():
        if "user_id" in session:
            user = g.db.execute("SELECT username FROM users WHERE id = ?", (session["user_id"],)).fetchone()
            if g.user:
                return jsonify({"ok": True, "username": g.user["username"], "role": g.user["role"], "credits": g.user["credits"]})
        return jsonify({"ok": False, "error": "Not logged in"})

    @app.route("/manifest.json")
    def manifest():
        return jsonify({
            "name": "Sage AI",
            "short_name": "SageAI",
            "start_url": "/",
            "display": "standalone",
            "background_color": "#1e1e2f",
            "theme_color": "#3b82f6",
            "icons": [{"src": "https://cdn-icons-png.flaticon.com/512/4712/4712035.png", "sizes": "512x512", "type": "image/png"}]
        })

    @app.route("/sw.js")
    def sw():
        js = """
        const CACHE_NAME = 'sage-v4';
        self.addEventListener('install', e => {
            self.skipWaiting();
            e.waitUntil(caches.open(CACHE_NAME).then(c => c.addAll(['/static/main.js', '/static/style.css'])));
        });
        self.addEventListener('activate', e => {
            e.waitUntil(caches.keys().then(keys => Promise.all(
                keys.map(k => { if (k !== CACHE_NAME) return caches.delete(k); })
            )));
            self.clients.claim();
        });
        self.addEventListener('fetch', e => {
            if (e.request.mode === 'navigate') {
                e.respondWith(fetch(e.request));
            } else {
                // Network First strategy to prevent getting stuck on old JS code
                e.respondWith(
                    fetch(e.request).then(response => {
                        let resClone = response.clone();
                        caches.open(CACHE_NAME).then(cache => cache.put(e.request, resClone));
                        return response;
                    }).catch(() => caches.match(e.request))
                );
            }
        });
        """
        return Response(js, mimetype="application/javascript")

    @app.post("/api/upload")
    @login_required
    def api_upload():
        syllabus_text = request.form.get("syllabus_text", "").strip()

        # Robust file retrieval: check explicit name first, then fallback to any file uploaded
        syllabus_pdf = request.files.get("syllabus_pdf")
        if not syllabus_pdf:
            for key in request.files:
                if request.files[key].filename:
                    syllabus_pdf = request.files[key]
                    break

        if not syllabus_pdf and not syllabus_text:
            return jsonify({"ok": False, "error": "Please provide a PDF or paste syllabus text."}), 200

        course_name = sanitize_text(request.form.get("course_name", "")).strip()
        class_name = sanitize_text(request.form.get("class_name", "")).strip()
        language = sanitize_text(request.form.get("language", "english_easy")).strip()

        if not course_name:
            course_name = "General Course"
        if not class_name:
            class_name = "General Class"

        syllabus_id = str(uuid.uuid4())

        filename = "text_upload.txt"
        pdf_path = ""
        raw_text = syllabus_text

        if syllabus_pdf and syllabus_pdf.filename:
            filename = secure_filename(syllabus_pdf.filename)
            pdf_path = os.path.join(UPLOAD_DIR, f"{syllabus_id}_{filename}")
            syllabus_pdf.save(pdf_path)

            ext = filename.lower().split('.')[-1]
            try:
                if ext == "pdf":
                    raw_text += "\n\n" + extract_pdf_text(pdf_path)
                elif ext == "docx":
                    raw_text += "\n\n" + extract_docx_text(pdf_path)
            except Exception as e:
                print(f"[Upload Extraction Error]: {e}")

        if not raw_text.strip():
            if os.path.exists(pdf_path):
                os.remove(pdf_path)
            return jsonify({"ok": False, "error": "Could not extract any text from the PDF. It might be scanned, image-only, or empty."}), 200

        # Convert whole syllabus text to an optimized structured hierarchical format immediately
        try:
            structured_text = extract_structured_syllabus(raw_text)
            if structured_text:
                raw_text = structured_text
        except ValueError as ve:
            # FALLBACK: If the AI rejects the document as an "invalid syllabus",
            # do not throw an error. Simply proceed with the raw unstructured text.
            print(f"Syllabus Validation Warning: {ve}")
        except Exception as e:
            if pdf_path and os.path.exists(pdf_path):
                os.remove(pdf_path)
            return jsonify({"ok": False, "error": str(e)}), 400

        meta_json = json.dumps(
            {
                "course_name": course_name,
                "class_name": class_name,
                "language": language,
                "filename": filename,
            }
        )

        g.db.execute(
            """
            INSERT INTO syllabi
            (id, user_id, filename, course_name, class_name, language, created_at, pdf_path, raw_text, meta_json,
             notes_json, practice_json, study_json, extra_notes_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                syllabus_id,
                session["user_id"],
                filename,
                course_name,
                class_name,
                language,
                datetime.utcnow().isoformat(),
                pdf_path,
                raw_text,
                meta_json,
                json.dumps({}),
                json.dumps({}),
                json.dumps({}),
                json.dumps({}),
            ),
        )

        exam_oriented = request.form.get("exam_oriented") == "on"
        code_heavy = request.form.get("code_heavy") == "on"
        eli5_analogies = request.form.get("eli5_analogies") == "on"
        step_by_step = request.form.get("step_by_step") == "on"
        custom_prompt_text = request.form.get("custom_prompt_text", "").strip()[:500]

        g.db.execute(
            """
            INSERT INTO note_customization_preferences 
            (user_id, syllabus_id, exam_oriented, code_heavy, eli5_analogies, step_by_step, custom_prompt_text)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (session["user_id"], syllabus_id, exam_oriented, code_heavy, eli5_analogies, step_by_step, custom_prompt_text)
        )

        g.db.commit()

        return jsonify({"ok": True, "syllabus_id": syllabus_id})

    @app.post("/api/syllabus/<syllabus_id>/customize")
    @login_required
    def api_customize_notes(syllabus_id: str):
        row = g.db.execute("SELECT * FROM syllabi WHERE id = ? AND user_id = ?", (syllabus_id, session["user_id"])).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "Not found"}), 404

        data = request.get_json(force=True) if request.is_json else {}
        exam_oriented = bool(data.get("exam_oriented", False))
        code_heavy = bool(data.get("code_heavy", False))
        eli5_analogies = bool(data.get("eli5_analogies", False))
        step_by_step = bool(data.get("step_by_step", False))
        custom_prompt_text = str(data.get("custom_prompt_text", "")).strip()[:500]

        existing = g.db.execute("SELECT id FROM note_customization_preferences WHERE syllabus_id = ?", (syllabus_id,)).fetchone()
        if existing:
            g.db.execute(
                """
                UPDATE note_customization_preferences
                SET exam_oriented = ?, code_heavy = ?, eli5_analogies = ?, step_by_step = ?, custom_prompt_text = ?
                WHERE syllabus_id = ?
                """,
                (exam_oriented, code_heavy, eli5_analogies, step_by_step, custom_prompt_text, syllabus_id)
            )
        else:
            g.db.execute(
                """
                INSERT INTO note_customization_preferences 
                (user_id, syllabus_id, exam_oriented, code_heavy, eli5_analogies, step_by_step, custom_prompt_text)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (session["user_id"], syllabus_id, exam_oriented, code_heavy, eli5_analogies, step_by_step, custom_prompt_text)
            )
        g.db.commit()
        return jsonify({"ok": True})

    @app.get("/api/topics/<id>/cards")
    @login_required
    def api_get_topic_cards(id: str):
        try:
            parts = id.split("|")
            syllabus_id, unit_idx, topic_idx = parts[0], int(parts[1]), int(parts[2])
            row = g.db.execute("SELECT notes_json FROM syllabi WHERE id = ? AND user_id = ?", (syllabus_id, session["user_id"])).fetchone()
            if not row: return jsonify({"ok": False, "error": "Not found"}), 404
            notes = _load_syllabus_json(row, "notes_json")
            topic = notes["units"][unit_idx]["topics"][topic_idx]
            chunks = topic.get("chunks", [])
            for c_idx, c in enumerate(chunks):
                c["chunk_id"] = f"{id}|{c_idx}"
            return jsonify({"ok": True, "cards": chunks})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.post("/api/topics/<id>/complete")
    @login_required
    def api_complete_topic(id: str):
        exists = g.db.execute("SELECT id FROM topic_progress WHERE user_id = ? AND topic_id = ?", (session["user_id"], id)).fetchone()
        if not exists:
            g.db.execute(
                "INSERT INTO topic_progress (user_id, topic_id, status, exp_awarded) VALUES (?, ?, 'completed', 20)",
                (session["user_id"], id)
            )
            g.db.execute(
                "INSERT INTO user_stats (user_id, total_exp, current_level, current_streak) VALUES (?, 20, 1, 0) ON CONFLICT(user_id) DO UPDATE SET total_exp = total_exp + 20, current_level = (total_exp + 20) / 100 + 1",
                (session["user_id"],)
            )
            g.db.commit()
            return jsonify({"ok": True, "exp_awarded": 20})
        return jsonify({"ok": True, "exp_awarded": 0})

    @app.get("/api/syllabus/<syllabus_id>/map")
    @login_required
    def api_syllabus_map(syllabus_id: str):
        row = g.db.execute("SELECT notes_json FROM syllabi WHERE id = ? AND user_id = ?", (syllabus_id, session["user_id"])).fetchone()
        if not row: return jsonify({"ok": False, "error": "Not found"}), 404
        notes = _load_syllabus_json(row, "notes_json")
        units = notes.get("units", [])
        
        progress_rows = g.db.execute("SELECT topic_id, status FROM topic_progress WHERE user_id = ?", (session["user_id"],)).fetchall()
        completed_topics = {pr["topic_id"] for pr in progress_rows if pr["status"] == "completed"}
        
        map_data = []
        is_unlocked = True
        for u_idx, u in enumerate(units):
            world = {
                "unit_index": u_idx,
                "title": u.get("unit_name", f"Unit {u_idx+1}"),
                "theme": f"theme-{u_idx % 4}",
                "nodes": []
            }
            topics = u.get("topics", [])
            for t_idx, t in enumerate(topics):
                tid = f"{syllabus_id}|{u_idx}|{t_idx}"
                is_completed = tid in completed_topics
                is_boss = (t_idx == len(topics) - 1)
                
                status = "locked"
                if is_completed:
                    status = "completed"
                    is_unlocked = True
                elif is_unlocked:
                    status = "unlocked"
                    is_unlocked = False
                
                world["nodes"].append({
                    "topic_index": t_idx,
                    "id": tid,
                    "title": t.get("topic_name", t.get("title", f"Topic {t_idx+1}")),
                    "status": status,
                    "is_boss": is_boss
                })
            map_data.append(world)
            
        return jsonify({"ok": True, "map": map_data})

    @app.post("/api/cards/<path:chunk_id>/signal")
    @login_required
    def api_card_signal(chunk_id: str):
        data = request.get_json(force=True) if request.is_json else {}
        signal = data.get("signal", "got_it")
        g.db.execute(
            "INSERT INTO card_review_signals (user_id, chunk_id, signal) VALUES (?, ?, ?)",
            (session["user_id"], chunk_id, signal)
        )
        g.db.commit()
        return jsonify({"ok": True})

    @app.post("/api/units/<id>/boss-fight/start")
    @login_required
    def api_boss_fight_start(id: str):
        parts = id.split("|")
        syllabus_id, unit_idx = parts[0], int(parts[1])
        row = g.db.execute("SELECT practice_json FROM syllabi WHERE id = ? AND user_id = ?", (syllabus_id, session["user_id"])).fetchone()
        if not row: return jsonify({"ok": False, "error": "Not found"}), 404
        prac = _load_syllabus_json(row, "practice_json")
        unit = prac.get("units", [])[unit_idx] if len(prac.get("units", [])) > unit_idx else {}
        questions = unit.get("questions", [])
        
        import random
        selected = random.sample(questions, min(5, len(questions)))
        
        cursor = g.db.cursor()
        cursor.execute(
            """INSERT INTO boss_fight_attempts (user_id, unit_id, hp, result, questions_json)
               VALUES (?, ?, 3, 'in_progress', ?)""",
            (session["user_id"], id, json.dumps(selected))
        )
        attempt_id = cursor.lastrowid
        g.db.commit()
        
        client_questions = []
        for q in selected:
            cq = q.copy()
            if "correct_answer" in cq: del cq["correct_answer"]
            client_questions.append(cq)
            
        return jsonify({"ok": True, "attempt_id": attempt_id, "questions": client_questions, "hp": 3})

    @app.post("/api/units/<id>/boss-fight/answer")
    @login_required
    def api_boss_fight_answer(id: str):
        data = request.get_json(force=True) if request.is_json else {}
        attempt_id = data.get("attempt_id")
        q_index = int(data.get("q_index", 0))
        answer = int(data.get("answer", -1))
        
        row = g.db.execute("SELECT hp, result, questions_json FROM boss_fight_attempts WHERE id = ? AND user_id = ?", (attempt_id, session["user_id"])).fetchone()
        if not row: return jsonify({"ok": False, "error": "Not found"}), 404
        if row["result"] != "in_progress":
            return jsonify({"ok": False, "error": f"Attempt already finished with result: {row['result']}"}), 400
            
        questions = json.loads(row["questions_json"])
        q = questions[q_index]
        correct = (answer == int(q["correct_answer"]))
        
        hp = row["hp"]
        result = "in_progress"
        is_completed = data.get("is_final_question", False)
        exp_awarded = 0
        
        if not correct:
            hp -= 1
            if hp <= 0:
                result = "defeat"
        elif is_completed and hp > 0:
            result = "victory"
            exp_awarded = 50
            g.db.execute(
                "INSERT INTO user_stats (user_id, total_exp, current_level, current_streak) VALUES (?, 50, 1, 0) ON CONFLICT(user_id) DO UPDATE SET total_exp = total_exp + 50, current_level = (total_exp + 50) / 100 + 1",
                (session["user_id"],)
            )
            parts = id.split("|")
            syllabus_id, unit_idx = parts[0], int(parts[1])
            row_syl = g.db.execute("SELECT notes_json FROM syllabi WHERE id = ? AND user_id = ?", (syllabus_id, session["user_id"])).fetchone()
            if row_syl:
                notes = _load_syllabus_json(row_syl, "notes_json")
                topics = notes.get("units", [])[unit_idx].get("topics", [])
                boss_topic_id = f"{syllabus_id}|{unit_idx}|{len(topics)-1}"
                g.db.execute("INSERT OR IGNORE INTO topic_progress (user_id, topic_id, status, exp_awarded) VALUES (?, ?, 'completed', 50)", (session["user_id"], boss_topic_id))
            
        g.db.execute("UPDATE boss_fight_attempts SET hp = ?, result = ? WHERE id = ?", (hp, result, attempt_id))
        g.db.commit()
        
        return jsonify({"ok": True, "correct": correct, "hp": hp, "result": result, "correct_answer": q["correct_answer"] if not correct else None, "exp_awarded": exp_awarded})
        
    @app.post("/api/units/<id>/boss-fight/retry")
    @login_required
    def api_boss_fight_retry(id: str):
        last = g.db.execute("SELECT questions_json FROM boss_fight_attempts WHERE unit_id = ? AND user_id = ? ORDER BY id DESC LIMIT 1", (id, session["user_id"])).fetchone()
        exclude_questions = []
        if last:
            old_qs = json.loads(last["questions_json"])
            exclude_questions = [q.get("question") for q in old_qs]
            
        parts = id.split("|")
        syllabus_id, unit_idx = parts[0], int(parts[1])
        row = g.db.execute("SELECT practice_json FROM syllabi WHERE id = ? AND user_id = ?", (syllabus_id, session["user_id"])).fetchone()
        if not row: return jsonify({"ok": False, "error": "Not found"}), 404
        prac = _load_syllabus_json(row, "practice_json")
        unit = prac.get("units", [])[unit_idx] if len(prac.get("units", [])) > unit_idx else {}
        all_qs = unit.get("questions", [])
        
        filtered = [q for q in all_qs if q.get("question") not in exclude_questions]
        if len(filtered) < 3:
            filtered = all_qs
            
        import random
        selected = random.sample(filtered, min(5, len(filtered)))
        
        cursor = g.db.cursor()
        cursor.execute(
            """INSERT INTO boss_fight_attempts (user_id, unit_id, hp, result, questions_json)
               VALUES (?, ?, 3, 'in_progress', ?)""",
            (session["user_id"], id, json.dumps(selected))
        )
        attempt_id = cursor.lastrowid
        g.db.commit()
        
        client_questions = []
        for q in selected:
            cq = q.copy()
            if "correct_answer" in cq: del cq["correct_answer"]
            client_questions.append(cq)
            
        return jsonify({"ok": True, "attempt_id": attempt_id, "questions": client_questions, "hp": 3})

    @app.post("/api/topics/<id>/mastery/generate")
    @login_required
    def api_mastery_generate(id: str):
        parts = id.split("|")
        syllabus_id, unit_idx, topic_idx = parts[0], int(parts[1]), int(parts[2])
        row = g.db.execute("SELECT notes_json FROM syllabi WHERE id = ? AND user_id = ?", (syllabus_id, session["user_id"])).fetchone()
        if not row: return jsonify({"ok": False, "error": "Not found"}), 404
        
        notes = _load_syllabus_json(row, "notes_json")
        try:
            topic = notes["units"][unit_idx]["topics"][topic_idx]
            chunks = topic.get("chunks", [])
            topic_text = "\\n\\n".join([f"{c.get('title', '')}\\n{c.get('content', '')}" for c in chunks])
        except (KeyError, IndexError):
            return jsonify({"ok": False, "error": "Topic not found"}), 404
            
        if not topic_text.strip():
            return jsonify({"ok": False, "error": "No content to generate questions from"}), 400
            
        data = request.get_json(force=True) if request.is_json else {}
        previous_questions = data.get("previous_questions", [])
        
        try:
            questions = generate_mastery_mcqs(topic_text, previous_questions)
            return jsonify({"ok": True, "questions": questions})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.post("/api/topics/<id>/mastery/answer")
    @login_required
    def api_mastery_answer(id: str):
        data = request.get_json(force=True) if request.is_json else {}
        is_correct = data.get("correct", False)
        
        cursor = g.db.cursor()
        row = cursor.execute("SELECT current_streak FROM topic_progress WHERE user_id = ? AND topic_id = ?", (session["user_id"], id)).fetchone()
        
        streak = 0
        if row:
            streak = row["current_streak"]
            
        if is_correct:
            streak += 1
        else:
            streak = 0
            
        if row:
            cursor.execute("UPDATE topic_progress SET current_streak = ? WHERE user_id = ? AND topic_id = ?", (streak, session["user_id"], id))
        else:
            cursor.execute("INSERT INTO topic_progress (user_id, topic_id, status, exp_awarded, current_streak) VALUES (?, ?, 'locked', 0, ?)", (session["user_id"], id, streak))
            
        mastered = False
        if streak >= 5:
            mastered = True
            cursor.execute("UPDATE topic_progress SET status = 'completed' WHERE user_id = ? AND topic_id = ?", (session["user_id"], id))
            # Award some EXP for mastery if not already completed? We can leave EXP out or give a small bonus.
            # Let's just grant mastery status.
            
        g.db.commit()
        return jsonify({"ok": True, "streak": streak, "mastered": mastered})

    @app.get("/coming-soon")
    @login_required
    def coming_soon():
        return render_template("coming_soon.html")

    @app.get("/dashboard")
    @login_required
    def dashboard():
        syllabi = g.db.execute(
            "SELECT id, filename, course_name, class_name, language, created_at FROM syllabi WHERE user_id = ? ORDER BY created_at DESC",
            (session["user_id"],),
        ).fetchall()
        return render_template("dashboard.html", syllabi=syllabi)

    @app.get("/syllabus/<syllabus_id>")
    @login_required
    def syllabus_page(syllabus_id: str):
        row = g.db.execute(
            "SELECT id, filename, course_name, class_name, language, created_at FROM syllabi WHERE id = ? AND user_id = ?",
            (syllabus_id, session["user_id"]),
        ).fetchone()
        if row is None:
            return render_template("404.html"), 404

        return render_template("syllabus.html", syllabus=row)

    @app.get("/syllabus/<syllabus_id>/podcast")
    @login_required
    def podcast_page(syllabus_id: str):
        row = g.db.execute("SELECT * FROM syllabi WHERE id = ? AND user_id = ?", (syllabus_id, session["user_id"])).fetchone()
        if row is None:
            return render_template("404.html"), 404

        return render_template("podcast.html", syllabus=row)

    @app.get("/api/syllabi")
    @login_required
    def api_syllabi_list():
        syllabi = g.db.execute(
            "SELECT id, filename, course_name, class_name, language, created_at FROM syllabi WHERE user_id = ? ORDER BY created_at DESC",
            (session["user_id"],),
        ).fetchall()
        return jsonify(
            {
                "ok": True,
                "syllabi": [
                    {
                        "id": row["id"],
                        "filename": row["filename"],
                        "course_name": row["course_name"],
                        "class_name": row["class_name"],
                        "language": row["language"],
                        "created_at": row["created_at"],
                    }
                    for row in syllabi
                ],
            }
        )

    @app.get("/api/syllabus/<syllabus_id>/meta")
    @login_required
    def api_syllabus_meta(syllabus_id: str):
        row = g.db.execute(
            "SELECT id, filename, course_name, class_name, language, created_at FROM syllabi WHERE id = ? AND user_id = ?",
            (syllabus_id, session["user_id"]),
        ).fetchone()
        if row is None:
            return jsonify({"ok": False, "error": "Not found"}), 404
        return jsonify(
            {
                "ok": True,
                "meta": {
                    "id": row["id"],
                    "filename": row["filename"],
                    "course_name": row["course_name"],
                    "class_name": row["class_name"],
                    "language": row["language"],
                    "created_at": row["created_at"],
                },
            }
        )

    def _load_syllabus_json(row, key: str):
        if row is None:
            return {}
        try:
            raw = row[key]
        except (IndexError, KeyError, TypeError):
            raw = None

        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    @app.get("/api/syllabus/<syllabus_id>/units")
    @login_required
    def api_syllabus_units(syllabus_id: str):
        row = g.db.execute("SELECT raw_text, meta_json FROM syllabi WHERE id = ? AND user_id = ?", (syllabus_id, session["user_id"])).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "Not found"}), 404

        meta = {}
        if row["meta_json"]:
            try: meta = json.loads(row["meta_json"])
            except Exception: pass

        if "unit_list" in meta and meta["unit_list"]:
            return jsonify({"ok": True, "units": meta["unit_list"]})

        units = get_units_list(row["raw_text"] or "")
        meta["unit_list"] = units
        g.db.execute("UPDATE syllabi SET meta_json = ? WHERE id = ?", (json.dumps(meta), syllabus_id))
        g.db.commit()
        return jsonify({"ok": True, "units": units})

    @app.post("/api/syllabus/<syllabus_id>/generate_unit")
    @login_required
    def api_generate_unit(syllabus_id: str):
        row = g.db.execute("SELECT * FROM syllabi WHERE id = ? AND user_id = ?", (syllabus_id, session["user_id"])).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "Not found"}), 404

        has_api_key = bool(os.environ.get("GEMINI_API_KEY"))
        if g.user['role'] != 'admin' and has_api_key:
            if g.user['credits'] < 1:
                return jsonify({"ok": False, "error": "Insufficient credits. Please contact an admin to top up."}), 402
            g.db.execute("UPDATE users SET credits = credits - 1 WHERE id = ?", (session['user_id'],))
            g.db.commit()

        data = request.get_json(force=True) if request.is_json else {}
        unit_name = data.get("unit_name")
        if not unit_name:
            return jsonify({"ok": False, "error": "unit_name required"}), 400

        course_name = row["course_name"]
        class_name = row["class_name"]
        language = row["language"]
        raw_text = row["raw_text"] or ""
        user_id = session["user_id"]

        def generate_stream():
            yield json.dumps({"status": "start", "unit": unit_name}) + "\n"
            try:
                conn = sqlite3.connect(DATABASE_PATH)
                conn.row_factory = sqlite3.Row

                # 1. Notes
                yield json.dumps({"status": "progress", "message": "Generating notes...", "progress": 10}) + "\n"
                
                prefs = conn.execute("SELECT * FROM note_customization_preferences WHERE syllabus_id = ?", (syllabus_id,)).fetchone()
                custom_prefs = dict(prefs) if prefs else None
                
                notes_generated = generate_unit_notes(raw_text, course_name, class_name, language, [unit_name], custom_prefs)
                current_notes = _load_syllabus_json(conn.execute("SELECT notes_json FROM syllabi WHERE id = ?", (syllabus_id,)).fetchone(), "notes_json")
                if "units" not in current_notes: current_notes["units"] = []

                if "units" in notes_generated and notes_generated["units"]:
                    new_u = notes_generated["units"][0]
                    replaced = False
                    for idx, ext_u in enumerate(current_notes["units"]):
                        if ext_u.get("unit_name") == new_u.get("unit_name"):
                            current_notes["units"][idx] = new_u
                            replaced = True; break
                    if not replaced: current_notes["units"].append(new_u)
                conn.execute("UPDATE syllabi SET notes_json = ? WHERE id = ?", (json.dumps(current_notes), syllabus_id))
                conn.commit()

                # 2. Practice
                yield json.dumps({"status": "progress", "message": "Generating MCQs...", "progress": 40}) + "\n"
                prac_generated = generate_mcq_questions(raw_text, language, [unit_name])
                current_prac = _load_syllabus_json(conn.execute("SELECT practice_json FROM syllabi WHERE id = ?", (syllabus_id,)).fetchone(), "practice_json")
                if "units" not in current_prac: current_prac["units"] = []
                if "units" in prac_generated and prac_generated["units"]:
                    new_u = prac_generated["units"][0]
                    replaced = False
                    for idx, ext_u in enumerate(current_prac["units"]):
                        if ext_u.get("unit_name") == new_u.get("unit_name"):
                            current_prac["units"][idx] = new_u
                            replaced = True; break
                    if not replaced: current_prac["units"].append(new_u)
                conn.execute("UPDATE syllabi SET practice_json = ? WHERE id = ?", (json.dumps(current_prac), syllabus_id))
                conn.commit()

                # 3. Study Pack
                yield json.dumps({"status": "progress", "message": "Generating Study Pack...", "progress": 70}) + "\n"
                study_json = _load_syllabus_json(conn.execute("SELECT study_json FROM syllabi WHERE id = ?", (syllabus_id,)).fetchone(), "study_json")
                if "flashcards" not in study_json: study_json["flashcards"] = {"units": []}
                if "units" not in study_json["flashcards"]: study_json["flashcards"]["units"] = []
                if "study_units" not in study_json: study_json["study_units"] = []

                gen_flash = generate_flashcards(raw_text, language, [unit_name])
                if "flashcards" in gen_flash and "units" in gen_flash["flashcards"] and gen_flash["flashcards"]["units"]:
                    new_flash = gen_flash["flashcards"]["units"][0]
                    replaced = False
                    for idx, ext_u in enumerate(study_json["flashcards"]["units"]):
                        if ext_u.get("unit_name") == new_flash.get("unit_name"):
                            study_json["flashcards"]["units"][idx] = new_flash
                            replaced = True; break
                    if not replaced: study_json["flashcards"]["units"].append(new_flash)

                gen_mixed = generate_mixed_questions(raw_text, language, [unit_name])
                if "study_units" in gen_mixed and gen_mixed["study_units"]:
                    new_mixed = gen_mixed["study_units"][0]
                    replaced = False
                    for idx, ext_u in enumerate(study_json["study_units"]):
                        if ext_u.get("unit_name") == new_mixed.get("unit_name"):
                            study_json["study_units"][idx] = new_mixed
                            replaced = True; break
                    if not replaced: study_json["study_units"].append(new_mixed)

                conn.execute("UPDATE syllabi SET study_json = ? WHERE id = ?", (json.dumps(study_json), syllabus_id))
                conn.commit()
                conn.close()
                yield json.dumps({"status": "done"}) + "\n"
            except Exception as e:
                yield json.dumps({"status": "error", "message": str(e)}) + "\n"

        return Response(generate_stream(), mimetype='application/jsonl')

    @app.post("/api/syllabus/<syllabus_id>/generate/notes")
    @login_required
    def api_generate_notes(syllabus_id: str):
        row = g.db.execute("SELECT * FROM syllabi WHERE id = ? AND user_id = ?", (syllabus_id, session["user_id"])).fetchone()
        if row is None:
            return jsonify({"ok": False, "error": "Not found"}), 404

        # Credit System Check
        has_api_key = bool(os.environ.get("GEMINI_API_KEY"))
        if g.user['role'] != 'admin' and has_api_key:
            if g.user['credits'] < 1:
                return jsonify({"ok": False, "error": "Insufficient credits. Please contact an admin to top up."}), 402 # 402 Payment Required

            # Deduct credit
            g.db.execute("UPDATE users SET credits = credits - 1 WHERE id = ?", (session['user_id'],))
            g.db.commit()

        data = request.get_json(force=True) if request.is_json else {}
        units = data.get("units")  # optional client override

        course_name = row["course_name"]
        class_name = row["class_name"]
        language = row["language"]
        raw_text = row["raw_text"] or ""
        user_id = session["user_id"]

        unit_list = units if isinstance(units, list) and units else get_units_list(raw_text)
        is_partial = bool(units)
        unit_list = units if is_partial else get_units_list(raw_text)

        def generate_stream():
            yield json.dumps({"status": "start", "total": len(unit_list)}) + "\n"
            notes_json = {"units": []}

            try:
                conn = sqlite3.connect(DATABASE_PATH)
                conn.row_factory = sqlite3.Row
                current_row = conn.execute("SELECT notes_json FROM syllabi WHERE id = ? AND user_id = ?", (syllabus_id, user_id)).fetchone()
                notes_json = _load_syllabus_json(current_row, "notes_json") if is_partial else {"units": []}
                if "units" not in notes_json: notes_json["units"] = []

                prefs = conn.execute("SELECT * FROM note_customization_preferences WHERE syllabus_id = ?", (syllabus_id,)).fetchone()
                custom_prefs = dict(prefs) if prefs else None

                for i, u in enumerate(unit_list):
                    yield json.dumps({"status": "progress", "message": f"Generating notes for {u}...", "current": i+1, "total": len(unit_list)}) + "\n"
                    generated = generate_unit_notes(
                        syllabus_text=raw_text, course_name=course_name,
                        class_name=class_name, language=language, unit_list=[u], custom_prefs=custom_prefs
                    )
                    if "units" in generated and generated["units"]:
                        new_unit = generated["units"][0]
                        replaced = False
                        for idx, ext_u in enumerate(notes_json["units"]):
                            if ext_u.get("unit_name") == new_unit.get("unit_name"):
                                notes_json["units"][idx] = new_unit
                                replaced = True
                                break
                        if not replaced:
                            notes_json["units"].append(new_unit)

                yield json.dumps({"status": "progress", "message": "Saving notes...", "current": len(unit_list), "total": len(unit_list)}) + "\n"
                conn = sqlite3.connect(DATABASE_PATH)
                conn.execute("UPDATE syllabi SET notes_json = ? WHERE id = ? AND user_id = ?", (json.dumps(notes_json), syllabus_id, user_id))
                conn.commit()
                conn.close()

                yield json.dumps({"status": "done", "notes": notes_json}) + "\n"
            except Exception as e:
                yield json.dumps({"status": "error", "message": str(e)}) + "\n"

        return Response(generate_stream(), mimetype='application/jsonl')

    @app.post("/api/syllabus/<syllabus_id>/generate/practice")
    @login_required
    def api_generate_practice(syllabus_id: str):
        row = g.db.execute("SELECT * FROM syllabi WHERE id = ? AND user_id = ?", (syllabus_id, session["user_id"])).fetchone()
        if row is None:
            return jsonify({"ok": False, "error": "Not found"}), 404

        # Credit System Check
        has_api_key = bool(os.environ.get("GEMINI_API_KEY"))
        if g.user['role'] != 'admin' and has_api_key:
            if g.user['credits'] < 1:
                return jsonify({"ok": False, "error": "Insufficient credits. Please contact an admin to top up."}), 402

            # Deduct credit
            g.db.execute("UPDATE users SET credits = credits - 1 WHERE id = ?", (session['user_id'],))
            g.db.commit()

        data = request.get_json(force=True) if request.is_json else {}
        language = row["language"]
        raw_text = row["raw_text"] or ""
        user_id = session["user_id"]

        is_partial = bool(data.get("unit_list"))
        unit_list = data.get("unit_list") or get_units_list(raw_text)

        def generate_stream():
            yield json.dumps({"status": "start", "total": len(unit_list)}) + "\n"
            practice_json = {"units": []}
            try:
                conn = sqlite3.connect(DATABASE_PATH)
                conn.row_factory = sqlite3.Row
                current_row = conn.execute("SELECT practice_json FROM syllabi WHERE id = ? AND user_id = ?", (syllabus_id, user_id)).fetchone()
                practice_json = _load_syllabus_json(current_row, "practice_json") if is_partial else {"units": []}
                if "units" not in practice_json: practice_json["units"] = []

                for i, u in enumerate(unit_list):
                    yield json.dumps({"status": "progress", "message": f"Generating practice MCQs for {u}...", "current": i+1, "total": len(unit_list)}) + "\n"
                    generated = generate_mcq_questions(syllabus_text=raw_text, language=language, unit_list=[u])
                    if "units" in generated and generated["units"]:
                        new_unit = generated["units"][0]
                        replaced = False
                        for idx, ext_u in enumerate(practice_json["units"]):
                            if ext_u.get("unit_name") == new_unit.get("unit_name"):
                                practice_json["units"][idx] = new_unit
                                replaced = True
                                break
                        if not replaced:
                            practice_json["units"].append(new_unit)

                conn = sqlite3.connect(DATABASE_PATH)
                conn.execute("UPDATE syllabi SET practice_json = ? WHERE id = ? AND user_id = ?", (json.dumps(practice_json), syllabus_id, user_id))
                conn.commit()
                conn.close()
                yield json.dumps({"status": "done"}) + "\n"
            except Exception as e:
                yield json.dumps({"status": "error", "message": str(e)}) + "\n"

        return Response(generate_stream(), mimetype='application/jsonl')

    @app.post("/api/syllabus/<syllabus_id>/generate/study")
    @login_required
    def api_generate_study(syllabus_id: str):
        row = g.db.execute("SELECT * FROM syllabi WHERE id = ? AND user_id = ?", (syllabus_id, session["user_id"])).fetchone()
        if row is None:
            return jsonify({"ok": False, "error": "Not found"}), 404

        # Credit System Check
        has_api_key = bool(os.environ.get("GEMINI_API_KEY"))
        if g.user['role'] != 'admin' and has_api_key:
            if g.user['credits'] < 1:
                return jsonify({"ok": False, "error": "Insufficient credits. Please contact an admin to top up."}), 402

            # Deduct credit
            g.db.execute("UPDATE users SET credits = credits - 1 WHERE id = ?", (session['user_id'],))
            g.db.commit()

        data = request.get_json(force=True) if request.is_json else {}
        language = row["language"]
        raw_text = row["raw_text"] or ""
        user_id = session["user_id"]

        is_partial = bool(data.get("unit_list"))
        unit_list = data.get("unit_list") or get_units_list(raw_text)

        def generate_stream():
            yield json.dumps({"status": "start", "total": len(unit_list)}) + "\n"
            flashcards_units = []
            mixed_units = []

            try:
                conn = sqlite3.connect(DATABASE_PATH)
                conn.row_factory = sqlite3.Row
                current_row = conn.execute("SELECT study_json FROM syllabi WHERE id = ? AND user_id = ?", (syllabus_id, user_id)).fetchone()
                study_json = _load_syllabus_json(current_row, "study_json") if is_partial else {"flashcards": {"units": []}, "study_units": []}

                if "flashcards" not in study_json: study_json["flashcards"] = {"units": []}
                if "units" not in study_json["flashcards"]: study_json["flashcards"]["units"] = []
                if "study_units" not in study_json: study_json["study_units"] = []

                for i, u in enumerate(unit_list):
                    yield json.dumps({"status": "progress", "message": f"Generating study material for {u}...", "current": i+1, "total": len(unit_list)}) + "\n"

                    gen_flash = generate_flashcards(syllabus_text=raw_text, language=language, unit_list=[u])
                    if "flashcards" in gen_flash and "units" in gen_flash["flashcards"] and gen_flash["flashcards"]["units"]:
                        new_flash = gen_flash["flashcards"]["units"][0]
                        replaced = False
                        for idx, ext_u in enumerate(study_json["flashcards"]["units"]):
                            if ext_u.get("unit_name") == new_flash.get("unit_name"):
                                study_json["flashcards"]["units"][idx] = new_flash
                                replaced = True
                                break
                        if not replaced:
                            study_json["flashcards"]["units"].append(new_flash)

                    gen_mixed = generate_mixed_questions(syllabus_text=raw_text, language=language, unit_list=[u])
                    if "study_units" in gen_mixed and gen_mixed["study_units"]:
                        new_mixed = gen_mixed["study_units"][0]
                        replaced = False
                        for idx, ext_u in enumerate(study_json["study_units"]):
                            if ext_u.get("unit_name") == new_mixed.get("unit_name"):
                                study_json["study_units"][idx] = new_mixed
                                replaced = True
                                break
                        if not replaced:
                            study_json["study_units"].append(new_mixed)

                conn = sqlite3.connect(DATABASE_PATH)
                conn.execute("UPDATE syllabi SET study_json = ? WHERE id = ? AND user_id = ?", (json.dumps(study_json), syllabus_id, user_id))
                conn.commit()
                conn.close()
                yield json.dumps({"status": "done"}) + "\n"
            except Exception as e:
                yield json.dumps({"status": "error", "message": str(e)}) + "\n"

        return Response(generate_stream(), mimetype='application/jsonl')

    @app.get("/api/syllabus/<syllabus_id>/data")
    @login_required
    def api_syllabus_data(syllabus_id: str):
        row = g.db.execute("SELECT * FROM syllabi WHERE id = ? AND user_id = ?", (syllabus_id, session["user_id"])).fetchone()
        if row is None:
            return jsonify({"ok": False, "error": "Not found"}), 404

        notes_json = _load_syllabus_json(row, "notes_json")
        
        # Phase 1 adapter logic: convert chunked data back into legacy notes format, and vice-versa
        modified_legacy = False
        if notes_json and "units" in notes_json:
            for u_idx, u in enumerate(notes_json["units"]):
                if "notes" not in u:
                    u["notes"] = f"{u.get('unit_name', 'Unit')}: Notes"
                if "topics" in u:
                    for t_idx, t in enumerate(u["topics"]):
                        if "chunks" in t:
                            md_lines = []
                            for chunk in t["chunks"]:
                                if chunk.get("title"):
                                    md_lines.append(f"### {chunk['title']}")
                                if chunk.get("content"):
                                    md_lines.append(chunk["content"])
                            t["topic_notes"] = "\n\n".join(md_lines)
                            t["topic_name"] = t.get("title", t.get("topic_name", "Topic"))
                        elif "topic_notes" in t:
                            # Legacy fallback: convert flat topic_notes into chunks
                            parts = [p.strip() for p in str(t["topic_notes"]).split("\n\n") if p.strip()]
                            t["chunks"] = [{"type": "INFO", "title": t.get("topic_name", "Notes"), "content": p} for p in parts]
                            t["title"] = t.get("topic_name", "Topic")
                            modified_legacy = True
                            
                            # Also auto-complete them in topic_progress just in case (safe backfill)
                            tid = f"{syllabus_id}|{u_idx}|{t_idx}"
                            g.db.execute("INSERT OR IGNORE INTO topic_progress (user_id, topic_id, status, exp_awarded) VALUES (?, ?, 'completed', 0)", (session["user_id"], tid))
                            
        if modified_legacy:
            g.db.execute("UPDATE syllabi SET notes_json = ? WHERE id = ? AND user_id = ?", (json.dumps(notes_json), syllabus_id, session["user_id"]))
            g.db.commit()

        practice_json = _load_syllabus_json(row, "practice_json")
        study_json = _load_syllabus_json(row, "study_json")
        extra_notes_json = _load_syllabus_json(row, "extra_notes_json")
        podcast_json = _load_syllabus_json(row, "podcast_json")

        materials = g.db.execute("SELECT * FROM extra_materials WHERE syllabus_id = ? ORDER BY created_at ASC", (syllabus_id,)).fetchall()
        extra_materials = [
            {
                "id": m["id"],
                "material_type": m["material_type"],
                "file_path": m["file_path"],
                "original_name": m["original_name"],
                "text_content": m["text_content"],
                "url": f"/uploads/{os.path.basename(m['file_path'])}" if m["file_path"] else None
            } for m in materials
        ]

        reviews = g.db.execute("SELECT * FROM flashcard_reviews WHERE user_id = ? AND syllabus_id = ?", (session["user_id"], syllabus_id)).fetchall()
        flashcard_reviews = { f"{r['unit_name']}_{r['front_text']}": r["next_review_date"] for r in reviews }

        return jsonify(
            {
                "ok": True,
                "notes": notes_json,
                "practice": practice_json,
                "study": study_json,
                "extra_notes": extra_notes_json,
                "extra_materials": extra_materials,
                "cheat_sheet": row["cheat_sheet"] if "cheat_sheet" in row.keys() else None,
                "flashcard_reviews": flashcard_reviews,
                "podcast": podcast_json
            }
        )

    @app.post("/api/syllabus/<syllabus_id>/update_json")
    @login_required
    def api_update_json(syllabus_id: str):
        payload = request.get_json()
        field = payload.get("field")
        data = payload.get("data")
        if field not in ["notes_json", "practice_json", "study_json", "extra_notes_json", "podcast_json"]:
            return jsonify({"ok": False, "error": "Invalid field"}), 400

        g.db.execute(f"UPDATE syllabi SET {field} = ? WHERE id = ? AND user_id = ?", (json.dumps(data), syllabus_id, session["user_id"]))
        g.db.commit()
        return jsonify({"ok": True})

    @app.post("/api/evaluate_written")
    @login_required
    def api_evaluate_written():
        payload = request.get_json()
        from services.gemini_client import evaluate_written_answer
        try:
            res = evaluate_written_answer(payload.get("question", ""), payload.get("actual_answer", ""), payload.get("user_answer", ""))
            return jsonify({"ok": True, "evaluation": res})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.route("/uploads/<filename>")
    @login_required
    def uploaded_file(filename):
        return send_from_directory(UPLOAD_DIR, filename)

    @app.post("/api/syllabus/<syllabus_id>/extra/upload")
    @login_required
    def api_extra_upload(syllabus_id: str):
        text_content = request.form.get("text_content", "").strip()
        file_obj = request.files.get("file")

        material_type = "text"
        file_path = None
        original_name = None
        extracted_text = text_content
        mat_id = str(uuid.uuid4())

        if file_obj and file_obj.filename:
            original_name = secure_filename(file_obj.filename)
            file_path = os.path.join(UPLOAD_DIR, f"extra_{mat_id}_{original_name}")
            file_obj.save(file_path)

            ext = original_name.lower().split('.')[-1]
            if ext == "pdf":
                material_type = "pdf"
                try:
                    extracted_text += "\n" + extract_pdf_text(file_path)
                except Exception:
                    pass
            elif ext == "docx":
                material_type = "docx"
                try:
                    extracted_text += "\n" + extract_docx_text(file_path)
                except Exception:
                    pass
            elif ext in ["png", "jpg", "jpeg", "webp"]:
                material_type = "image"

        if not file_path and not extracted_text:
            return jsonify({"ok": False, "error": "Empty submission"}), 400

        g.db.execute(
            "INSERT INTO extra_materials (id, syllabus_id, material_type, file_path, original_name, text_content, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (mat_id, syllabus_id, material_type, file_path, original_name, extracted_text, datetime.utcnow().isoformat())
        )
        g.db.commit()
        return jsonify({"ok": True})

    @app.post("/api/syllabus/<syllabus_id>/extra/generate")
    @login_required
    def api_extra_generate(syllabus_id: str):
        row = g.db.execute("SELECT * FROM syllabi WHERE id = ? AND user_id = ?", (syllabus_id, session["user_id"])).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "Not found"}), 404

        materials = g.db.execute("SELECT * FROM extra_materials WHERE syllabus_id = ?", (syllabus_id,)).fetchall()

        # Fixed half-credit cost for extra notes generation as requested
        cost = 0.5

        # Credit System Check
        has_api_key = bool(os.environ.get("GEMINI_API_KEY"))
        if g.user['role'] != 'admin' and has_api_key:
            if g.user['credits'] < cost:
                return jsonify({"ok": False, "error": f"Insufficient credits. Processing extra materials costs {cost} credits."}), 402

        text_contents = [m["text_content"] for m in materials if m["text_content"]]
        image_paths = [m["file_path"] for m in materials if m["material_type"] == "image" and m["file_path"]]

        try:
            notes = generate_extra_notes(row["course_name"], row["class_name"], row["language"], text_contents, image_paths)

            if "error" in notes:
                return jsonify({"ok": False, "error": notes["error"]}), 400

            # Deduct credit ONLY after a successful generation
            if g.user['role'] != 'admin' and has_api_key:
                g.db.execute("UPDATE users SET credits = credits - ? WHERE id = ?", (cost, session['user_id']))

            g.db.execute("UPDATE syllabi SET extra_notes_json = ? WHERE id = ?", (json.dumps(notes), syllabus_id))
            g.db.commit()
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.post("/api/syllabus/<syllabus_id>/chat")
    @login_required
    def api_chat(syllabus_id: str):
        payload = request.get_json(silent=True) or {}
        unit_name = payload.get("unit_name", "General")
        query = payload.get("query", "")
        notes_context = payload.get("notes_context", "")

        row = g.db.execute("SELECT * FROM syllabi WHERE id = ? AND user_id = ?", (syllabus_id, session["user_id"])).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "Not found"}), 404

        subject_name = row["course_name"]
        user_id = session["user_id"]

        user_msg_id = str(uuid.uuid4())
        g.db.execute(
            "INSERT INTO chats (id, user_id, syllabus_id, unit_name, role, message, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_msg_id, session["user_id"], syllabus_id, unit_name, "user", query, datetime.utcnow().isoformat())
        )
        g.db.commit()

        history_rows = g.db.execute(
            "SELECT role, message FROM chats WHERE syllabus_id = ? AND unit_name = ? AND user_id = ? ORDER BY created_at ASC",
            (syllabus_id, unit_name, session["user_id"])
        ).fetchall()

        history = [{"role": r["role"], "message": r["message"]} for r in history_rows[:-1]]

        def generate_stream():
            try:
                from services.gemini_client import answer_chat_query
                response_text = answer_chat_query(notes_context, subject_name, query, history)

                ai_msg_id = str(uuid.uuid4())
                conn = sqlite3.connect(DATABASE_PATH)
                conn.execute(
                    "INSERT INTO chats (id, user_id, syllabus_id, unit_name, role, message, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (ai_msg_id, user_id, syllabus_id, unit_name, "model", response_text, datetime.utcnow().isoformat())
                )
                conn.commit()
                conn.close()

                yield json.dumps({"status": "done", "response": response_text}) + "\n"
            except Exception as e:
                yield json.dumps({"status": "error", "message": str(e)}) + "\n"

        return Response(generate_stream(), mimetype='application/jsonl')

    @app.get("/api/syllabus/<syllabus_id>/chat/<unit_name>")
    @login_required
    def api_get_chat(syllabus_id: str, unit_name: str):
        history_rows = g.db.execute(
            "SELECT role, message FROM chats WHERE syllabus_id = ? AND unit_name = ? AND user_id = ? ORDER BY created_at ASC",
            (syllabus_id, unit_name, session["user_id"])
        ).fetchall()
        return jsonify({"ok": True, "history": [{"role": r["role"], "message": r["message"]} for r in history_rows]})

    @app.post("/api/syllabus/<syllabus_id>/mcq/attempt")
    @login_required
    def api_mcq_attempt(syllabus_id: str):
        payload = request.get_json(silent=True) or {}
        unit_index = int(payload.get("unit_index", 0))
        topic_index = int(payload.get("topic_index", 0))
        question_count = int(payload.get("question_count", 0))
        score = int(payload.get("score", 0))
        time_seconds = int(payload.get("time_seconds", 0))

        g.db.execute(
            """
            INSERT INTO mcq_attempts (id, user_id, syllabus_id, unit_index, topic_index, question_count, score, time_seconds, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                session["user_id"],
                syllabus_id,
                unit_index,
                topic_index,
                question_count,
                score,
                time_seconds,
                datetime.utcnow().isoformat(),
            ),
        )
        g.db.commit()
        return jsonify({"ok": True})

    @app.get("/api/dashboard/usage/<syllabus_id>")
    @login_required
    def api_dashboard_usage(syllabus_id: str):
        # Return attempts summary (for per-topic mark display)
        rows = g.db.execute(
            """
            SELECT unit_index, topic_index, COUNT(*) as attempts, AVG(score) as avg_score, MAX(score) as best_score
            FROM mcq_attempts
            WHERE user_id = ? AND syllabus_id = ?
            GROUP BY unit_index, topic_index
            """,
            (session["user_id"], syllabus_id),
        ).fetchall()

        return jsonify(
            {
                "ok": True,
                "attempt_summary": [
                    {
                        "unit_index": r["unit_index"],
                        "topic_index": r["topic_index"],
                        "attempts": r["attempts"],
                        "avg_score": r["avg_score"],
                        "best_score": r["best_score"],
                    }
                    for r in rows
                ],
            }
        )

    @app.delete("/api/syllabus/<syllabus_id>")
    @login_required
    def api_delete_syllabus(syllabus_id: str):
        row = g.db.execute("SELECT * FROM syllabi WHERE id = ? AND user_id = ?", (syllabus_id, session["user_id"])).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "Not found"}), 404

        # Delete the associated PDF file
        if row["pdf_path"] and os.path.exists(row["pdf_path"]):
            try:
                os.remove(row["pdf_path"])
            except OSError:
                pass # Ignore file deletion errors

        # Delete associated extra material files
        extra_mats = g.db.execute("SELECT file_path FROM extra_materials WHERE syllabus_id = ?", (syllabus_id,)).fetchall()
        for mat in extra_mats:
            if mat["file_path"] and os.path.exists(mat["file_path"]):
                try:
                    os.remove(mat["file_path"])
                except OSError:
                    pass

        g.db.execute("DELETE FROM mcq_attempts WHERE syllabus_id = ?", (syllabus_id,))
        g.db.execute("DELETE FROM extra_materials WHERE syllabus_id = ?", (syllabus_id,))
        g.db.execute("DELETE FROM chats WHERE syllabus_id = ?", (syllabus_id,))
        g.db.execute("DELETE FROM flashcard_reviews WHERE syllabus_id = ?", (syllabus_id,))
        g.db.execute("DELETE FROM syllabi WHERE id = ?", (syllabus_id,))
        g.db.commit()
        return jsonify({"ok": True})

    @app.post("/api/syllabus/<syllabus_id>/flashcard/review")
    @login_required
    def api_flashcard_review(syllabus_id: str):
        payload = request.get_json(silent=True) or {}
        unit_name = payload.get("unit_name")
        front_text = payload.get("front_text")
        quality = payload.get("quality")

        row = g.db.execute("SELECT * FROM flashcard_reviews WHERE user_id = ? AND syllabus_id = ? AND unit_name = ? AND front_text = ?",
            (session["user_id"], syllabus_id, unit_name, front_text)).fetchone()

        ease_factor = 2.5
        interval = 0
        repetitions = 0
        if row:
            ease_factor = row["ease_factor"]
            interval = row["interval"]
            repetitions = row["repetitions"]

        if quality == "again":
            repetitions = 0
            interval = 1
            ease_factor = max(1.3, ease_factor - 0.2)
        elif quality == "hard":
            repetitions += 1
            interval = max(1, int(interval * 1.2))
            ease_factor = max(1.3, ease_factor - 0.15)
        elif quality == "good":
            repetitions += 1
            interval = max(1, int((interval if interval > 0 else 1) * ease_factor))
        elif quality == "easy":
            repetitions += 1
            interval = max(1, int((interval if interval > 0 else 1) * ease_factor * 1.3))
            ease_factor += 0.15

        next_review_date = (datetime.utcnow() + timedelta(days=interval)).isoformat()

        if row:
            g.db.execute("UPDATE flashcard_reviews SET next_review_date=?, ease_factor=?, interval=?, repetitions=? WHERE id=?",
                (next_review_date, ease_factor, interval, repetitions, row["id"]))
        else:
            g.db.execute("INSERT INTO flashcard_reviews (id, user_id, syllabus_id, unit_name, front_text, next_review_date, ease_factor, interval, repetitions, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), session["user_id"], syllabus_id, unit_name, front_text, next_review_date, ease_factor, interval, repetitions, datetime.utcnow().isoformat()))
        g.db.commit()
        return jsonify({"ok": True})

    @app.post("/api/explain_text")
    @login_required
    def api_explain_text():
        payload = request.get_json(silent=True) or {}
        text = payload.get("text", "")
        action = payload.get("action", "ELI5")
        context = payload.get("context", "")

        from services.gemini_client import explain_text
        try:
            explanation = explain_text(text, action, context)
            return jsonify({"ok": True, "explanation": explanation})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.post("/api/syllabus/<syllabus_id>/generate/summary")
    @login_required
    def api_generate_summary(syllabus_id: str):
        row = g.db.execute("SELECT * FROM syllabi WHERE id = ? AND user_id = ?", (syllabus_id, session["user_id"])).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "Not found"}), 404

        # Credit System Check
        has_api_key = bool(os.environ.get("GEMINI_API_KEY"))
        if g.user['role'] != 'admin' and has_api_key:
            if g.user['credits'] < 1:
                return jsonify({"ok": False, "error": "Insufficient credits. Please contact an admin to top up."}), 402

            # Deduct credit
            g.db.execute("UPDATE users SET credits = credits - 1 WHERE id = ?", (session['user_id'],))
            g.db.commit()

        notes_json = row["notes_json"]
        from services.gemini_client import generate_summary_sheet
        try:
            summary_md = generate_summary_sheet(notes_json)

            g.db.execute("UPDATE syllabi SET cheat_sheet = ? WHERE id = ? AND user_id = ?", (summary_md, syllabus_id, session["user_id"]))
            g.db.commit()

            return jsonify({"ok": True, "cheat_sheet": summary_md})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.post("/api/syllabus/<syllabus_id>/podcast/generate")
    @login_required
    def api_generate_podcast(syllabus_id: str):
        try:
            print(f"\n[Podcast Gen] Incoming request for syllabus: {syllabus_id}")
            row = g.db.execute("SELECT * FROM syllabi WHERE id = ? AND user_id = ?", (syllabus_id, session["user_id"])).fetchone()
            if not row:
                print("[Podcast Gen] Error: Syllabus not found in DB.")
                return jsonify({"ok": False, "error": "Not found"}), 404

            # Credit Check
            has_api_key = bool(os.environ.get("GEMINI_API_KEY"))
            if g.user['role'] != 'admin' and has_api_key:
                if g.user['credits'] < 1:
                    print(f"[Podcast Gen] Error: Insufficient credits. User has {g.user['credits']}.")
                    return jsonify({"ok": False, "error": "Insufficient credits. Generating a podcast costs 1 credit."}), 402

            data = request.get_json(silent=True) or {}
            unit_name = data.get("unit_name")
            if not unit_name:
                print(f"[Podcast Gen] Error: 'unit_name' was missing from the incoming JSON payload: {data}")
                return jsonify({"ok": False, "error": "unit_name required in JSON payload"}), 400

            # Extract unit notes context
            notes_json = _load_syllabus_json(row, "notes_json")
            unit_notes_text = ""
            available_units = []
            for u in notes_json.get("units", []):
                u_name = str(u.get("unit_name", ""))
                available_units.append(u_name)

                u_name_clean = u_name.strip().lower()
                req_name_clean = str(unit_name).strip().lower()

                if u_name_clean == req_name_clean or req_name_clean in u_name_clean or u_name_clean in req_name_clean:
                    n = u.get("notes")
                    if n and str(n).strip() != "None":
                        unit_notes_text += str(n) + "\n"
                    for t in u.get("topics", []):
                        tn = t.get("topic_notes")
                        if tn and str(tn).strip() != "None":
                            unit_notes_text += str(tn) + "\n"
                    break

            if not unit_notes_text.strip():
                print(f"[Podcast Gen] Error: Could not find any valid notes in the database for the unit '{unit_name}'.")
                print(f"[Podcast Gen] Available units in DB: {available_units}")
                return jsonify({"ok": False, "error": f"No notes found for unit '{unit_name}'. Generate notes first."}), 400

            # Deduct credit
            if g.user['role'] != 'admin' and has_api_key:
                g.db.execute("UPDATE users SET credits = credits - 1 WHERE id = ?", (session['user_id'],))

            print(f"[Podcast Gen] Calling Gemini AI to write script for '{unit_name}'...")
            script_data = generate_podcast_script(unit_name, unit_notes_text, row["course_name"])

            if "error" in script_data:
                print(f"[Podcast Gen] AI Generation Failed: {script_data['error']}")
                return jsonify({"ok": False, "error": script_data["error"]}), 400

            current_podcasts = _load_syllabus_json(row, "podcast_json")
            current_podcasts[unit_name] = script_data

            g.db.execute("UPDATE syllabi SET podcast_json = ? WHERE id = ?", (json.dumps(current_podcasts), syllabus_id))
            g.db.commit()
            print("[Podcast Gen] Success! Podcast script saved to DB.")
            return jsonify({"ok": True, "script": script_data["script"]})

        except Exception as e:
            import traceback
            print("\n[Podcast Gen] FATAL SERVER ERROR:")
            traceback.print_exc()
            return jsonify({"ok": False, "error": str(e)}), 400

    # --- ADMIN ROUTES ---
    @app.get("/admin")
    @login_required
    @admin_required
    def admin_dashboard():
        users = g.db.execute("SELECT id, username, credits, role, created_at FROM users ORDER BY created_at DESC").fetchall()
        return render_template("admin.html", users=users)

    @app.post("/api/admin/user/<user_id>/credits")
    @login_required
    @admin_required
    def admin_update_credits(user_id: str):
        payload = request.get_json(silent=True) or {}
        new_credits = payload.get("credits")

        if new_credits is None and request.form:
            new_credits = request.form.get("credits")

        try:
            if new_credits is None:
                raise ValueError
            new_credits = float(new_credits)
        except (ValueError, TypeError):
            return jsonify({"ok": False, "error": "Invalid credit amount"}), 400

        g.db.execute("UPDATE users SET credits = ? WHERE id = ?", (new_credits, user_id))
        g.db.commit()
        return jsonify({"ok": True})

    @app.delete("/api/admin/user/<user_id>")
    @login_required
    @admin_required
    def admin_delete_user(user_id: str):
        if user_id == session["user_id"]:
            return jsonify({"ok": False, "error": "Cannot delete your own account"}), 400

        # Find all syllabi for the user to delete files
        syllabi = g.db.execute("SELECT id, pdf_path FROM syllabi WHERE user_id = ?", (user_id,)).fetchall()
        for syllabus in syllabi:
            if syllabus['pdf_path'] and os.path.exists(syllabus['pdf_path']):
                os.remove(syllabus['pdf_path'])

            extra_mats = g.db.execute("SELECT file_path FROM extra_materials WHERE syllabus_id = ?", (syllabus['id'],)).fetchall()
            for mat in extra_mats:
                if mat['file_path'] and os.path.exists(mat['file_path']):
                    os.remove(mat['file_path'])

        # Cascade delete from all related tables
        g.db.execute("DELETE FROM extra_materials WHERE syllabus_id IN (SELECT id FROM syllabi WHERE user_id = ?)", (user_id,))
        g.db.execute("DELETE FROM mcq_attempts WHERE user_id = ?", (user_id,))
        g.db.execute("DELETE FROM chats WHERE user_id = ?", (user_id,))
        g.db.execute("DELETE FROM flashcard_reviews WHERE user_id = ?", (user_id,))
        g.db.execute("DELETE FROM syllabi WHERE user_id = ?", (user_id,))
        g.db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        g.db.commit()
        return jsonify({"ok": True})

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0")
