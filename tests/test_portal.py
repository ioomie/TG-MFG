import io
import json
import sys
import tempfile
import threading
import unittest
import zipfile
from http.client import HTTPConnection
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from portal import PortalServer


class PortalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        for name in ("TG-MFG-Windows-x64.zip", "TG-MFG-Mac.zip"):
            with zipfile.ZipFile(Path(cls.directory.name) / name, "w") as z:
                z.writestr("TG-MFG/run.bat", "test fixture")
        cls.server = PortalServer(("127.0.0.1", 0), "https://tg.example.com", cls.directory.name)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.directory.cleanup()

    def request(self, path, agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)", method="GET", host=None):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        headers = {"User-Agent": agent}
        if host:
            headers["Host"] = host
        connection.request(method, path, headers=headers)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_page_has_no_session_and_initially_hides_credentials(self):
        status, headers, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b'name="app-token" content=""', body)
        self.assertIn(b'name="core-mode" content="portal"', body)
        self.assertIn(b'id="workspace" class="workspace" hidden', body)
        self.assertIn("http://127.0.0.1:8765", headers["Content-Security-Policy"])

    def test_english_default_and_language_assets(self):
        status, _, body = self.request('/')
        self.assertEqual(status, 200)
        self.assertIn(b'<html lang="en">', body)
        self.assertIn(b'id="languageSelect"', body)
        for path in ('/i18n.js', '/translations.js'):
            status, headers, body = self.request(path)
            self.assertEqual(status, 200)
            self.assertIn('text/javascript', headers['Content-Type'])
        status, _, body = self.request('/downloads/windows', agent='Android')
        self.assertEqual(status, 403)
        self.assertEqual(body.decode(), 'Core downloads are available only for Mac and Windows computers.')

    def test_desktop_download_has_matching_site_configuration(self):
        for path, agent in (("windows", "Windows NT 10.0"), ("mac", "Macintosh; Intel Mac OS X")):
            status, headers, body = self.request(f"/downloads/{path}", agent)
            self.assertEqual(status, 200)
            self.assertEqual(headers["Content-Type"], "application/zip")
            with zipfile.ZipFile(io.BytesIO(body)) as z:
                self.assertEqual(json.loads(z.read("TG-MFG/core-settings.json"))["portal_origin"], self.server.site_origin)
        status, _, body = self.request("/downloads/windows?origin=https%3A%2F%2Fportal.example", host="portal.example")
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            self.assertEqual(json.loads(z.read("TG-MFG/core-settings.json"))["portal_origin"], "https://portal.example")
        self.assertEqual(self.request("/downloads/windows?origin=https%3A%2F%2Fportal.example%2Fbad")[0], 400)
        self.assertEqual(self.request("/downloads/windows?origin=https%3A%2F%2Fevil.example")[0], 400)

    def test_other_platforms_and_files_are_not_provided(self):
        for agent in ("Linux x86_64", "iPhone Mac OS X", "iPad Mac OS X", "Android", ""):
            for platform in ("mac", "windows"):
                self.assertEqual(self.request(f"/downloads/{platform}", agent)[0], 403)
        for path in ("/app.py", "/portal.py", "/core-settings.json", "/downloads/linux", "/../app.py", "/tls/server.key", "/api/status"):
            self.assertEqual(self.request(path)[0], 404)
        self.assertEqual(self.request("/api/connect", method="POST")[0], 404)


if __name__ == "__main__":
    unittest.main()
