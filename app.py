#!/usr/bin/env python3
"""A local, single-account, read-only Telegram reaction browser."""
import argparse
import asyncio
import concurrent.futures
import contextlib
import json
import logging
import re
import secrets
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from telethon import TelegramClient, errors, functions, types
from telethon.sessions import MemorySession, StringSession
from storage import LocalStore, StorageError
from diagnostics import CORE_VERSION, Diagnostics, ROUTES, error_fields
from network_proxy import parse_proxy, probe_proxy

ROOT = Path(__file__).resolve().parent
SCAN_INTERVALS = (0.5, 1, 2, 5, 10, 30)


class AppError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def parse_scan_interval(data):
    value = data.get("request_interval", 1)
    if type(value) not in (int, float) or value not in SCAN_INTERVALS:
        raise AppError("Choose a supported batch interval: 0.5, 1, 2, 5, 10 or 30 seconds.")
    return float(value)


def public_error(exc):
    if isinstance(exc, (AppError, StorageError)):
        return str(exc)
    if isinstance(exc, errors.FloodWaitError):
        return f"Telegram requires a wait of {exc.seconds} seconds. Try again later."
    messages = {
        "ApiIdInvalidError": "Invalid API ID or API Hash.",
        "PhoneNumberInvalidError": "Invalid phone number. Include the country code.",
        "PhoneCodeInvalidError": "Invalid login code. Enter it again.",
        "PhoneCodeExpiredError": "The login code has expired. Request a new code.",
        "PasswordHashInvalidError": "Invalid two-step verification password.",
        "PhoneNumberBannedError": "Telegram has banned this phone number from signing in.",
        "PhoneNumberUnoccupiedError": "This phone number is not registered with Telegram. Register using the official app first.",
        "SessionPasswordNeededError": "Enter your two-step verification password.",
        "AuthRestartError": "Telegram requires a fresh login. Request a new code.",
        "ChannelPrivateError": "This account cannot access the channel.",
        "AuthKeyUnregisteredError": "The login session is no longer valid. Disconnect and sign in again.",
        "SessionRevokedError": "The login session was revoked. Sign in again.",
    }
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "Connection timed out. Check your network or proxy and try again."
    if type(exc).__name__ == 'ProxyTimeoutError':
        return 'Proxy connection or handshake timed out. Check the proxy type, port and software.'
    if type(exc).__name__ == 'ProxyConnectionError':
        return 'Cannot connect to the proxy. Check that it is running and listening on the configured host and port.'
    if type(exc).__name__ == 'ProxyError':
        return 'Proxy handshake failed or the proxy refused the destination. Check that the port supports the selected SOCKS5 / HTTP CONNECT protocol.'
    if isinstance(exc, (OSError, ConnectionError)):
        return "Cannot connect to Telegram. Check your network or proxy."
    return messages.get(type(exc).__name__, "Operation failed. Try again or sign in again.")


def error_status(exc):
    if isinstance(exc, AppError):
        return exc.status
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or type(exc).__name__ == 'ProxyTimeoutError':
        return 504
    if isinstance(exc, (OSError, ConnectionError)) or type(exc).__name__ in ('ProxyConnectionError','ProxyError'):
        return 503
    return 400


def parse_date(value):
    if not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError()
        return result.astimezone(timezone.utc)
    except (ValueError, AttributeError):
        raise AppError("Invalid date format.")


def serialize_message(message, channel):
    reactions = {}
    for item in getattr(getattr(message, "reactions", None), "results", []) or []:
        reaction = item.reaction
        if isinstance(reaction, types.ReactionEmoji):
            key = reaction.emoticon.replace("\ufe0f", "")
        elif isinstance(reaction, types.ReactionCustomEmoji):
            key = f"custom:{reaction.document_id}"
        else:
            # Paid Stars are a monetary counter, not ordinary emoji reactions.
            continue
        reactions[key] = reactions.get(key, 0) + int(item.count)
    username = getattr(channel, "username", None)
    base = username if username else f"c/{channel.id}"
    text = message.message or ""
    utf16 = text.encode("utf-16-le")
    hashtags = []
    for entity in getattr(message, "entities", None) or []:
        if isinstance(entity, types.MessageEntityHashtag):
            tag = utf16[entity.offset * 2:(entity.offset + entity.length) * 2].decode("utf-16-le", errors="replace")
            if tag.startswith("#") and tag not in hashtags:
                hashtags.append(tag)
    return {
        "id": message.id,
        "date": message.date.isoformat(),
        "text": text,
        "hashtags": hashtags,
        "media": bool(getattr(message, "media", None)),
        "edited_at": message.edit_date.isoformat() if getattr(message, "edit_date", None) else None,
        "reactions": reactions,
        "total": sum(reactions.values()),
        "link": f"https://t.me/{base}/{message.id}",
    }


