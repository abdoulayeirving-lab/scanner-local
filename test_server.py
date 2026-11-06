import http.server
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from datetime import date, timedelta
from urllib.error import HTTPError
from urllib.request import (HTTPRedirectHandler, Request, build_opener,
                            urlopen)
from unittest.mock import patch

import server


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


class ProductApiTests(unittest.TestCase):
    def setUp(self):
        self.original_db = server.DB
        self.temp_dir = tempfile.TemporaryDirectory()
        server.DB = os.path.join(self.temp_dir.name, "test.db")
        connection = sqlite3.connect(server.DB)
        try:
            connection.execute("""CREATE TABLE products(
                id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE, name TEXT,
                price REAL DEFAULT 0, qty INTEGER DEFAULT 0, updated TEXT)""")
            connection.execute(
                "INSERT INTO products(code,name,price,qty,updated) VALUES(?,?,?,?,?)",
                ("LEGACY", "Ancien produit", 100, 2, "2026-01-01 10:00:00"))
            connection.commit()
        finally:
            connection.close()
        server.initialize_database()
        with server.db() as c:
            self.shop_id = c.execute("SELECT id FROM shops ORDER BY id LIMIT 1").fetchone()["id"]
        self.cookie = "stock_session=" + server.create_session(self.shop_id)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.httpd.server_port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        server.DB = self.original_db
        self.temp_dir.cleanup()

    def open(self, path, payload=None, method=None, cookie=True, follow=True):
        data = None if payload is None else (
            payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8"))
        headers = {"Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = self.cookie
        request = Request(self.base_url + path, data=data, headers=headers,
                          method=method or ("POST" if data is not None else "GET"))
        if follow:
            return urlopen(request)
        return build_opener(_NoRedirect).open(request)

    def call(self, path, payload=None, method=None, cookie=True, follow=True):
        def parse(response, body):
            content_type = response.headers.get("Content-Type", "")
            return json.loads(body) if body and "json" in content_type else body

        try:
            with self.open(path, payload, method, cookie, follow) as response:
                return response.status, response.headers, parse(response, response.read())
        except HTTPError as error:
            with error:
                return error.code, error.headers, parse(error, error.read())

    def post_product(self, payload, cookie=True):
        status, _, result = self.call("/api/products", payload, cookie=cookie)
        return status, result

    def test_existing_database_is_migrated_without_losing_products(self):
        connection = sqlite3.connect(server.DB)
        try:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(products)")}
        finally:
            connection.close()
        self.assertIn("expiration_date", columns)
        self.assertIn("shop_id", columns)
        status, _, product = self.call("/api/product?code=LEGACY")
        self.assertEqual(status, 200)
        self.assertEqual(product["name"], "Ancien produit")
        self.assertIsNone(product["expiration_date"])
        self.assertEqual(product["shop_id"], self.shop_id)

    def test_product_date_and_french_price_are_saved(self):
        status, result = self.post_product({
            "code": "NEW-1", "name": "Huile", "price": "1 500,25",
            "qty": "2", "expiration_date": "2027-05-01"})
        self.assertEqual(status, 200)
        self.assertTrue(result["ok"])
        with self.open("/api/product?code=NEW-1") as response:
            product = json.loads(response.read())
        self.assertEqual(product["price"], 1500.25)
        self.assertEqual(product["qty"], 2)
        self.assertEqual(product["expiration_date"], "2027-05-01")

    def test_invalid_date_is_rejected_without_stopping_server(self):
        status, result = self.post_product({
            "code": "BAD-1", "qty": 1, "expiration_date": "31/12/2027"})
        self.assertEqual(status, 400)
        self.assertIn("invalide", result["error"])
        with self.open("/api/products") as response:
            self.assertEqual(response.status, 200)

    def test_expiry_push_is_sent_once_within_seven_days_and_after_expiry(self):
        soon = (date.today() + timedelta(days=7)).isoformat()
        expired = (date.today() - timedelta(days=1)).isoformat()
        with server.db() as connection:
            connection.executemany(
                "INSERT INTO products(shop_id,code,name,price,qty,updated,expiration_date) "
                "VALUES(?,?,?,?,?,?,?)",
                [(self.shop_id, "SOON", "Produit bientôt périmé", 10, 1, "2026-01-01", soon),
                 (self.shop_id, "EXPIRED", "Produit périmé", 10, 1, "2026-01-01", expired)])
            connection.execute(
                "INSERT INTO push_subscriptions(endpoint,subscription_json,created,shop_id) "
                "VALUES(?,?,?,?)",
                ("https://push.example/subscription", "{}", "2026-01-01", self.shop_id))

        with patch("server.get_push_public_key"), patch("pywebpush.webpush") as webpush:
            server.send_expiry_pushes()
            self.assertEqual(webpush.call_count, 2)
            payloads = [json.loads(call.kwargs["data"]) for call in webpush.call_args_list]
            self.assertTrue(any("7 jour" in payload["body"] for payload in payloads))
            self.assertTrue(any(payload["title"] == "Produit expiré" for payload in payloads))
            server.send_expiry_pushes()
            self.assertEqual(webpush.call_count, 2)

    def test_push_subscription_can_be_registered_and_removed(self):
        subscription = {
            "endpoint": "https://push.example/device",
            "keys": {"p256dh": "public-key", "auth": "auth-key"}
        }
        with patch("server.get_push_public_key"):
            status, _, _ = self.call("/api/push-subscriptions",
                                     {"subscription": subscription})
            self.assertEqual(status, 200)
        with server.db() as connection:
            row = connection.execute(
                "SELECT shop_id FROM push_subscriptions").fetchone()
            self.assertEqual(row[0], self.shop_id)

        status, _, _ = self.call("/api/push-subscriptions",
                                 {"endpoint": subscription["endpoint"]}, method="DELETE")
        self.assertEqual(status, 200)
        with server.db() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM push_subscriptions").fetchone()[0], 0)

    def test_remote_scan_can_only_be_enabled_from_local_dashboard(self):
        status, _, state = self.call("/api/remote-scan")
        self.assertFalse(state["enabled"])

        status, _, state = self.call("/api/remote-scan", {"enabled": True})
        self.assertTrue(state["enabled"])
        self.assertTrue(server.remote_scan_is_enabled(self.shop_id))

        with self.open("/scan") as response:
            self.assertEqual(response.status, 200)

        status, _, state = self.call("/api/remote-scan", {"enabled": False})
        self.assertFalse(state["enabled"])
        self.assertFalse(server.remote_scan_is_enabled(self.shop_id))

    def test_remote_scanner_is_rejected_until_enabled(self):
        class RemoteProbe:
            client_address = ("192.168.1.50", 50000)

            def send(self, body, code=200, extra=None):
                self.response = (body, code)

        probe = RemoteProbe()
        probe.is_local_client = server.H.is_local_client.__get__(probe, RemoteProbe)
        probe.require_remote_scan = server.H.require_remote_scan.__get__(probe, RemoteProbe)

        self.assertFalse(probe.require_remote_scan(self.shop_id))
        self.assertEqual(probe.response[1], 403)
        server.set_remote_scan_enabled(self.shop_id, True)
        self.assertTrue(probe.require_remote_scan(self.shop_id))

    def test_pages_require_an_account(self):
        status, headers, _ = self.call("/", cookie=False, follow=False)
        self.assertEqual(status, 302)
        self.assertEqual(headers["Location"], "/login")
        status, _, _ = self.call("/api/products", cookie=False)
        self.assertEqual(status, 401)
        status, _, _ = self.call("/api/products")
        self.assertEqual(status, 200)

    def test_register_login_logout_and_isolation_between_shops(self):
        status, headers, result = self.call(
            "/api/auth/register",
            {"shop_name": "Boutique A", "email": "a@example.com", "password": "secret1"},
            cookie=False)
        self.assertEqual(status, 200)
        self.assertIn("stock_session=", headers["Set-Cookie"])
        self.assertEqual(result["name"], "Boutique A")

        status, _, _ = self.call("/api/auth/register", {
            "shop_name": "Doublon", "email": "a@example.com", "password": "secret1"},
            cookie=False)
        self.assertEqual(status, 409)

        status, _, _ = self.call("/api/auth/login",
                                 {"email": "a@example.com", "password": "mauvais"},
                                 cookie=False)
        self.assertEqual(status, 401)

        status, headers, _ = self.call("/api/auth/login",
                                       {"email": "a@example.com", "password": "secret1"},
                                       cookie=False)
        self.assertEqual(status, 200)
        cookie_a = headers["Set-Cookie"].split(";", 1)[0]

        status, headers, _ = self.call(
            "/api/auth/register",
            {"shop_name": "Boutique B", "email": "b@example.com", "password": "secret2"},
            cookie=False)
        self.assertEqual(status, 200)
        cookie_b = headers["Set-Cookie"].split(";", 1)[0]
        self.assertNotEqual(cookie_a, cookie_b)

        status, _, _ = self.call("/api/products", {"code": "SHARED", "qty": 1,
                                                   "name": "Chez A"},
                                 cookie=False)
        self.assertEqual(status, 401)
        self.cookie = cookie_a
        self.assertEqual(self.post_product({"code": "SHARED", "qty": 1, "name": "Chez A"})[0], 200)
        self.cookie = cookie_b
        status, _, products = self.call("/api/products")
        self.assertEqual(status, 200)
        self.assertEqual(products, [])
        self.assertEqual(self.post_product({"code": "SHARED", "qty": 2, "name": "Chez B"})[0], 200)
        status, _, products_b = self.call("/api/products")
        self.assertEqual([(p["name"], p["qty"]) for p in products_b], [("Chez B", 2)])

        self.cookie = cookie_a
        status, _, products_a = self.call("/api/products")
        names_a = {(p["name"], p["qty"]) for p in products_a}
        self.assertIn(("Chez A", 1), names_a)
        self.assertNotIn(("Chez B", 2), names_a)
        self.assertEqual(self.call("/api/products/" + str(products_b[0]["id"]),
                                   method="DELETE")[0], 404)

        status, _, _ = self.call("/api/auth/logout", {})
        self.assertEqual(status, 200)
        self.assertEqual(self.call("/api/products")[0], 401)

    def test_google_login_is_disabled_without_credentials(self):
        with patch.multiple(server, GOOGLE_CLIENT_ID="", GOOGLE_CLIENT_SECRET=""):
            status, headers, _ = self.call("/api/auth/google", cookie=False, follow=False)
            self.assertEqual(status, 302)
            self.assertIn("google-non-configure", headers["Location"])
            status, _, config = self.call("/api/auth/google-config", cookie=False)
            self.assertEqual(status, 200)
            self.assertIn("enabled", config)

    def test_google_redirect_uri_depends_on_browsed_host(self):
        self.assertEqual(server.google_redirect_uri_for("localhost:8443"),
                         f"https://localhost:{server.PORT}/auth/google/callback")
        self.assertEqual(server.google_redirect_uri_for("127.0.0.1:8443"),
                         f"https://localhost:{server.PORT}/auth/google/callback")
        self.assertEqual(server.google_redirect_uri_for("192.168.1.6:8443"),
                         f"https://192-168-1-6.nip.io:{server.PORT}/auth/google/callback")
        with patch.object(server, "GOOGLE_REDIRECT_URI", "https://stock.example.com/cb"):
            self.assertEqual(server.google_redirect_uri_for("192.168.1.6:8443"),
                             "https://stock.example.com/cb")

    def test_env_file_is_loaded_without_overriding_and_ignores_empty_values(self):
        with self.subTest("valeurs vides ignorées"):
            handle = tempfile.NamedTemporaryFile("w", suffix=".env", delete=False,
                                                 encoding="utf-8")
            handle.write("# commentaire\nGOOGLE_CLIENT_SECRET_VIDE=\n")
            handle.close()
            try:
                os.environ.pop("GOOGLE_CLIENT_SECRET_VIDE", None)
                server.load_env_file(handle.name)
                self.assertNotIn("GOOGLE_CLIENT_SECRET_VIDE", os.environ)
            finally:
                os.remove(handle.name)
        with self.subTest("variables existantes prioritaires"):
            handle = tempfile.NamedTemporaryFile("w", suffix=".env", delete=False,
                                                 encoding="utf-8")
            handle.write('GOOGLE_CLIENT_ID="depuis-fichier"\n')
            handle.close()
            previous = os.environ.get("GOOGLE_CLIENT_ID")
            os.environ["GOOGLE_CLIENT_ID"] = "depuis-environnement"
            try:
                server.load_env_file(handle.name)
                self.assertEqual(os.environ["GOOGLE_CLIENT_ID"], "depuis-environnement")
            finally:
                os.remove(handle.name)
                if previous is None:
                    os.environ.pop("GOOGLE_CLIENT_ID", None)
                else:
                    os.environ["GOOGLE_CLIENT_ID"] = previous

    def test_offline_app_shell_and_scanner_libraries_are_served_locally(self):
        for path, content_type in (
            ("/manifest.webmanifest", "application/manifest+json"),
            ("/offline.html", "text/html"),
            ("/icon.svg", "image/svg+xml"),
            ("/vendor/qrcode.min.js", "application/javascript"),
            ("/vendor/html5-qrcode.min.js", "application/javascript"),
            ("/auth.js", "application/javascript"),
        ):
            with self.open(path) as response:
                self.assertEqual(response.status, 200)
                self.assertIn(content_type, response.headers["Content-Type"])
                self.assertGreater(len(response.read()), 0)

        with self.open("/sw.js") as response:
            worker = response.read().decode()
        self.assertIn("cache.addAll(SHELL_FILES)", worker)
        self.assertIn("'/scan'", worker)
        self.assertIn("caches.match(request)", worker)
        self.assertIn("/vendor/html5-qrcode.min.js", worker)
        with self.open("/") as response:
            dashboard = response.read().decode()
        with self.open("/scan") as response:
            scanner = response.read().decode()
        with self.open("/login", cookie=False) as response:
            login = response.read().decode()
        for page in (dashboard, scanner):
            self.assertNotIn("cdnjs.cloudflare.com", page)
            self.assertNotIn("fonts.googleapis.com", page)
        self.assertIn("/vendor/qrcode.min.js", dashboard)
        self.assertIn("/vendor/html5-qrcode.min.js", scanner)
        self.assertIn("id=\"splash\"", dashboard)
        self.assertIn("Ouverture de Stock", dashboard)
        self.assertIn("id=\"scanner-start\"", scanner)
        self.assertIn("Activation de la caméra", scanner)
        self.assertIn("Continuer avec Google", login)

    def test_https_ca_is_restricted_to_local_names_and_private_networks(self):
        from cryptography import x509
        server.ensure_local_ca()
        with open(server.CA_CERT, "rb") as ca_file:
            ca_certificate = x509.load_pem_x509_certificate(ca_file.read())
        constraints = ca_certificate.extensions.get_extension_for_class(
            x509.NameConstraints).value
        permitted_dns = [name.value for name in constraints.permitted_subtrees
                         if isinstance(name, x509.DNSName)]
        permitted_ips = [str(network.value) for network in constraints.permitted_subtrees
                         if isinstance(network, x509.IPAddress)]
        self.assertEqual(permitted_dns, ["localhost"])
        self.assertIn("192.168.0.0/16", permitted_ips)
        self.assertIn("10.0.0.0/8", permitted_ips)

    def test_https_certificate_is_signed_by_the_local_ca_for_local_addresses(self):
        from cryptography import x509
        cert_path, key_path = server.make_cert()
        with open(cert_path, "rb") as cert_file:
            certificate = x509.load_pem_x509_certificate(cert_file.read())
        with open(server.CA_CERT, "rb") as ca_file:
            ca_certificate = x509.load_pem_x509_certificate(ca_file.read())

        certificate.verify_directly_issued_by(ca_certificate)
        self.assertTrue(server.certificate_matches_current_ip(
            cert_path, key_path, ca_certificate))
        names = certificate.extensions.get_extension_for_class(
            x509.SubjectAlternativeName).value
        self.assertIn("localhost", names.get_values_for_type(x509.DNSName))
        self.assertIn(server.IP, [str(address) for address in
                                  names.get_values_for_type(x509.IPAddress)])
        self.assertFalse(certificate.extensions.get_extension_for_class(
            x509.BasicConstraints).value.ca)


if __name__ == "__main__":
    unittest.main()
