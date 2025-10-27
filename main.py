"""
Flask ERP Core (Hardened)
Amaç: Güvenli oturum, CSRF/XSS korumaları, parametrik SQL, kapsamlı loglama,
hata sayfaları, cache kontrolü ve PEP8 uyumlu, tek-dosyada çalışan temel iskelet.

Routes:
- GET/POST /login  : Kullanıcı girişi (CSRF kontrollü)
- GET       /logout : Oturum kapat
- GET       /       : Dashboard (login_required)
- GET       /healthz: Sağlık kontrol uç noktası
- GET       /pdf/<id>: PDF üretim kancası (try/except + log)

Dönüş:
- Flask Response objeleri (HTML/JSON)
"""

import os
import sqlite3
import secrets
import logging
from logging.handlers import RotatingFileHandler
from datetime import timedelta, datetime
from pathlib import Path
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, g, flash, abort, jsonify, make_response
)
from markupsafe import escape  # XSS'e karşı güvenli kaçış

# --- Uygulama ve Config ---

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "database.db"
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

def create_app():
    """
    Amaç: Flask uygulamasını konfigüre eder ve döndürür.
    Parametre: Yok
    Dönüş: Flask app
    """
    app = Flask(
        __name__,
        template_folder=str(BASE_DIR / "templates"),
        static_folder=str(BASE_DIR / "static"),
    )

    # SECRET_KEY (env > .env > random fallback)
    secret = os.getenv("SECRET_KEY")
    if not secret:
        # Production'da environment'tan ver!
        secret = secrets.token_hex(32)
    app.config["SECRET_KEY"] = secret

    # Session güvenliği
    app.permanent_session_lifetime = timedelta(minutes=int(os.getenv("SESSION_MINUTES", "120")))
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # HTTPS varsa aç: SESSION_COOKIE_SECURE=True
    )

    # Basit güvenlik başlıkları (CSP minimal; ihtiyaç oldukça genişlet)
    @app.after_request
    def set_security_headers(resp):
        """
        Amaç: Güvenlik ve cache headerlarını yanıt üstüne eklemek.
        Parametre: resp (Response)
        Dönüş: Response
        """
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-XSS-Protection"] = "0"
        # Statik dosyalar için (CDN yoksa) kısa cache örneği:
        if request.path.startswith("/static/"):
            resp.headers["Cache-Control"] = "public, max-age=86400"
        else:
            resp.headers["Cache-Control"] = "no-store"
        # Basit CSP (ihtiyaca göre genişlet)
        resp.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline';"
        return resp

    # Logging (Rotating File + Console)
    setup_logging(app)

    # DB şemasını hazırla
    with app.app_context():
        init_db()

    # Request bazlı loglar
    @app.before_request
    def log_request_meta():
        """
        Amaç: Her isteğin temel meta bilgisini loglamak.
        Parametre: Yok (request context)
        Dönüş: Yok
        """
        # Potansiyel kullanıcı kimliği
        uid = session.get("user_id")
        app.logger.info(
            "REQ ip=%s user_id=%s method=%s path=%s ua=%s",
            request.headers.get("X-Forwarded-For", request.remote_addr),
            uid,
            request.method,
            request.path,
            request.headers.get("User-Agent", "-"),
        )

    # CSRF: manuel token (Flask-WTF'siz)
    @app.before_request
    def ensure_csrf_for_mutations():
        """
        Amaç: Mutating (POST/PUT/PATCH/DELETE) isteklerde CSRF token doğrulamak.
        Parametre: Yok
        Dönüş: Yok (abort 403 atabilir)
        """
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            # /healthz gibi istisnalar gerekirse buraya ekle
            # Login formu vs. tüm POST'larda gizli input 'csrf_token' beklenir
            form_token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
            sess_token = session.get("csrf_token")
            if not sess_token or not form_token or not secrets.compare_digest(form_token, sess_token):
                app.logger.warning("CSRF token invalid/missing path=%s", request.path)
                abort(403)

    # --- Routes ---
    @app.route("/healthz")
    def healthz():
        """
        Amaç: Sağlık kontrolü (kullanıldığı yerlerde basit check)
        Parametre: Yok
        Dönüş: JSON {"status":"ok","ts":...}
        """
        return jsonify(status="ok", ts=datetime.utcnow().isoformat() + "Z")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        """
        Amaç: Kullanıcı giriş işlemi (CSRF + parametrik SQL + hatalı giriş logu)
        Parametre: Form alanları: username, password, csrf_token
        Dönüş: HTML (GET/başarısız POST) veya Redirect (başarılı POST)
        """
        # Zaten girişliyse
        if session.get("user_id"):
            return redirect(url_for("dashboard"))

        # CSRF token üret (her GET'te yenileyelim)
        if request.method == "GET":
            session["csrf_token"] = secrets.token_urlsafe(32)
            return render_template("login.html", csrf_token=session["csrf_token"])

        # POST
        try:
            username = (request.form.get("username") or "").strip()
            password = (request.form.get("password") or "").strip()
            if not username or not password:
                flash("Kullanıcı adı ve şifre zorunlu.", "warning")
                app.logger.warning("Login empty fields")
                return render_template("login.html", csrf_token=session.get("csrf_token")), 400

            db = get_db()
            # SQL injection'a karşı parametrik kullanım
            user = db.execute(
                "SELECT id, username, password, role, is_active FROM users WHERE username = ?",
                (username,),
            ).fetchone()

            # Parola doğrulama: şu an düz metin (production'da hash'le!)
            if not user or int(user["is_active"]) != 1 or user["password"] != password:
                flash("Kullanıcı adı veya şifre hatalı.", "danger")
                app.logger.warning("Login failed for user=%s", username)
                return render_template("login.html", csrf_token=session.get("csrf_token")), 401

            # Başarılı login
            session.clear()
            session.permanent = True
            session["user_id"] = int(user["id"])
            session["username"] = user["username"]
            session["role"] = user["role"]

            dest = request.args.get("next") or url_for("dashboard")
            if dest.startswith("http://") or dest.startswith("https://"):
                dest = url_for("dashboard")  # open redirect engeli
            app.logger.info("Login OK user_id=%s", session["user_id"])
            return redirect(dest)

        except Exception as exc:
            app.logger.exception("Login error: %s", exc)
            flash("Beklenmeyen bir hata oluştu.", "danger")
            return render_template("login.html", csrf_token=session.get("csrf_token")), 500

    @app.get("/logout")
    def logout():
        """
        Amaç: Oturumu kapatır.
        Parametre: Yok
        Dönüş: Redirect /login
        """
        session.clear()
        flash("Çıkış yapıldı.", "info")
        return redirect(url_for("login"))

    @app.get("/")
    @login_required
    def dashboard():
        """
        Amaç: Giriş gerektiren ana ekran.
        Parametre: Yok
        Dönüş: HTML
        """
        uname = escape(session.get("username") or "")
        return render_template("dashboard.html", username=uname)

    @app.get("/pdf/<int:doc_id>")
    @login_required
    def pdf_view(doc_id: int):
        """
        Amaç: PDF üretim/servis kancası. pdfkit/wkhtmltopdf entegrasyonunu güvenli çalıştırır.
        Parametre: doc_id (int)
        Dönüş: PDF (application/pdf) veya hata halinde sade mesaj
        """
        try:
            # Örnek: db'den ilgili kaydı al
            row = get_db().execute(
                "SELECT id, title FROM documents WHERE id = ?", (doc_id,)
            ).fetchone()
            if not row:
                app.logger.warning("PDF not found id=%s", doc_id)
                abort(404)

            # Burada senin HTML->PDF üretimini çağır (pdfkit/wkhtmltopdf)
            # pdf_bytes = render_to_pdf(template="pdf/template.html", context={...})
            # resp = make_response(pdf_bytes)
            # resp.headers["Content-Type"] = "application/pdf"
            # resp.headers["Content-Disposition"] = f"inline; filename=doc_{doc_id}.pdf"
            # return resp

            # Şimdilik placeholder:
            return jsonify(message=f"PDF üretim kancası: {row['title']}", id=doc_id)

        except Exception as exc:
            app.logger.exception("PDF error id=%s err=%s", doc_id, exc)
            # Kullanıcıya sade mesaj
            return render_template("errors/500.html"), 500

    # Error handlers
    @app.errorhandler(403)
    def err_403(e):
        """
        Amaç: Yetkisiz/CSRF durumunda kullanıcıya sade sayfa, log'a detay.
        Parametre: e (Exception)
        Dönüş: HTML 403
        """
        app.logger.warning("403 path=%s ref=%s", request.path, request.referrer)
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def err_404(e):
        """
        Amaç: Bulunamadı sayfası.
        Parametre: e (Exception)
        Dönüş: HTML 404
        """
        app.logger.info("404 path=%s", request.path)
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def err_500(e):
        """
        Amaç: Sunucu hatası sayfası (detay log'da tutulur).
        Parametre: e (Exception)
        Dönüş: HTML 500
        """
        app.logger.exception("500 path=%s", request.path)
        return render_template("errors/500.html"), 500

    return app