class TelegramService:
    def __init__(self, factory=TelegramClient, diagnostics=None, store=None):
        self.store = store
        self.account_id = ""
        self.credentials = None
        self.persistence_error = ""
        self.restore_task = None
        self.cached = {}
        self.factory = factory
        self.diagnostics = diagnostics or Diagnostics()
        self.client = None
        self.stage = "disconnected"
        self.phone = None
        self.phone_code_hash = None
        self.channels = {}
        self.rows = []
        self.blocked_messages = {}  # (channel ID, message ID) -> reversible in-memory snapshot
        self.scan_task = None
        self.scan = {"status": "idle", "checked": 0, "count": 0, "error": ""}
        self.auth_lock = asyncio.Lock()
        self.channel_lock = asyncio.Lock()

    async def status(self):
        prefs = self.store.preferences if self.store else {}
        return {"stage": self.stage, "storage_supported": bool(self.store and self.store.supported),
                "save_messages": prefs.get("save_messages", False), "retain_login": prefs.get("retain_login", False),
                "persistence_error": self.persistence_error, "cached_channels": self.cache_list()}

    async def connect(self, data):
        async with self.auth_lock:
            if self.restore_task and not self.restore_task.done():
                raise AppError("Login restoration is in progress. Please wait.", 409)
            return await self._connect(data)

    async def _connect(self, data, saved_session=None):
        # Both manual login and restored login use the same proxy validation and transport.
        if self.client:
            raise AppError("Disconnect the current session before changing credentials.", 409)
        try:
            api_id = int(data.get("api_id", ""))
        except (ValueError, TypeError):
            raise AppError("API ID must be a positive integer.")
        api_hash = str(data.get("api_hash", "")).strip()
        if api_id <= 0 or api_id > 2147483647 or not re.fullmatch(r"[a-fA-F0-9]{32}", api_hash):
            raise AppError("Enter a valid API ID and a 32-character API Hash.")
        try:
            proxy = parse_proxy(data)
        except ValueError as exc:
            raise AppError(str(exc)) from None
        self.client = self.factory(
            StringSession(saved_session) if saved_session else MemorySession(), api_id, api_hash, proxy=proxy,
            timeout=10, connection_retries=1, request_retries=1,
            flood_sleep_threshold=0, receive_updates=False,
            device_model="TG-MFG", app_version=CORE_VERSION,
        )
        self.diagnostics.record("telegram_connect", proxy_enabled=bool(proxy),
                                proxy_type=proxy['proxy_type'] if proxy else "none",
                                proxy_backend="python-socks" if proxy else "direct")
        try:
            if proxy:
                await probe_proxy(proxy,self.diagnostics)
            await asyncio.wait_for(self.client.connect(), 25)
            self.stage = "restoring" if saved_session else "phone"
            self.credentials = {"api_id":api_id, "api_hash":api_hash, "proxy_enabled":bool(proxy)}
            if proxy:
                self.credentials.update(proxy_type=proxy["proxy_type"], proxy_host=proxy["addr"], proxy_port=proxy["port"])
            if saved_session:
                if not await asyncio.wait_for(self.client.is_user_authorized(), 15):
                    raise AppError("The saved login is no longer valid. Sign in again.", 401)
                self.stage = "ready"
                await self._authorized()
        except BaseException:
            await self._disconnect()
            raise
        return await self.status()

    async def test_proxy(self, data):
        try:
            proxy = parse_proxy(data)
        except ValueError as exc:
            raise AppError(str(exc)) from None
        if not proxy:
            raise AppError('Enable the proxy and enter its settings first.')
        return await probe_proxy(proxy,self.diagnostics)

    def require_client(self, authorized=False):
        if not self.client:
            raise AppError("Connect to Telegram first.", 401)
        if authorized and self.stage != "ready":
            raise AppError("Sign in to your account first.", 401)

    async def send_code(self, data):
        async with self.auth_lock:
            self.require_client()
            if self.stage == "ready":
                raise AppError("The account is already signed in.", 409)
            phone = re.sub(r"[\s()-]", "", str(data.get("phone") or self.phone or ""))
            if not re.fullmatch(r"\+[1-9]\d{6,14}", phone):
                raise AppError("The phone number must include the country code, for example +12025550123.")
            self.phone = phone
            self.phone_code_hash = None
            self.stage = "phone"
            sent = await asyncio.wait_for(self.client.send_code_request(phone), 25)
            self.phone_code_hash = sent.phone_code_hash
            self.stage = "code"
            return await self.status()

    async def sign_in(self, data):
        async with self.auth_lock:
            self.require_client()
            if self.stage == "password":
                password = data.get("password", "")
                if not isinstance(password, str) or not password:
                    raise AppError("Enter your two-step verification password.")
                await asyncio.wait_for(self.client.sign_in(password=password), 25)
            elif self.stage == "code" and self.phone_code_hash:
                code = str(data.get("code", "")).strip()
                if not re.fullmatch(r"\d{4,8}", code):
                    raise AppError("Enter a valid numeric login code.")
                try:
                    await asyncio.wait_for(self.client.sign_in(
                        phone=self.phone, code=code, phone_code_hash=self.phone_code_hash,
                    ), 25)
                except errors.SessionPasswordNeededError:
                    self.stage = "password"
                    return await self.status()
            else:
                raise AppError("Request a login code first.")
            self.stage = "ready"
            self.phone = self.phone_code_hash = None
            await self._authorized()
            return await self.status()

    async def list_channels(self):
        async with self.channel_lock:
            self.require_client(True)
            result, entities = [], {}
            iterator = self.client.iter_dialogs(ignore_migrated=True).__aiter__()
            async with self.auth_lock:
                while True:
                    try:
                        dialog = await asyncio.wait_for(iterator.__anext__(), 25)
                    except StopAsyncIteration:
                        break
                    entity = dialog.entity
                    if isinstance(entity, types.Channel) and entity.broadcast and not getattr(entity, "left", False):
                        key = str(entity.id)
                        entities[key] = entity
                        result.append({"id": key, "title": entity.title, "username": entity.username or ""})
                self.channels = entities
            result.sort(key=lambda item: item["title"].casefold())
            return {"channels": result}

    async def channel_count(self, data):
        async with self.auth_lock:
            self.require_client(True)
            key = str(data.get("channel_id", ""))
            if key not in self.channels:
                raise AppError("Select a channel from the channel list.")
            # One history request, at most one message; never infer count from its ID.
            response = await asyncio.wait_for(self.client(functions.messages.GetHistoryRequest(
                peer=self.channels[key], offset_id=0, offset_date=None, add_offset=0,
                limit=1, max_id=0, min_id=0, hash=0,
            )), 25)
            count = getattr(response, "count", None)
            if count is None:
                # messages.Messages contains the complete (small) result set.
                if isinstance(response, types.messages.Messages):
                    count = len(response.messages)
                else:
                    raise AppError("Telegram did not return a message count for this channel. Try again later.")
            return {"channel_id": key, "count": int(count),
                    "inexact": bool(getattr(response, "inexact", False))}

    async def start_scan(self, data):
        async with self.auth_lock:
            self.require_client(True)
            if self.scan_task and not self.scan_task.done():
                raise AppError("A fetch task is already running. Stop it first.", 409)
            key = str(data.get("channel_id", ""))
            if key not in self.channels:
                raise AppError("Select a channel from the channel list.")
            try:
                limit = int(data.get("limit", 2000))
            except (ValueError, TypeError):
                raise AppError("Invalid message limit format.")
            if limit not in (0, 500, 2000, 10000, 50000):
                raise AppError("Choose a supported message limit.")
            interval = parse_scan_interval(data)
            start, end = parse_date(data.get("start")), parse_date(data.get("end"))
            if start and end and start >= end:
                raise AppError("The start date must precede the end date.")
            self.rows = []
            self.scan = {"status": "running", "checked": 0, "count": 0,
                         "error": "", "channel_id": key, "channel_title": self.channels[key].title, "limit": limit,
                         "scan_id": secrets.token_urlsafe(12), "request_interval": interval,
                         "skipped": {"service":0, "outside_dates":0, "unsupported":0, "duplicate":0},
                         "stop_reason":"", "fetched_at":datetime.now(timezone.utc).isoformat(), "kind":"history"}
            self.scan_task = asyncio.create_task(self._scan(self.channels[key], limit, start, end))
            return dict(self.scan)

    async def _scan(self, channel, limit, start, end):
        iterator = self.client.iter_messages(channel, limit=limit or None, offset_date=end,
                                             wait_time=self.scan["request_interval"]).__aiter__()
        seen = set()
        try:
            # A complete history can be lengthy; individual network operations stay bounded.
            while not self.scan.get("cancel_requested") and (not limit or self.scan["checked"] < limit):
                try:
                    # Telethon paces network batches (up to 100), not individual cached rows.
                    # A live update applies when the next batch wait is calculated.
                    interval = self.scan["request_interval"]
                    if hasattr(iterator, "wait_time"):
                        iterator.wait_time = interval
                    # Deliberate pacing must not consume the network timeout budget.
                    message = await asyncio.wait_for(iterator.__anext__(), 30 + interval)
                except StopAsyncIteration:
                    self.scan["stop_reason"] = "history_exhausted"
                    break
                self.scan["checked"] += 1
                if not isinstance(message, (types.Message, types.MessageService)):
                    self.scan["skipped"]["unsupported"] += 1
                    continue
                if start and message.date < start:
                    self.scan["skipped"]["outside_dates"] += 1
                    self.scan["stop_reason"] = "date_boundary"
                    break
                if end and message.date >= end:
                    self.scan["skipped"]["outside_dates"] += 1
                    continue
                if isinstance(message, types.MessageService):
                    self.scan["skipped"]["service"] += 1
                    continue
                if message.id in seen:
                    self.scan["skipped"]["duplicate"] += 1
                    continue
                seen.add(message.id)
                row = serialize_message(message, channel)
                self.rows.append(row)
                blocked = self.blocked_messages.get((str(channel.id), row["id"]))
                if blocked:
                    blocked.update(row, channel_title=channel.title)
                self.scan["count"] = len(self.rows)
            self.scan["status"] = "cancelled" if self.scan.get("cancel_requested") else "done"
            self.scan["stop_reason"] = self.scan["stop_reason"] or ("cancelled" if self.scan.get("cancel_requested") else "limit_reached")
        except asyncio.CancelledError:
            self.scan.update(status="cancelled", stop_reason="cancelled")
        except Exception as exc:
            self.scan.update(status="error", stop_reason="error", error=public_error(exc))
        finally:
            self._save_snapshot()

    async def scan_status(self):
        self.require_content()
        return dict(self.scan)

    async def set_scan_speed(self, data):
        async with self.auth_lock:
            self.require_client(True)
            interval = parse_scan_interval(data)
            if not self.scan_task or self.scan_task.done() or self.scan["status"] != "running":
                raise AppError("No fetch task is running. The new interval applies to the next fetch.", 409)
            if data.get("scan_id") != self.scan.get("scan_id"):
                raise AppError("The fetch task has changed. Refresh its status and try again.", 409)
            self.scan["request_interval"] = interval
            return dict(self.scan)

    async def results(self):
        self.require_content()
        channel_id = self.scan.get("channel_id")
        return {"messages": [{**row, "blocked": (channel_id, row["id"]) in self.blocked_messages}
                             for row in self.rows], "scan": dict(self.scan),
                "blocked_messages": list(self.blocked_messages.values())}

    async def set_message_block(self, data):
        async with self.auth_lock:
            self.require_content()
            channel_id, message_id = data.get("channel_id"), data.get("message_id")
            blocked = data.get("blocked")
            if (not isinstance(channel_id, str) or not channel_id.isdigit()
                    or type(message_id) is not int or not 0 < message_id <= 2147483647
                    or type(blocked) is not bool):
                raise AppError("Invalid blocked-message parameters.")
            key = channel_id, message_id
            if blocked:
                if self.scan_task and not self.scan_task.done():
                    raise AppError("Wait for fetching to finish, or stop it before blocking messages.", 409)
                if channel_id != self.scan.get("channel_id"):
                    raise AppError("Only messages from the currently fetched channel can be blocked.", 409)
                row = next((row for row in self.rows if row["id"] == message_id), None)
                if row is None:
                    raise AppError("The message is not in the fetched results.", 404)
                self.blocked_messages.pop(key, None)
                self.blocked_messages[key] = {**row, "blocked": True, "channel_id": channel_id,
                                              "channel_title": self.scan.get("channel_title", "Channel")}
            else:
                self.blocked_messages.pop(key, None)
            self._save_snapshot()
            self._save_blocks(channel_id)
            return {"channel_id": channel_id, "message_id": message_id, "blocked": blocked,
                    "message": self.blocked_messages.get(key)}

    async def cancel_scan(self):
        task, scan = self.scan_task, self.scan
        if task and not task.done():
            scan["cancel_requested"] = True
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            scan["status"] = "cancelled"
        return dict(scan)

    def require_content(self):
        if self.stage != "ready" and not self.cached:
            raise AppError("Sign in or open saved messages first.", 401)

    def cache_list(self):
        return [{"id":key, "title":item["scan"].get("channel_title", "Channel"),
                 "count":len(item["messages"]), "saved_at":item.get("saved_at", "")}
                for key, item in self.cached.items()]

    def initialize_storage(self):
        if self.store and self.store.preferences["save_messages"]:
            self.account_id = self.store.preferences["last_account"]
            self._load_snapshots()

    def _load_snapshots(self):
        self.cached = {item["scan"]["channel_id"]:item for item in
                       (self.store.snapshots(self.account_id) if self.store.preferences["save_messages"] else [])}
        self.blocked_messages = {(row["channel_id"], row["id"]):row
                                 for item in self.cached.values() for row in item.get("blocked_messages", [])}
        if self.cached:
            item = max(self.cached.values(), key=lambda item:item.get("saved_at", ""))
            self._apply_snapshot(item)
        else:
            self.rows = []
            self.scan = {"status":"idle", "checked":0, "count":0, "error":""}

    def _apply_snapshot(self, snapshot):
        self.rows = [dict(row) for row in snapshot["messages"]]
        self.scan = {**snapshot["scan"], "scan_id":secrets.token_urlsafe(12), "restored":True}
        self.scan["status"] = "done" if self.scan["status"] == "running" else self.scan["status"]

    def _save_snapshot(self):
        if not (self.store and self.store.preferences["save_messages"] and self.account_id and self.scan.get("channel_id")):
            return
        key = self.scan["channel_id"]
        item = {"scan":dict(self.scan), "messages":[dict(row) for row in self.rows],
                "blocked_messages":[dict(row) for row in self.blocked_messages.values() if row["channel_id"] == key],
                "saved_at":datetime.now(timezone.utc).isoformat()}
        try:
            self.store.save(self.account_id, item)
            self.cached[key] = item
            self.persistence_error = ""
        except Exception:
            self.persistence_error = "Messages could not be saved locally. Check disk space or directory permissions. Fetched content remains in memory."

    def _save_blocks(self, channel_id):
        if channel_id == self.scan.get("channel_id") or channel_id not in self.cached:
            return
        item = dict(self.cached[channel_id])
        item["blocked_messages"] = [dict(row) for row in self.blocked_messages.values() if row["channel_id"] == channel_id]
        try:
            self.store.save(self.account_id, item)
            self.cached[channel_id] = item
        except Exception:
            self.persistence_error = "Blocked records could not be saved locally."

    async def _authorized(self):
        # Real account ID scopes cached data; never use a phone number as a storage key.
        if self.store and self.store.supported:
            me = await asyncio.wait_for(self.client.get_me(), 15)
            account = str(me.id)
            if account != self.account_id or self.scan.get("status") == "idle":
                self.account_id = account
                self._load_snapshots()
            if self.store.preferences["retain_login"]:
                try:
                    self._save_login()
                except Exception:
                    self.persistence_error = "Sign-in succeeded, but login could not be saved securely. Sign in again after restarting."
                    self.store.settings(retain_login=False)

    def _save_login(self):
        session = StringSession.save(self.client.session)
        if not session:
            raise StorageError("The current login cannot be saved. Sign in again.")
        self.store.login_vault().write({"credentials":self.credentials, "session":session})

    async def storage_settings(self, data):
        async with self.auth_lock:
            if not self.store or not self.store.supported:
                raise AppError("Local storage is supported only on macOS and Windows.")
            if self.scan_task and not self.scan_task.done():
                raise AppError("Stop the current task before changing local storage options.", 409)
            if set(data) != {"save_messages", "retain_login"} or any(type(value) is not bool for value in data.values()):
                raise AppError("Storage switches must be boolean values.")
            old = self.store.preferences
            if data["retain_login"] and self.stage == "ready":
                self._save_login()  # Failure leaves the switch off; never fall back to plaintext.
            elif not data["retain_login"] and old["retain_login"]:
                self.store.login_vault().delete()
            self.store.settings(**data)
            if not data["save_messages"]:
                self.store.clear_messages()
                self.cached = {}
            else:
                self._save_snapshot()
            return await self.status()

    async def open_cache(self, data):
        async with self.auth_lock:
            if self.scan_task and not self.scan_task.done():
                raise AppError("Stop the current task first.", 409)
            item = self.cached.get(str(data.get("channel_id", "")))
            if item is None:
                raise AppError("There are no saved messages for this channel.", 404)
            self._apply_snapshot(item)
            return await self.results()

    async def restore_login(self, data=None):
        if not self.store or not self.store.preferences["retain_login"]:
            raise AppError("There is no saved login.", 404)
        if self.stage == "ready" or (self.restore_task and not self.restore_task.done()):
            return await self.status()
        self.stage = "restoring"
        self.persistence_error = ""
        self.restore_task = asyncio.create_task(self._restore_login())
        return await self.status()

    async def _restore_login(self):
        async with self.auth_lock:
            try:
                record = self.store.login_vault().read()
                if not record:
                    raise AppError("The saved login does not exist. Sign in again.", 401)
                await asyncio.wait_for(self._connect(record["credentials"], record["session"]), 35)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._disconnect()
                # Keep protected credentials on transient network failure so retry needs no code.
                self.initialize_storage()
                self.persistence_error = public_error(exc)
                if (isinstance(exc, AppError) and exc.status == 401) or type(exc).__name__ in ("AuthKeyUnregisteredError", "SessionRevokedError", "UserDeactivatedError"):
                    try:
                        self.store.login_vault().delete()
                        self.store.settings(retain_login=False)
                    except Exception:
                        self.persistence_error += " The saved login could not be cleared. Disable login retention and try again."

    async def close(self):
        if self.restore_task and not self.restore_task.done():
            self.restore_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.restore_task
        async with self.auth_lock:
            await self.cancel_scan()
            self._save_snapshot()
            if self.store and self.store.preferences["retain_login"] and self.stage == "ready":
                try:
                    self._save_login()
                except Exception:
                    self.persistence_error = "The saved login could not be updated before shutdown."
            if self.store and self.store.preferences["retain_login"]:
                await self._disconnect()  # Stop is not logout when retention was explicitly selected.
            else:
                await self.disconnect_without_lock()
        return {"stopped":True, "warning":self.persistence_error}

    async def disconnect_without_lock(self):
        if self.client and self.stage == "ready":
            try:
                await asyncio.wait_for(self.client.log_out(), 20)
            except Exception:
                self.persistence_error = "The core has stopped, but Telegram did not confirm logout. Remove this session in the official app under Devices."
        await self._disconnect()

    async def refresh_messages(self, data):
        async with self.auth_lock:
            self.require_client(True)
            if self.scan_task and not self.scan_task.done():
                raise AppError("Stop the current task first.", 409)
            key = self.scan.get("channel_id")
            if data.get("scan_id") != self.scan.get("scan_id") or data.get("channel_id") != key:
                raise AppError("The message snapshot has changed. Open it again before checking updates.", 409)
            if not self.rows or key not in self.channels:
                raise AppError("Open saved messages and refresh the channel list to confirm access first.")
            interval = parse_scan_interval(data)
            self.scan = {**self.scan, "status":"running", "kind":"refresh", "restored":False,
                         "scan_id":secrets.token_urlsafe(12), "request_interval":interval, "error":"",
                         "checked":0, "count":sum(not row.get("unavailable") for row in self.rows), "stop_reason":"",
                         "changes":{"text":0, "reactions":0, "other":0, "unavailable":0, "unconfirmed":0}}
            self.scan_task = asyncio.create_task(self._refresh(self.channels[key]))
            return dict(self.scan)

    async def _refresh(self, channel):
        old_rows = list(self.rows)
        last_request = 0
        try:
            for offset in range(0, len(old_rows), 100):
                interval = self.scan["request_interval"]
                await asyncio.sleep(max(0, interval - (time.monotonic() - last_request)))
                last_request = time.monotonic()
                batch = old_rows[offset:offset+100]
                response = await asyncio.wait_for(self.client(functions.channels.GetMessagesRequest(
                    channel=channel, id=[types.InputMessageID(row["id"]) for row in batch])), 30)
                returned = {msg.id:msg for msg in response.messages}
                for old in batch:
                    msg = returned.get(old["id"])
                    self.scan["checked"] += 1
                    if isinstance(msg, (types.MessageEmpty, types.MessageService)):
                        old["unavailable"] = True
                        self.scan["changes"]["unavailable"] += 1
                    elif isinstance(msg, types.Message):
                        new = serialize_message(msg, channel)
                        for name, fields in (("text", ("text", "hashtags")), ("reactions", ("reactions",)),
                                             ("other", ("media", "edited_at"))):
                            if any(old.get(field) != new.get(field) for field in fields):
                                self.scan["changes"][name] += 1
                        new["checked_at"] = datetime.now(timezone.utc).isoformat()
                        if new["text"] != old["text"]:
                            new["previous_text"] = old["text"]
                        elif old.get("previous_text") is not None:
                            new["previous_text"] = old["previous_text"]
                        old.clear()
                        old.update(new)
                    else:
                        # No response for this ID is insufficient evidence of deletion.
                        self.scan["changes"]["unconfirmed"] += 1
                    blocked = self.blocked_messages.get((str(channel.id), old["id"]))
                    if blocked:
                        blocked.update(old)
                self.scan["count"] = sum(not row.get("unavailable") for row in self.rows)
            self.scan.update(status="done", stop_reason="refresh_complete", checked_at=datetime.now(timezone.utc).isoformat())
        except asyncio.CancelledError:
            self.scan.update(status="cancelled", stop_reason="cancelled")
        except Exception as exc:
            self.scan.update(status="error", stop_reason="error", error=public_error(exc))
        finally:
            self._save_snapshot()

    async def _disconnect(self):
        await self.cancel_scan()
        client, self.client = self.client, None
        if client:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(client.disconnect(), 10)
        self.stage = "disconnected"
        self.phone = self.phone_code_hash = None
        self.credentials = None
        self.channels = {}
        self.rows = []
        self.blocked_messages.clear()
        self.scan = {"status": "idle", "checked": 0, "count": 0, "error": ""}

    async def disconnect(self, data=None):
        if self.restore_task and not self.restore_task.done():
            self.restore_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.restore_task
        async with self.auth_lock:
            # Explicitly revoke this authorization when the user disconnects.
            # Never report a successful logout if Telegram could not confirm it.
            error = None
            if self.store and self.store.preferences["retain_login"] and self.stage != "ready":
                error = AppError("Logout could not be confirmed online.")
            await self.cancel_scan()
            if self.client and self.stage == "ready":
                try:
                    await asyncio.wait_for(self.client.log_out(), 20)
                except Exception as exc:
                    error = exc
            storage_error = None
            if self.store and self.store.supported:
                try:
                    if self.store.preferences["retain_login"]:
                        self.store.login_vault().delete()
                    self.store.settings(retain_login=False, save_messages=False, last_account="")
                    self.store.clear_messages()
                except Exception:
                    storage_error = AppError("The account disconnected, but some saved data could not be cleared. Check directory or Keychain permissions, then disable storage and try again.")
            self.cached = {}
            await self._disconnect()
            if storage_error:
                raise storage_error
            if error:
                raise AppError("The local session was cleared, but Telegram did not confirm logout. Remove this session in the official app under Devices.")
            return await self.status()


