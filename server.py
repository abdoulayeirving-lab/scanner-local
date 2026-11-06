#!/usr/bin/env python3
"""Enregistreur de produits : le PC = serveur + base, le téléphone = scanner."""
import base64, http.server, ssl, socket, sqlite3, json, os, csv, io, time, math, ipaddress
import hashlib, hmac, secrets, urllib.request, urllib.parse
import threading, traceback
from contextlib import contextmanager
from datetime import date, datetime
from urllib.parse import urlparse, parse_qs

PORT = 8443
DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(DIR, "produits.db")


def load_env_file(path):
    """Charge les clés du fichier .env sans écraser les variables déjà définies."""
    try:
        with open(path, encoding="utf-8") as file:
            for line in file:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, value = line.split("=", 1)
                name = name.strip()
                value = value.strip()
                if value[:1] == value[-1:] and value[:1] in ("'", '"'):
                    value = value[1:-1]
                if name and value and name not in os.environ:
                    os.environ[name] = value
    except FileNotFoundError:
        pass


load_env_file(os.path.join(DIR, ".env"))

VAPID_KEY = os.path.join(DIR, "push_private.pem")
CA_CERT = os.path.join(DIR, "scanner-ca.pem")
CA_CERT_WINDOWS = os.path.join(DIR, "scanner-ca.cer")
USER_DATA_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "StockLocal")
CA_KEY = os.path.join(USER_DATA_DIR, "root-ca-key.pem")
ALERT_DAYS = 7
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:admin@example.com")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_STATES = {}
SESSION_COOKIE = "stock_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 jours


def normalize_number(value, default=0, *, allow_zero=True):
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError("bool n'est pas un nombre valide")
    s = str(value).strip().replace(" ", "")
    if not s:
        return default
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        num = float(s)
    except ValueError as exc:
        raise ValueError(f"nombre invalide: {value!r}") from exc
    if not allow_zero and num == 0:
        raise ValueError("la valeur ne peut pas être nulle")
    return num


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1)); return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()

IP = local_ip()

@contextmanager
def db():
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "")


def google_redirect_uri_for(host):
    """Construit l'URI de redirection Google autorisée pour l'hôte consulté."""
    host = (host or "").split(":")[0].strip().lower()
    if host in ("localhost", "127.0.0.1", "::1"):
        return f"https://localhost:{PORT}/auth/google/callback"
    if GOOGLE_REDIRECT_URI:
        return GOOGLE_REDIRECT_URI
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return f"https://{host}:{PORT}/auth/google/callback"
    if ip.version == 4:
        # Google refuse les IP brutes : nip.io fournit un nom de domaine qui
        # pointe vers l'adresse locale (nécessite Internet pour la résolution DNS).
        return f"https://{host.replace('.', '-')}.nip.io:{PORT}/auth/google/callback"
    return f"https://[{host}]:{PORT}/auth/google/callback"


def google_redirect_uri():
    return google_redirect_uri_for(IP)


def google_enabled():
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt), 200_000)
    return f"{salt}${digest.hex()}"


def verify_password(password, stored):
    try:
        salt, expected = stored.split("$", 1)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                     bytes.fromhex(salt), 200_000).hex()
        return hmac.compare_digest(digest, expected)
    except (ValueError, AttributeError):
        return False


def create_session(shop_id):
    token = secrets.token_urlsafe(32)
    with db() as c:
        c.execute("DELETE FROM sessions WHERE created < datetime('now', '-30 days')")
        c.execute("INSERT INTO sessions(token, shop_id, created) VALUES(?,?,?)",
                  (token, shop_id, datetime.now().isoformat(timespec="seconds")))
    return token


