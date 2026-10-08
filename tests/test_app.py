import asyncio
import json
import re
import sys
import threading
import unittest
from datetime import timedelta
from http.client import HTTPConnection
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import AppError, LocalServer, Runtime, TelegramService, parse_date, public_error, serialize_message, validate_portal_origin
from telethon import errors, types
from telethon.sessions import MemorySession
from fakes import FakeClient, NOW, channel, message


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service = TelegramService(FakeClient)

    async def asyncTearDown(self):
        await self.service._disconnect()

    async def login(self):
        await self.service.connect({"api_id": 123, "api_hash": "a" * 32})
        await self.service.send_code({"phone": "+8613800000000"})
        self.assertEqual((await self.service.sign_in({"code": "12345"}))["stage"], "password")
        await self.service.sign_in({"password": "test-password"})
        await self.service.list_channels()

    async def scan(self, **options):
        await self.service.start_scan({"channel_id": "101", "limit": 500, **options})
        await self.service.scan_task
        return await self.service.results()

    async def test_block_restore_refresh_rescan_and_channel_isolation(self):
        await self.login()
        before = await self.scan()
        row = before["messages"][0]
        original_requests = list(self.service.client.requests)
        data = {"channel_id":"101", "message_id":row["id"], "blocked":True}
        await self.service.set_message_block(data)
        result = await self.service.results()
        self.assertTrue(result["messages"][0]["blocked"])
        self.assertEqual(result["blocked_messages"][0]["text"], row["text"])
        self.assertEqual((await self.service.results())["blocked_messages"], result["blocked_messages"])
        self.assertTrue((await self.scan())["messages"][0]["blocked"])
        other = await self.scan(channel_id="102")
        self.assertFalse(other["messages"][0]["blocked"])
        await self.service.set_message_block({**data, "channel_id":"102"})
        self.assertEqual(len((await self.service.results())["blocked_messages"]), 2)
        # Restore the previous channel even though its rows are no longer loaded.
        await self.service.set_message_block({**data, "blocked":False})
        self.assertTrue((await self.service.results())["messages"][0]["blocked"])
        again = await self.scan()
        self.assertFalse(again["messages"][0]["blocked"])
        self.assertEqual(again["blocked_messages"][0]["channel_id"], "102")
        self.assertEqual(self.service.client.requests, original_requests)
        self.service.client.logout_failure = True
        with self.assertRaises(AppError):
            await self.service.disconnect()
        self.assertEqual(self.service.blocked_messages, {})

    async def test_block_requires_auth_valid_message_and_completed_scan(self):
        valid = {"channel_id":"101", "message_id":1000, "blocked":True}
        with self.assertRaises(AppError):
            await self.service.set_message_block(valid)
        await self.login()
        await self.scan()
        for changed in ({"message_id":True}, {"message_id":0}, {"message_id":"1000"},
                        {"blocked":"true"}, {"channel_id":101}, {"channel_id":"102"}, {"message_id":123456}):
            with self.subTest(changed=changed), self.assertRaises(AppError):
                await self.service.set_message_block({**valid, **changed})
        self.service.client.delay = .01
        await self.service.start_scan({"channel_id":"101", "limit":500})
        with self.assertRaises(AppError) as raised:
            await self.service.set_message_block(valid)
        self.assertEqual(raised.exception.status, 409)
        await self.service.cancel_scan()

    async def test_full_login_memory_and_revoke(self):
        await self.login()
        client = self.service.client
        self.assertIsInstance(client.args[0], MemorySession)
        self.assertFalse(client.kwargs["receive_updates"])
        self.assertEqual(self.service.stage, "ready")
        self.assertIsNone(self.service.phone)
        await self.service.disconnect()
        self.assertTrue(client.revoked)
        self.assertIsNone(self.service.client)
        self.assertEqual(self.service.channels, {})

    async def test_unauthorized_reads_and_invalid_keys(self):
        with self.assertRaises(AppError):
            await self.service.list_channels()
        for data in ({}, {"api_id": -1, "api_hash": "a" * 32}, {"api_id": 12, "api_hash": "bad"}):
            with self.assertRaises(AppError):
                await self.service.connect(data)
        self.assertIsNone(self.service.client)

    async def test_incorrect_code_password_and_resend_after_refresh(self):
        await self.service.connect({"api_id": 123, "api_hash": "a" * 32})
        await self.service.send_code({"phone": "+8613800000000"})
        await self.service.send_code({"phone": ""})
        with self.assertRaises(errors.PhoneCodeInvalidError):
            await self.service.sign_in({"code": "99999"})
        self.assertEqual(self.service.stage, "code")
        await self.service.sign_in({"code": "12345"})
        with self.assertRaises(errors.PasswordHashInvalidError):
            await self.service.sign_in({"password": "wrong"})
        self.assertEqual(self.service.stage, "password")
        await self.service.sign_in({"password": "test-password"})
        self.assertEqual(self.service.stage, "ready")

    async def test_only_joined_broadcast_channels_including_archive(self):
        await self.login()
        result = await self.service.list_channels()
        self.assertEqual({c["id"] for c in result["channels"]}, {"101", "102", "103"})

    async def test_channel_count_is_one_bounded_request_without_scanning(self):
        await self.login()
        result = await self.service.channel_count({"channel_id":"101"})
        self.assertEqual(result, {"channel_id":"101", "count":80000, "inexact":False})
        self.assertEqual(len(self.service.client.requests), 1)
        self.assertEqual(self.service.client.requests[0].limit, 1)
        self.assertFalse(hasattr(self.service.client, "history_args"))
        self.assertEqual(self.service.rows, [])
        self.assertEqual(self.service.scan["status"], "idle")

    async def test_channel_count_zero_inexact_and_existing_results(self):
        await self.login()
        previous = await self.scan()
        self.service.client.total_count = 0
        self.service.client.count_inexact = True
        result = await self.service.channel_count({"channel_id":"101"})
        self.assertEqual(result["count"], 0)
        self.assertTrue(result["inexact"])
        self.assertEqual(await self.service.results(), previous)

    async def test_channel_count_auth_channel_and_failure(self):
        with self.assertRaises(AppError):
            await self.service.channel_count({"channel_id":"101"})
        await self.login()
        with self.assertRaises(AppError):
            await self.service.channel_count({"channel_id":"unjoined"})
        self.assertEqual(self.service.client.requests, [])
        self.service.client.count_failure = errors.FloodWaitError(request=None, capture=17)
        with self.assertRaises(errors.FloodWaitError):
            await self.service.channel_count({"channel_id":"101"})
        self.assertEqual(self.service.rows, [])

    async def test_hashtag_entities_use_utf16_offsets(self):
        row = serialize_message(message(1, "😀 #设计 #AI #设计 https://example.org/#fragment C#"), channel())
        self.assertEqual(row["hashtags"], ["#设计", "#AI"])
        without_entities = serialize_message(message(2, "#plain", entities=[]), channel())
        self.assertEqual(without_entities["hashtags"], [])

    async def test_message_reactions_normalization_private_links_and_paid(self):
        row = serialize_message(message(42, reactions={"❤": 3, "❤️": 5, "🔥": 2, "paid": 1000, "custom:888": 7}), channel(username=None))
        self.assertEqual(row["reactions"], {"❤": 8, "🔥": 2, "custom:888": 7})
        self.assertEqual(row["total"], 17)
        self.assertEqual(row["link"], "https://t.me/c/101/42")

    async def test_date_range_and_html_retained_as_text(self):
        await self.login()
        result = await self.scan(start=(NOW - timedelta(days=3)).isoformat(), end=(NOW + timedelta(hours=1)).isoformat())
        self.assertEqual(result["scan"]["status"], "done")
        self.assertEqual(len(result["messages"]), 4)
        self.assertIn("<img", result["messages"][0]["text"])
        self.assertEqual(self.service.client.history_args["offset_date"], NOW + timedelta(hours=1))

    async def test_cancel_preserves_partial_and_rejects_second_scan(self):
        await self.login()
        self.service.client.delay = .01
        await self.service.start_scan({"channel_id": "101", "limit": 500})
        with self.assertRaises(AppError):
            await self.service.start_scan({"channel_id": "102", "limit": 500})
        await asyncio.sleep(.025)
        result = await self.service.cancel_scan()
        self.assertEqual(result["status"], "cancelled")
        self.assertGreater(len(self.service.rows), 0)
        self.assertLess(len(self.service.rows), 80)

    async def test_flood_wait_preserves_partial(self):
        await self.login()
        self.service.client.failure = errors.FloodWaitError(request=None, capture=33)
        result = await self.scan()
        self.assertEqual(result["scan"]["status"], "error")
        self.assertIn("33", result["scan"]["error"])
        self.assertEqual(len(result["messages"]), 1)

    async def test_cancel_before_scan_coroutine_starts(self):
        await self.login()
        await self.service.start_scan({"channel_id": "101", "limit": 500})
        result = await self.service.cancel_scan()
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["count"], 0)
        await self.service.start_scan({"channel_id": "102", "limit": 500})
        await self.service.scan_task
        self.assertEqual(self.service.scan["status"], "done")

    async def test_scan_limit_and_all_history(self):
        await self.login()
        self.service.client.messages = [message(1000 - i, days=i) for i in range(501)]
        limited = await self.scan(limit=500)
        self.assertEqual(len(limited["messages"]), 500)
        all_rows = await self.scan(limit=0)
        self.assertEqual(len(all_rows["messages"]), 501)

    async def test_failed_logout_still_clears_memory(self):
        await self.login()
        self.service.client.logout_failure = True
        with self.assertRaises(AppError):
            await self.service.disconnect()
        self.assertIsNone(self.service.client)
        self.assertEqual(self.service.stage, "disconnected")

    async def test_invalid_dates_and_channel(self):
        await self.login()
        for options in ({"channel_id": "evil"}, {"limit": -1}, {"start": "2026-10-08"},
                        {"start": NOW.isoformat(), "end": NOW.isoformat()}):
            with self.assertRaises(AppError):
                await self.scan(**options)
        with self.assertRaises(AppError):
            parse_date("2026-10-08")


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = LocalServer(0, Runtime(FakeClient))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server.runtime.close()
        cls.thread.join()

    def request(self, path, method="GET", body=None, headers=None):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_block_endpoint_requires_token_and_rejects_foreign_origin(self):
        body = json.dumps({"channel_id":"101", "message_id":1000, "blocked":True})
        headers = {"Content-Type":"application/json"}
        self.assertEqual(self.request("/api/message-block", "POST", body, headers)[0], 403)
        headers["X-App-Token"] = self.server.token
        self.assertEqual(self.request("/api/message-block", "POST", body, {**headers, "Origin":"https://evil.example"})[0], 403)
        # Valid origin/token reaches the authenticated service, rather than a missing route.
        self.assertEqual(self.request("/api/message-block", "POST", body, headers)[0], 401)

    def test_language_modules_are_served_and_errors_ignore_browser_language(self):
        for path in ('/i18n.js', '/translations.js'):
            status, headers, body = self.request(path)
            self.assertEqual(status, 200)
            self.assertIn('text/javascript', headers['Content-Type'])
            self.assertTrue(body)
        status, _, body = self.request('/api/status', headers={'Accept-Language':'zh-CN'})
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)['error'], 'Invalid page session. Refresh this page.')

    def test_local_root_and_policy(self):
        status, headers, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(self.server.token.encode(), body)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_tokens_host_origin_and_cross_site(self):
        for headers in ({}, {"X-App-Token": "wrong"},
                        {"X-App-Token": self.server.token, "Host": "evil.example"},
                        {"X-App-Token": self.server.token, "Origin": "https://evil.example"},
                        {"X-App-Token": self.server.token, "Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.request("/api/status", headers=headers)[0], 403)
        self.assertEqual(self.request("/", headers={"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.request("/api/status", headers={"X-App-Token": self.server.token})[0], 200)

    def test_cannot_access_files_or_post_other_formats(self):
        for path in ("/app.py", "/requirements.txt", "/../app.py", "/web/index.html", "/.env"):
            self.assertEqual(self.request(path)[0], 404)
        headers = {"X-App-Token": self.server.token}
        self.assertEqual(self.request("/api/connect", "POST", "{}", headers)[0], 415)
        headers["Content-Type"] = "application/json"
        self.assertEqual(self.request("/api/connect", "POST", "[]", headers)[0], 400)
        self.assertEqual(self.request("/api/connect", "POST", "{bad}", headers)[0], 400)
        self.assertEqual(self.request("/api/connect", "POST", "a" * 17000, headers)[0], 413)


class PortalCoreHTTPTests(HTTPTests):
    @classmethod
    def setUpClass(cls):
        cls.server = LocalServer(0, Runtime(FakeClient), portal_origin="https://tg.example.com")
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    def test_configured_website_bootstrap_and_api(self):
        headers = {"Origin": self.server.portal_origin, "Sec-Fetch-Site": "cross-site"}
        status, response_headers, body = self.request("/api/bootstrap", headers=headers)
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["application"], "TG-MFG")
        self.assertEqual(data["token"], self.server.token)
        self.assertEqual(response_headers["Access-Control-Allow-Origin"], self.server.portal_origin)
        # Website origin alone is insufficient for any account API operation.
        self.assertEqual(self.request("/api/status", headers=headers)[0], 403)
        headers["X-App-Token"] = data["token"]
        self.assertEqual(self.request("/api/status", headers=headers)[0], 200)
        for origin in ("https://evil.example", "null", "http://tg.example.com"):
            status, returned, _ = self.request("/api/bootstrap", headers={"Origin": origin})
            self.assertEqual(status, 403)
            self.assertNotIn("Access-Control-Allow-Origin", returned)

    def test_website_preflight_and_private_network(self):
        headers = {"Origin": self.server.portal_origin, "Sec-Fetch-Site": "cross-site",
                   "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "x-app-token,content-type",
                   "Access-Control-Request-Private-Network": "true"}
        status, returned, body = self.request("/api/connect", "OPTIONS", headers=headers)
        self.assertEqual(status, 204)
        self.assertEqual(body, b"")
        self.assertEqual(returned["Access-Control-Allow-Private-Network"], "true")
        self.assertEqual(returned["Access-Control-Allow-Origin"], self.server.portal_origin)
        for changed in ({"Origin": "https://evil.example"}, {"Access-Control-Request-Method": "DELETE"},
                        {"Access-Control-Request-Headers": "authorization"}):
            self.assertEqual(self.request("/api/connect", "OPTIONS", headers={**headers, **changed})[0], 403)

    def test_origin_validation(self):
        self.assertEqual(validate_portal_origin("https://portal.example/"), "https://portal.example")
        for origin in ("*", "null", "file:///tmp", "https://portal.example/path", "https://u:p@portal.example", "https://portal.example?x=1"):
            with self.assertRaises(ValueError):
                validate_portal_origin(origin)


class CoreShutdownTests(unittest.TestCase):
    def test_shutdown_requires_token_and_clears_session_before_exit(self):
        receipt = {"managed": True, "platform": "windows", "version": 1, "id": "test-install"}
        server = LocalServer(0, Runtime(FakeClient), installation=receipt)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(path, method="GET", headers=None, body=None):
            conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            conn.request(method, path, headers=headers or {}, body=body)
            response = conn.getresponse()
            status, data = response.status, response.read()
            conn.close()
            return status, json.loads(data)
        try:
            self.assertEqual(request("/api/bootstrap")[1]["installation"], receipt)
            headers = {"Content-Type": "application/json"}
            self.assertEqual(request("/api/shutdown", "POST", headers, "{}")[0], 403)
            self.assertTrue(thread.is_alive())
            server.runtime.call("connect", {"api_id": 123, "api_hash": "a" * 32})
            server.runtime.call("send_code", {"phone": "+8613800000000"})
            server.runtime.call("sign_in", {"code": "12345"})
            server.runtime.call("sign_in", {"password": "test-password"})
            client = server.runtime.service.client
            headers["X-App-Token"] = server.token
            self.assertTrue(request("/api/shutdown", "POST", headers, "{}")[1]["stopped"])
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
            self.assertTrue(client.revoked)
            self.assertIsNone(server.runtime.service.client)
        finally:
            server.shutdown()
            server.server_close()
            server.runtime.close()


if __name__ == "__main__":
    unittest.main()