class Runtime:
    def __init__(self, factory=TelegramClient, diagnostics=None, store=None):
        self.diagnostics = diagnostics or Diagnostics()
        self.loop = asyncio.new_event_loop()
        self.diagnostics.record('runtime_created',event_loop=type(self.loop).__name__)
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        async def create_service():
            service = TelegramService(factory, self.diagnostics, store or (LocalStore() if factory is TelegramClient else None))
            service.initialize_storage()
            if service.store and service.store.preferences["retain_login"]:
                await service.restore_login()
            return service
        self.service = asyncio.run_coroutine_threadsafe(create_service(), self.loop).result(timeout=5)

    def call(self, method, *args):
        started = time.monotonic()
        future = asyncio.run_coroutine_threadsafe(getattr(self.service, method)(*args), self.loop)
        try:
            result = future.result(timeout=45)
            self.diagnostics.record("operation", operation=method, result="ok", duration_ms=round((time.monotonic()-started)*1000))
            return result
        except concurrent.futures.TimeoutError:
            future.cancel()
            self.diagnostics.record("operation", operation=method, result="timeout", duration_ms=round((time.monotonic()-started)*1000))
            raise AppError("Operation timed out. Check the connection and try again.", 504)
        except Exception as exc:
            self.diagnostics.record("operation", operation=method, result="error", duration_ms=round((time.monotonic()-started)*1000), **error_fields(exc))
            raise

    def close(self):
        with contextlib.suppress(Exception):
            self.call("close")
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=12)
        self.loop.close()