# --- Logging Kurulumu ---

def setup_logging(app: Flask) -> None:
    """
    Amaç: Rotating file + console loglama kurulumunu yapmak.
    Parametre: app (Flask)
    Dönüş: Yok
    """
    app.logger.setLevel(logging.INFO)

    # File handler
    file_handler = RotatingFileHandler(
        LOG_DIR / "app.log", maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(fmt)
    app.logger.addHandler(file_handler)

    # Console handler
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    app.logger.addHandler(console)


# --- DB Yardımcıları ---

def get_db():
    """
    Amaç: SQLite bağlantısını (request-scope) döndürür.
    Parametre: Yok
    Dönüş: sqlite3.Connection (row_factory=Row)
    """
    if "db" not in g:
        conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        g.db = conn
    return g.db


def init_db():
    """
    Amaç: Gerekli tabloları oluşturur (idempotent).
    Parametre: Yok
    Dönüş: Yok
    """
    db = get_db()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,          -- TODO: production'da HASH sakla!
            role TEXT DEFAULT 'user',
            is_active INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
        """
    )
    db.commit()


def teardown_db(exc=None):
    """
    Amaç: Request sonunda DB bağlantısını kapatır (teardown_appcontext ile bağla).
    Parametre: exc (Exception veya None)
    Dönüş: Yok
    """
    db = g.pop("db", None)
    if db is not None:
        db.close()


# --- Decorators ---

def login_required(view):
    """
    Amaç: Kimlik doğrulaması gerektiren endpoint'ler için dekoratör.
    Parametre: view (callable)
    Dönüş: wrapped view
    """
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


# --- Entrypoint ---

app = create_app()
app.teardown_appcontext(teardown_db)

if __name__ == "__main__":
    # Lokal geliştirme
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    debug = os.getenv("FLASK_DEBUG", "1") == "1"
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=debug)