def initialize_database():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS shops(
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            email TEXT UNIQUE, password_hash TEXT, google_sub TEXT UNIQUE,
            created TEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS sessions(
            token TEXT PRIMARY KEY, shop_id INTEGER NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
            created TEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS expiry_alerts(
            product_id INTEGER NOT NULL, expiration_date TEXT NOT NULL, kind TEXT NOT NULL,
            sent_at TEXT NOT NULL, PRIMARY KEY(product_id, expiration_date, kind))""")

        products_exists = c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='products'").fetchone()
        product_cols = {r["name"] for r in c.execute("PRAGMA table_info(products)")} if products_exists else set()
        legacy = products_exists and "shop_id" not in product_cols

        default_shop_id = None
        if legacy:
            row = c.execute("SELECT id FROM shops ORDER BY id LIMIT 1").fetchone()
            if row:
                default_shop_id = row["id"]
            else:
                now = datetime.now().isoformat(timespec="seconds")
                cursor = c.execute(
                    "INSERT INTO shops(name, email, created) VALUES('Ma boutique', NULL, ?)", (now,))
                default_shop_id = cursor.lastrowid

        if legacy:
            legacy_expiration = "expiration_date" if "expiration_date" in product_cols else "NULL"
            c.execute("ALTER TABLE products RENAME TO products_legacy")
            c.execute("""CREATE TABLE products(
                id INTEGER PRIMARY KEY AUTOINCREMENT, shop_id INTEGER NOT NULL REFERENCES shops(id),
                code TEXT NOT NULL, name TEXT, price REAL DEFAULT 0, qty INTEGER DEFAULT 0,
                updated TEXT, expiration_date TEXT, UNIQUE(shop_id, code))""")
            c.execute(f"""INSERT INTO products(id, shop_id, code, name, price, qty, updated, expiration_date)
                SELECT id, ?, code, name, price, qty, updated, {legacy_expiration} FROM products_legacy""",
                (default_shop_id,))
            c.execute("DROP TABLE products_legacy")
        elif not products_exists:
            c.execute("""CREATE TABLE products(
                id INTEGER PRIMARY KEY AUTOINCREMENT, shop_id INTEGER NOT NULL REFERENCES shops(id),
                code TEXT NOT NULL, name TEXT, price REAL DEFAULT 0, qty INTEGER DEFAULT 0,
                updated TEXT, expiration_date TEXT, UNIQUE(shop_id, code))""")

        if default_shop_id is None:
            default_shop_id = c.execute("SELECT MIN(id) AS id FROM shops").fetchone()["id"]

        push_exists = c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='push_subscriptions'").fetchone()
        push_cols = {r["name"] for r in c.execute("PRAGMA table_info(push_subscriptions)")} if push_exists else set()
        if push_exists and "shop_id" not in push_cols:
            c.execute(f"ALTER TABLE push_subscriptions ADD COLUMN shop_id INTEGER NOT NULL DEFAULT {int(default_shop_id or 1)}")
        elif not push_exists:
            c.execute("""CREATE TABLE push_subscriptions(
                endpoint TEXT PRIMARY KEY, subscription_json TEXT NOT NULL,
                created TEXT NOT NULL, shop_id INTEGER NOT NULL REFERENCES shops(id))""")

        settings_exists = c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_settings'").fetchone()
        settings_cols = {r["name"] for r in c.execute("PRAGMA table_info(app_settings)")} if settings_exists else set()
        if settings_exists and "shop_id" not in settings_cols:
            c.execute("""CREATE TABLE app_settings_new(
                shop_id INTEGER NOT NULL REFERENCES shops(id), name TEXT NOT NULL,
                value TEXT NOT NULL, PRIMARY KEY(shop_id, name))""")
            c.execute("INSERT INTO app_settings_new(shop_id, name, value) SELECT ?, name, value FROM app_settings",
                      (default_shop_id or 1,))
            c.execute("DROP TABLE app_settings")
            c.execute("ALTER TABLE app_settings_new RENAME TO app_settings")
        elif not settings_exists:
            c.execute("""CREATE TABLE app_settings(
                shop_id INTEGER NOT NULL REFERENCES shops(id), name TEXT NOT NULL,
                value TEXT NOT NULL, PRIMARY KEY(shop_id, name))""")


initialize_database()


def remote_scan_is_enabled(shop_id):
    with db() as c:
        row = c.execute(
            "SELECT value FROM app_settings WHERE shop_id=? AND name='remote_scan_enabled'",
            (shop_id,)).fetchone()
    return bool(row and row["value"] == "1")


def set_remote_scan_enabled(shop_id, enabled):
    with db() as c:
        c.execute(
            "INSERT INTO app_settings(shop_id, name, value) VALUES(?, 'remote_scan_enabled', ?) "
            "ON CONFLICT(shop_id, name) DO UPDATE SET value=excluded.value",
            (shop_id, "1" if enabled else "0"))


def get_push_public_key():
    from pywebpush import webpush  # noqa: F401
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    if not os.path.exists(VAPID_KEY):
        private_key = ec.generate_private_key(ec.SECP256R1())
        pem = private_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption())
        with open(VAPID_KEY, "wb") as f:
            f.write(pem)
    with open(VAPID_KEY, "rb") as f:
        private_key = serialization.load_pem_private_key(f.read(), password=None)
    raw_public_key = private_key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw_public_key).rstrip(b"=").decode("ascii")


def send_expiry_pushes():
    try:
        from pywebpush import WebPushException, webpush
        get_push_public_key()
    except ImportError:
        print("Web Push désactivé : installez les dépendances avec python -m pip install -r requirements.txt")
        return

    today = date.today()
    with db() as c:
        shop_ids = [r["id"] for r in c.execute("SELECT id FROM shops")]
        products = []
        subscriptions_by_shop = {}
        for shop_id in shop_ids:
            products += [(shop_id, r) for r in c.execute(
                "SELECT id, code, name, expiration_date FROM products "
                "WHERE shop_id=? AND qty > 0 AND expiration_date IS NOT NULL AND expiration_date != ''",
                (shop_id,))]
            subscriptions_by_shop[shop_id] = c.execute(
                "SELECT endpoint, subscription_json FROM push_subscriptions WHERE shop_id=?",
                (shop_id,)).fetchall()

    for shop_id, product in products:
        subscriptions = subscriptions_by_shop.get(shop_id) or []
        if not subscriptions:
            continue
        try:
            expiry = date.fromisoformat(product["expiration_date"])
        except ValueError:
            continue
        days_left = (expiry - today).days
        if days_left > ALERT_DAYS:
            continue
        kind = "expired" if days_left < 0 else "expiring"
        with db() as c:
            already_sent = c.execute(
                "SELECT 1 FROM expiry_alerts WHERE product_id=? AND expiration_date=? AND kind=?",
                (product["id"], product["expiration_date"], kind)).fetchone()
        if already_sent:
            continue

        title = "Produit expiré" if kind == "expired" else "Expiration proche"
        body = (f"{product['name'] or product['code']} a expiré le {expiry:%d/%m/%Y}."
                if kind == "expired" else
                f"{product['name'] or product['code']} expire dans {days_left} jour(s), le {expiry:%d/%m/%Y}.")
        payload = json.dumps({
            "title": title, "body": body, "tag": f"expiration-{product['id']}",
            "url": "/"
        }, ensure_ascii=False)
        delivered = False
        for subscription in subscriptions:
            try:
                webpush(
                    subscription_info=json.loads(subscription["subscription_json"]),
                    data=payload, vapid_private_key=VAPID_KEY,
                    vapid_claims={"sub": VAPID_SUBJECT})
                delivered = True
            except WebPushException as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status in (404, 410):
                    with db() as c:
                        c.execute("DELETE FROM push_subscriptions WHERE endpoint=?",
                                  (subscription["endpoint"],))
                else:
                    print(f"Échec d'envoi d'une notification Web Push: {exc}")
            except Exception:
                traceback.print_exc()
        if delivered:
            with db() as c:
                c.execute(
                    "INSERT OR IGNORE INTO expiry_alerts(product_id, expiration_date, kind, sent_at) "
                    "VALUES(?,?,?,?)",
                    (product["id"], product["expiration_date"], kind,
                     datetime.now().isoformat(timespec="seconds")))


def expiry_alert_loop():
    while True:
        try:
            send_expiry_pushes()
        except Exception:
            traceback.print_exc()
        time.sleep(3600)


def ensure_local_ca():
    import datetime
    import ipaddress
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    os.makedirs(USER_DATA_DIR, exist_ok=True)
    if os.path.exists(CA_KEY) and os.path.exists(CA_CERT):
        with open(CA_KEY, "rb") as key_file:
            ca_key = serialization.load_pem_private_key(key_file.read(), password=None)
        with open(CA_CERT, "rb") as cert_file:
            ca_cert = x509.load_pem_x509_certificate(cert_file.read())
    elif os.path.exists(CA_KEY) or os.path.exists(CA_CERT):
        raise RuntimeError(
            "La clé ou le certificat de l’autorité locale est manquant. "
            "Restaurez-les ensemble ou supprimez les deux pour en créer une nouvelle.")
    else:
        ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = datetime.datetime.now(datetime.timezone.utc)
        ca_name = x509.Name([
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Stock local"),
            x509.NameAttribute(NameOID.COMMON_NAME, "Stock Local Development CA")
        ])
        ca_cert = (
            x509.CertificateBuilder()
            .subject_name(ca_name)
            .issuer_name(ca_name)
            .public_key(ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(
                digital_signature=False, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=True,
                crl_sign=True, encipher_only=None, decipher_only=None), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
                           critical=False)
            .add_extension(x509.NameConstraints(
                permitted_subtrees=[
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_network("127.0.0.0/8")),
                    x509.IPAddress(ipaddress.ip_network("10.0.0.0/8")),
                    x509.IPAddress(ipaddress.ip_network("172.16.0.0/12")),
                    x509.IPAddress(ipaddress.ip_network("192.168.0.0/16")),
                    x509.IPAddress(ipaddress.ip_network("169.254.0.0/16"))
                ],
                excluded_subtrees=None
            ), critical=True)
            .sign(ca_key, hashes.SHA256())
        )
        with open(CA_KEY, "wb") as key_file:
            key_file.write(ca_key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption()))
        if os.name != "nt":
            os.chmod(CA_KEY, 0o600)

    with open(CA_CERT, "wb") as cert_file:
        cert_file.write(ca_cert.public_bytes(serialization.Encoding.PEM))
    with open(CA_CERT_WINDOWS, "wb") as cert_file:
        cert_file.write(ca_cert.public_bytes(serialization.Encoding.DER))
    return ca_key, ca_cert


def certificate_matches_current_ip(cert_path, key_path, ca_cert):
    import ipaddress
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    try:
        with open(cert_path, "rb") as f:
            certificate = x509.load_pem_x509_certificate(f.read())
        with open(key_path, "rb") as f:
            private_key = serialization.load_pem_private_key(f.read(), password=None)
        sans = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        cert_public_key = certificate.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        key_public_key = private_key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        now = datetime.now().astimezone()
        return (
            certificate.issuer == ca_cert.subject
            and cert_public_key == key_public_key
            and ipaddress.ip_address(IP) in sans.get_values_for_type(x509.IPAddress)
            and "localhost" in sans.get_values_for_type(x509.DNSName)
            and certificate.not_valid_before_utc <= now
            and certificate.not_valid_after_utc > now
        )
    except (OSError, ValueError, TypeError, x509.ExtensionNotFound):
        return False


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send_file(self, filename, content_type, extra=None):
        with open(os.path.join(DIR, filename), encoding="utf-8") as file:
            content = file.read()
        return self.send(content, content_type, extra=extra)

    def is_local_client(self):
        try:
            address = ipaddress.ip_address(self.client_address[0])
            return address.is_loopback or address == ipaddress.ip_address(IP)
        except ValueError:
            return False

    def parse_cookies(self):
        cookies = {}
        for part in (self.headers.get("Cookie") or "").split(";"):
            if "=" in part:
                key, value = part.strip().split("=", 1)
                cookies[key] = value
        return cookies

    def current_shop_id(self):
        token = self.parse_cookies().get(SESSION_COOKIE)
        if not token:
            return None
        with db() as c:
            row = c.execute("SELECT shop_id FROM sessions WHERE token=?", (token,)).fetchone()
        return row["shop_id"] if row else None

    def set_session_cookie(self, token, clear=False):
        if clear:
            return f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Secure; Max-Age=0"
        return f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Secure; Max-Age={SESSION_MAX_AGE}"

    def require_auth(self, api=False):
        shop_id = self.current_shop_id()
        if shop_id is not None:
            return shop_id
        if api:
            self.send({"error": "Authentification requise."}, code=401)
        else:
            self.send_response(302)
            self.send_header("Location", "/login")
            self.send_header("Content-Length", "0")
            self.end_headers()
        return None

    def require_remote_scan(self, shop_id):
        if self.is_local_client() or remote_scan_is_enabled(shop_id):
            return True
        self.send({"error": "Le scan à distance est désactivé sur le PC."}, code=403)
        return False

    def send(self, body, ctype="application/json", code=200, extra=None):
        if isinstance(body, (dict, list)): body = json.dumps(body)
        if isinstance(body, str):
            body = body.encode()
        else:
            ctype = ctype.split(";", 1)[0]
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith(("text/", "application/json", "application/javascript", "application/manifest+json")) else ""))
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items(): self.send_header(k, v)
        self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        if u.path == "/login":
            return self.send_file("login.html", "text/html")
        if u.path == "/auth/google/callback":
            return self.google_callback(q)
        if u.path == "/api/auth/google-config":
            return self.send({"enabled": google_enabled()})
        if u.path == "/api/auth/google":
            if not google_enabled():
                return self.redirect("/login?error=google-non-configure")
            state = secrets.token_urlsafe(16)
            redirect_uri = google_redirect_uri_for(self.headers.get("Host", ""))
            GOOGLE_STATES[state] = {"created": time.time(), "redirect_uri": redirect_uri}
            for key, entry in list(GOOGLE_STATES.items()):
                if time.time() - entry["created"] > 600:
                    GOOGLE_STATES.pop(key, None)
            params = urllib.parse.urlencode({
                "client_id": GOOGLE_CLIENT_ID,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": "openid email profile",
                "state": state,
                "prompt": "select_account"
            })
            return self.redirect(f"https://accounts.google.com/o/oauth2/v2/auth?{params}")

        if u.path in ("/", "/index.html", "/scan"):
            shop_id = self.require_auth()
            if shop_id is None:
                return
            if u.path in ("/", "/index.html"):
                return self.send_file("index.html", "text/html")
            if not self.require_remote_scan(shop_id):
                return
            return self.send_file("scan.html", "text/html")
        if u.path == "/notifications.js":
            return self.send_file("notifications.js", "application/javascript")
        if u.path == "/sw.js":
            return self.send_file(
                "sw.js", "application/javascript", extra={"Service-Worker-Allowed": "/"})
        static_assets = {
            "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
            "/offline.html": ("offline.html", "text/html"),
            "/icon.svg": ("icon.svg", "image/svg+xml"),
            "/vendor/qrcode.min.js": ("vendor/qrcode.min.js", "application/javascript"),
            "/vendor/html5-qrcode.min.js": ("vendor/html5-qrcode.min.js", "application/javascript"),
        }
        if u.path in static_assets or u.path == "/auth.js":
            filename, content_type = (static_assets.get(u.path) or ("auth.js", "application/javascript"))
            return self.send_file(filename, content_type)
        if u.path == "/scanner-ca.cer":
            shop_id = self.require_auth(api=True)
            if shop_id is None:
                return
            if not self.require_remote_scan(shop_id):
                return
            try:
                with open(CA_CERT_WINDOWS, "rb") as file:
                    content = file.read()
            except OSError:
                return self.send({"error": "certificat introuvable"}, code=404)
            return self.send(content, "application/x-x509-ca-cert",
                             extra={"Content-Disposition": "attachment; filename=scanner-ca.cer"})
        if u.path == "/api/auth/me":
            shop_id = self.current_shop_id()
            if shop_id is None:
                return self.send({"authenticated": False}, code=401)
            with db() as c:
                row = c.execute("SELECT name, email FROM shops WHERE id=?", (shop_id,)).fetchone()
            return self.send({"authenticated": True, "name": row["name"], "email": row["email"]})
        if u.path == "/api/info":
            if self.require_auth(api=True) is None:
                return
            return self.send({"scan_url": f"https://{IP}:{PORT}/scan"})
        if u.path == "/api/remote-scan":
            shop_id = self.require_auth(api=True)
            if shop_id is None:
                return
            return self.send({
                "enabled": remote_scan_is_enabled(shop_id),
                "scan_url": f"https://{IP}:{PORT}/scan"
            })
        if u.path == "/api/push-config":
            if self.require_auth(api=True) is None:
                return
            try:
                return self.send({"enabled": True, "public_key": get_push_public_key()})
            except ImportError:
                return self.send({"enabled": False, "reason": "Dépendances Web Push non installées"}, code=503)
        if u.path == "/api/products":
            shop_id = self.require_auth(api=True)
            if shop_id is None:
                return
            if not self.require_remote_scan(shop_id):
                return
            with db() as c:
                return self.send([dict(r) for r in c.execute(
                    "SELECT * FROM products WHERE shop_id=? ORDER BY updated DESC", (shop_id,))])
        if u.path == "/api/product":
            shop_id = self.require_auth(api=True)
            if shop_id is None:
                return
            if not self.require_remote_scan(shop_id):
                return
            with db() as c:
                r = c.execute("SELECT * FROM products WHERE code=? AND shop_id=?",
                              (q.get("code", [""])[0], shop_id)).fetchone()
            return self.send(dict(r) if r else {})
        if u.path == "/export.csv":
            shop_id = self.require_auth(api=True)
            if shop_id is None:
                return
            if not self.require_remote_scan(shop_id):
                return
            out = io.StringIO(); w = csv.writer(out, delimiter=";")
            w.writerow(["code", "nom", "prix", "quantite", "date_expiration", "maj"])
            with db() as c:
                for r in c.execute(
                        "SELECT code,name,price,qty,expiration_date,updated FROM products WHERE shop_id=?",
                        (shop_id,)):
                    w.writerow(list(r))
            return self.send("\ufeff" + out.getvalue(), "text/csv",
                             extra={"Content-Disposition": "attachment; filename=produits.csv"})
        self.send({"error": "not found"}, code=404)

    def do_POST(self):
        path = urlparse(self.path).path
        auth_paths = ("/api/auth/register", "/api/auth/login", "/api/auth/logout")
        if path not in ("/api/products", "/api/push-subscriptions", "/api/remote-scan") + auth_paths:
            return self.send({"error": "not found"}, code=404)
        if path == "/api/remote-scan" and not self.is_local_client():
            return self.send({"error": "Activez le scan distant depuis le PC."}, code=403)
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length < 0 or length > 1024 * 1024:
                return self.send({"error": "requête trop volumineuse"}, code=413)
            raw = self.rfile.read(length)
            d = json.loads(raw.decode("utf-8") or "{}") if raw else {}
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return self.send({"error": "données JSON invalides"}, code=400)
        if not isinstance(d, dict):
            return self.send({"error": "format de données invalide"}, code=400)

        if path in auth_paths:
            return self.handle_auth(path, d)

        shop_id = self.require_auth(api=True)
        if shop_id is None:
            return
        if path == "/api/products" and not self.require_remote_scan(shop_id):
            return

        if path == "/api/remote-scan":
            enabled = d.get("enabled")
            if not isinstance(enabled, bool):
                return self.send({"error": "état d’activation invalide"}, code=400)
            set_remote_scan_enabled(shop_id, enabled)
            return self.send({"ok": True, "enabled": enabled})

        if path == "/api/push-subscriptions":
            try:
                subscription = d["subscription"]
                endpoint = subscription["endpoint"]
                keys = subscription["keys"]
                if (urlparse(endpoint).scheme != "https" or not keys.get("p256dh")
                        or not keys.get("auth")):
                    raise ValueError
                get_push_public_key()
            except (KeyError, TypeError, ValueError):
                return self.send({"error": "abonnement Web Push invalide"}, code=400)
            except ImportError:
                return self.send({"error": "Dépendances Web Push non installées"}, code=503)
            with db() as c:
                c.execute(
                    "INSERT INTO push_subscriptions(endpoint, subscription_json, created, shop_id) VALUES(?,?,?,?) "
                    "ON CONFLICT(endpoint) DO UPDATE SET subscription_json=excluded.subscription_json, shop_id=excluded.shop_id",
                    (endpoint, json.dumps(subscription), datetime.now().isoformat(timespec="seconds"), shop_id))
            return self.send({"ok": True})

        code = str(d.get("code", "")).strip()
        if not code:
            return self.send({"error": "code manquant"}, code=400)
        try:
            qty_number = float(normalize_number(d.get("qty"), default=1))
            if not qty_number.is_integer() or qty_number < 0:
                raise ValueError
            qty = int(qty_number)
            price_raw = d.get("price")
            price = None if price_raw is None or str(price_raw).strip() == "" \
                else normalize_number(price_raw, default=0)
            if price is not None and (price < 0 or not math.isfinite(price)):
                raise ValueError
            expiration_provided = "expiration_date" in d
            expiration = d.get("expiration_date") if expiration_provided else None
            if expiration:
                if not isinstance(expiration, str) or date.fromisoformat(expiration).isoformat() != expiration:
                    raise ValueError
            name = str(d.get("name") or "").strip()
        except (ValueError, TypeError, OverflowError):
            return self.send({"error": "quantité, prix ou date d’expiration invalide"}, code=400)

        now = time.strftime("%Y-%m-%d %H:%M:%S")
        with db() as c:
            r = c.execute("SELECT * FROM products WHERE code=? AND shop_id=?", (code, shop_id)).fetchone()
            if r:
                price = float(r["price"] or 0) if price is None else price
                if expiration_provided:
                    c.execute(
                        "UPDATE products SET name=?, price=?, qty=qty+?, updated=?, expiration_date=? WHERE id=?",
                        (name or r["name"], price, qty, now, expiration or None, r["id"]))
                else:
                    c.execute("UPDATE products SET name=?, price=?, qty=qty+?, updated=? WHERE id=?",
                              (name or r["name"], price, qty, now, r["id"]))
            else:
                c.execute(
                    "INSERT INTO products(shop_id,code,name,price,qty,updated,expiration_date) VALUES(?,?,?,?,?,?,?)",
                    (shop_id, code, name, price if price is not None else 0, qty, now, expiration or None))
        self.send({"ok": True})

    def do_DELETE(self):
        path = urlparse(self.path).path
        shop_id = self.require_auth(api=True)
        if shop_id is None:
            return
        if path == "/api/push-subscriptions":
            try:
                length = int(self.headers.get("Content-Length", 0))
                d = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                endpoint = d["endpoint"]
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
                return self.send({"error": "abonnement invalide"}, code=400)
            with db() as c:
                c.execute("DELETE FROM push_subscriptions WHERE endpoint=? AND shop_id=?", (endpoint, shop_id))
            return self.send({"ok": True})
        if not self.require_remote_scan(shop_id):
            return
        if not path.startswith("/api/products/"):
            return self.send({"error": "not found"}, code=404)
        pid = path.rsplit("/", 1)[-1]
        if not pid or not pid.isdigit():
            return self.send({"error": "identifiant invalide"}, code=400)
        with db() as c:
            row = c.execute("SELECT id FROM products WHERE id=? AND shop_id=?", (pid, shop_id)).fetchone()
            if not row:
                return self.send({"error": "produit introuvable"}, code=404)
            c.execute("DELETE FROM products WHERE id=?", (pid,))
            c.execute("DELETE FROM expiry_alerts WHERE product_id=?", (pid,))
        self.send({"ok": True})

    def redirect(self, location, extra=None):
        self.send_response(302)
        self.send_header("Location", location)
        headers = {"Content-Length": "0"}
        headers.update(extra or {})
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()

    def handle_auth(self, path, d):
        if path == "/api/auth/logout":
            token = self.parse_cookies().get(SESSION_COOKIE)
            if token:
                with db() as c:
                    c.execute("DELETE FROM sessions WHERE token=?", (token,))
            return self.send({"ok": True}, extra={"Set-Cookie": self.set_session_cookie("", clear=True)})

        email = str(d.get("email") or "").strip().lower()
        password = str(d.get("password") or "")
        shop_name = str(d.get("shop_name") or "").strip()
        if "@" not in email or "." not in email.split("@")[-1] or len(email) > 254:
            return self.send({"error": "adresse email invalide"}, code=400)

        if path == "/api/auth/register":
            if len(password) < 6:
                return self.send({"error": "mot de passe : 6 caractères minimum"}, code=400)
            name = shop_name or email.split("@")[0]
            try:
                with db() as c:
                    # Reprend la boutique locale existante (migration) si elle n'a pas encore de compte.
                    legacy = c.execute(
                        "SELECT id FROM shops WHERE email IS NULL AND password_hash IS NULL "
                        "AND google_sub IS NULL ORDER BY id LIMIT 1").fetchone()
                    if legacy:
                        c.execute("UPDATE shops SET name=?, email=?, password_hash=? WHERE id=?",
                                  (name, email, hash_password(password), legacy["id"]))
                        shop_id = legacy["id"]
                    else:
                        cursor = c.execute(
                            "INSERT INTO shops(name, email, password_hash, created) VALUES(?,?,?,?)",
                            (name, email, hash_password(password),
                             datetime.now().isoformat(timespec="seconds")))
                        shop_id = cursor.lastrowid
            except sqlite3.IntegrityError:
                return self.send({"error": "un compte existe déjà avec cet email"}, code=409)
            token = create_session(shop_id)
            return self.send({"ok": True, "name": name},
                             extra={"Set-Cookie": self.set_session_cookie(token)})

        # /api/auth/login
        with db() as c:
            row = c.execute(
                "SELECT id, name, password_hash FROM shops WHERE email=?", (email,)).fetchone()
        if not row or not row["password_hash"] or not verify_password(password, row["password_hash"]):
            return self.send({"error": "email ou mot de passe incorrect"}, code=401)
        token = create_session(row["id"])
        return self.send({"ok": True, "name": row["name"]},
                         extra={"Set-Cookie": self.set_session_cookie(token)})

    def google_callback(self, q):
        error = q.get("error", [None])[0]
        state = q.get("state", [None])[0]
        code = q.get("code", [None])[0]
        if error or not state or state not in GOOGLE_STATES:
            return self.redirect("/login?error=google-refuse")
        redirect_uri = GOOGLE_STATES.pop(state)["redirect_uri"]
        if not code or not google_enabled():
            return self.redirect("/login?error=google-non-configure")
        try:
            body = urllib.parse.urlencode({
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code"
            }).encode("utf-8")
            request = urllib.request.Request(
                "https://oauth2.googleapis.com/token", data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"})
            with urllib.request.urlopen(request, timeout=15) as response:
                token_data = json.loads(response.read())
            access_token = token_data["access_token"]
            request = urllib.request.Request(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {access_token}"})
            with urllib.request.urlopen(request, timeout=15) as response:
                profile = json.loads(response.read())
            google_sub = str(profile.get("sub") or "")
            email = str(profile.get("email") or "").strip().lower()
            name = str(profile.get("name") or profile.get("given_name") or email.split("@")[0])
            if not google_sub or not email:
                raise ValueError("profil Google incomplet")
        except Exception:
            traceback.print_exc()
            return self.redirect("/login?error=google-echec")

        now = datetime.now().isoformat(timespec="seconds")
        with db() as c:
            row = c.execute("SELECT id FROM shops WHERE google_sub=? OR email=?",
                            (google_sub, email)).fetchone()
            if row:
                shop_id = row["id"]
                c.execute("UPDATE shops SET google_sub=? WHERE id=? AND (google_sub IS NULL OR google_sub='')",
                          (google_sub, shop_id))
            else:
                legacy = c.execute(
                    "SELECT id FROM shops WHERE email IS NULL AND password_hash IS NULL "
                    "AND google_sub IS NULL ORDER BY id LIMIT 1").fetchone()
                if legacy:
                    shop_id = legacy["id"]
                    c.execute("UPDATE shops SET name=?, email=?, google_sub=? WHERE id=?",
                              (name, email, google_sub, shop_id))
                else:
                    cursor = c.execute(
                        "INSERT INTO shops(name, email, google_sub, created) VALUES(?,?,?,?)",
                        (name, email, google_sub, now))
                    shop_id = cursor.lastrowid
        token = create_session(shop_id)
        return self.redirect("/", extra={"Set-Cookie": self.set_session_cookie(token)})

def make_cert():
    cert, key = os.path.join(DIR, "cert.pem"), os.path.join(DIR, "key.pem")
    ca_key, ca_cert = ensure_local_ca()
    if certificate_matches_current_ip(cert, key, ca_cert):
        return cert, key
    import datetime
    from cryptography import x509
    import ipaddress
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.timezone.utc)
    server_name = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Stock local"),
        x509.NameAttribute(NameOID.COMMON_NAME, "localhost")
    ])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(server_name)
        .issuer_name(ca_cert.subject)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=825))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([
            x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.ip_address(IP))
        ]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=True,
            data_encipherment=False, key_agreement=False, key_cert_sign=False,
            crl_sign=False, encipher_only=None, decipher_only=None), critical=True)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                       critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    with open(key, "wb") as key_file:
        key_file.write(server_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()))
    with open(cert, "wb") as cert_file:
        cert_file.write(certificate.public_bytes(serialization.Encoding.PEM))
    return cert, key

if __name__ == "__main__":
    cert, key = make_cert()
    srv = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), H)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(cert, key)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=expiry_alert_loop, daemon=True).start()
    print(f"\n  PC (tableau de bord) : https://localhost:{PORT}")
    print(f"  Connexion / inscription : https://localhost:{PORT}/login")
    print(f"  Téléphone (scanner)  : https://{IP}:{PORT}/scan")
    print(f"  Autorité locale (à approuver une fois) : {CA_CERT_WINDOWS}")
    print(f"  Si nécessaire : certutil -user -addstore -f Root \"{CA_CERT_WINDOWS}\"")
    print(f"  Connexion Google : {'activée' if google_enabled() else 'non configurée (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET)'}")
    if google_enabled():
        print("    URI à autoriser dans Google Cloud Console (Clients OAuth) :")
        print(f"      {google_redirect_uri_for('localhost')}")
        print(f"      {google_redirect_uri()}")
    else:
        print(f"    Redirect URI à déclarer dans Google Cloud : {google_redirect_uri_for('localhost')}")
    print(f"  Alertes d'expiration: {ALERT_DAYS} jours avant la date limite")
    print("  Ctrl+C pour arrêter\n")
    srv.serve_forever()