def validate_portal_origin(value):
    """Only the explicitly configured website may connect to the local core."""
    if not value:
        return ""
    url = urlsplit(value)
    if (url.scheme not in ("http", "https") or not url.hostname or url.username or url.password
            or url.path not in ("", "/") or url.query or url.fragment):
        raise ValueError("The website origin must be a complete http:// or https:// host address without a path or credentials.")
    # Accessing .port also validates the numeric range.
    url.port
    return f"{url.scheme}://{url.netloc}".rstrip("/")


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port=8765, runtime=None, portal_origin="", installation=None, diagnostics=None):
        self.diagnostics = diagnostics or (runtime.diagnostics if runtime else Diagnostics())
        self.portal_origin = validate_portal_origin(portal_origin)
        if installation and (not isinstance(installation, dict)
                             or not {"managed", "platform", "version", "id"} <= installation.keys()
                             or installation.get("managed") is not True or installation.get("version") != 1
                             or installation.get("platform") not in ("windows", "mac")
                             or not isinstance(installation.get("id"), str) or not installation["id"]):
            raise ValueError("Core installation information is incomplete. Run the installer again.")
        self.installation = ({key: installation[key] for key in ("managed", "platform", "version", "id")}
                             if installation else None)
        self.token = secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", port), Handler)
        self.runtime = runtime or Runtime(diagnostics=self.diagnostics)
        self.origin = f"http://127.0.0.1:{self.server_port}"
        self.diagnostics.record("core_started", port=self.server_port)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass  # Never log request bodies, credentials, or channel content.

    def reply(self, status, payload, mime="application/json; charset=utf-8"):
        path = urlsplit(self.path).path
        self.server.diagnostics.record("http", method=self.command, route=path if path in ROUTES else "other",
                                       status=status, origin_match=self.headers.get("Origin") in (None, self.server.origin, self.server.portal_origin),
                                       fetch_site=self.headers.get("Sec-Fetch-Site", "absent") if self.headers.get("Sec-Fetch-Site", "absent")
                                       in ("absent", "none", "same-origin", "same-site", "cross-site") else "unknown")
        body = json.dumps(payload, ensure_ascii=False).encode() if isinstance(payload, dict) else payload
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; form-action 'self'; base-uri 'none'")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if self.server.portal_origin and self.headers.get("Origin") == self.server.portal_origin:
            self.send_header("Access-Control-Allow-Origin", self.server.portal_origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(body)

    def check_request(self, api=False):
        if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
            raise AppError("Host address is not allowed.", 403)
        origin = self.headers.get("Origin")
        portal_request = bool(self.server.portal_origin and origin == self.server.portal_origin)
        # A normal top-level link from the website must be able to open the local UI.
        # Its GET has no Origin. SOP and frame-ancestors still protect the rendered token.
        # APIs, preflights, iframes and every mutation keep the strict origin/token rules.
        document_navigation = (self.command == "GET" and urlsplit(self.path).path in ("/", "/index.html")
                               and self.headers.get("Sec-Fetch-Mode") == "navigate"
                               and self.headers.get("Sec-Fetch-Dest") == "document" and not origin)
        if self.headers.get("Sec-Fetch-Site") == "cross-site" and not portal_request and not document_navigation:
            raise AppError("Cross-site requests are not allowed.", 403)
        if origin and origin != self.server.origin and not portal_request:
            raise AppError("Cross-site requests are not allowed.", 403)
        if api and not secrets.compare_digest(self.headers.get("X-App-Token", ""), self.server.token):
            raise AppError("Invalid page session. Refresh this page.", 403)

    def do_GET(self):
        path = urlsplit(self.path).path
        try:
            self.check_request(path.startswith("/api/") and path != "/api/bootstrap")
            if path == "/api/bootstrap":
                self.reply(200, {"application": "TG-MFG", "protocol": 1, "token": self.server.token,
                                 "installation": self.server.installation, "core_version": CORE_VERSION,
                                 "capabilities": {"scan_speed": True, "local_storage": True, "scan_report":True}})
            elif path in ("/", "/index.html"):
                html = ((ROOT / "web/index.html").read_text(encoding="utf-8").replace("__APP_TOKEN__", self.server.token)
                        .replace("__CORE_MODE__", "local").replace("__CORE_ORIGIN__", self.server.origin)
                        .replace("__MANAGED_CORE__", "true" if self.server.installation else "false"))
                self.reply(200, html.encode(), "text/html; charset=utf-8")
            elif path in ("/app.js", "/styles.css", "/themes.css", "/logic.js", "/theme.js", "/core.js", "/core.css", "/diagnostics.js", "/i18n.js", "/translations.js"):
                mime = "text/css" if path.endswith(".css") else "text/javascript"
                self.reply(200, (ROOT / "web" / path[1:]).read_bytes(), mime + "; charset=utf-8")
            elif path in ("/api/status", "/api/channels", "/api/scan", "/api/results"):
                method = {"/api/status": "status", "/api/channels": "list_channels",
                          "/api/scan": "scan_status", "/api/results": "results"}[path]
                self.reply(200, self.server.runtime.call(method))
            elif path == "/api/diagnostics":
                report = self.server.diagnostics.snapshot()
                report["telegram_stage"] = self.server.runtime.call("status")["stage"]
                report["portal_origin"] = self.server.portal_origin
                self.reply(200, report)
            else:
                self.reply(404, {"error": "Page not found."})
        except Exception as exc:
            self.reply(error_status(exc), {"error": public_error(exc)})

    def do_OPTIONS(self):
        try:
            self.check_request()
            if not self.server.portal_origin or self.headers.get("Origin") != self.server.portal_origin:
                raise AppError("Cross-site requests are not allowed.", 403)
            if not urlsplit(self.path).path.startswith("/api/"):
                raise AppError("API endpoint not found.", 404)
            if self.headers.get("Access-Control-Request-Method", "") not in ("GET", "POST"):
                raise AppError("Request method is not allowed.", 403)
            requested = {part.strip().lower() for part in self.headers.get("Access-Control-Request-Headers", "").split(",") if part.strip()}
            if not requested <= {"x-app-token", "content-type"}:
                raise AppError("Request headers are not allowed.", 403)
            self.send_response(204)
            self.server.diagnostics.record("http", method="OPTIONS", route=urlsplit(self.path).path if urlsplit(self.path).path in ROUTES else "other", status=204, origin_match=True)
            self.send_header("Access-Control-Allow-Origin", self.server.portal_origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST")
            self.send_header("Access-Control-Allow-Headers", "X-App-Token, Content-Type")
            self.send_header("Access-Control-Max-Age", "600")
            if self.headers.get("Access-Control-Request-Private-Network") == "true":
                self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Vary", "Origin")
            self.send_header("Content-Length", "0")
            self.end_headers()
        except Exception as exc:
            self.reply(error_status(exc), {"error": public_error(exc)})

    def do_POST(self):
        try:
            self.check_request(True)
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise AppError("Only JSON requests are accepted.", 415)
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise AppError("Invalid request length.")
            if not 0 < size <= 16384:
                raise AppError("Invalid request size.", 413)
            self.connection.settimeout(10)
            try:
                data = json.loads(self.rfile.read(size))
            except (ValueError, OSError):
                raise AppError("Invalid request format.")
            if not isinstance(data, dict):
                raise AppError("Invalid request format.")
            routes = {"/api/connect": "connect", "/api/send-code": "send_code",
                      "/api/sign-in": "sign_in", "/api/disconnect": "disconnect",
                      "/api/scan": "start_scan", "/api/channel-count": "channel_count", "/api/test-proxy":"test_proxy",
                      "/api/message-block": "set_message_block", "/api/scan-speed": "set_scan_speed",
                      "/api/storage": "storage_settings", "/api/cache-open":"open_cache",
                      "/api/restore-login":"restore_login", "/api/refresh-messages":"refresh_messages"}
            path = urlsplit(self.path).path
            if path == "/api/debug":
                if not isinstance(data.get("enabled"), bool):
                    raise AppError("The debug switch must be a boolean value.")
                self.server.diagnostics.set_enabled(data["enabled"])
                self.reply(200, {"enabled": self.server.diagnostics.enabled})
                return
            elif path == "/api/shutdown":
                warning = ""
                try:
                    result = self.server.runtime.call("close")
                    warning = result.get("warning", "")
                except Exception as exc:
                    warning = public_error(exc)
                self.reply(200, {"stopped": True, "warning": warning})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            elif path == "/api/cancel":
                result = self.server.runtime.call("cancel_scan")
            elif path in routes:
                result = self.server.runtime.call(routes[path], data)
            else:
                raise AppError("API endpoint not found.", 404)
            self.reply(200, result)
        except Exception as exc:
            self.reply(error_status(exc), {"error": public_error(exc)})


def main():
    parser = argparse.ArgumentParser(description="TG-MFG · Local Telegram Message Filter Gateway")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open-browser", action="store_true", help="Open the default browser after startup")
    parser.add_argument("--portal-origin", help="Website origin allowed to connect to this core")
    parser.add_argument("--debug", action="store_true", help="Write redacted diagnostics to debug.log in the installation directory")
    args = parser.parse_args()
    logging.getLogger("telethon").setLevel(logging.CRITICAL)
    try:
        portal_origin = args.portal_origin
        settings = ROOT / "core-settings.json"
        if portal_origin is None and settings.exists():
            portal_origin = json.loads(settings.read_text(encoding="utf-8")).get("portal_origin", "")
        receipt = ROOT / "installation.json"
        installation = json.loads(receipt.read_text(encoding="utf-8")) if receipt.exists() else None
        diagnostics = Diagnostics(ROOT / "debug.log", args.debug)
        server = LocalServer(args.port, portal_origin=portal_origin or "", installation=installation, diagnostics=diagnostics)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Cannot start the local service on port {args.port}: {exc}. Close the existing service or use --port 8767.\n")
    print(f"Open {server.origin}  —  Press Ctrl+C to stop. Local storage is controlled by the page switches.", flush=True)
    if args.open_browser:
        threading.Thread(target=webbrowser.open, args=(server.portal_origin or server.origin,), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.runtime.close()
        server.diagnostics.close()


if __name__ == "__main__":
    main()
