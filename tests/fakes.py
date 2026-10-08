"""Offline Telegram adapter. It never connects to the network."""
import asyncio
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from telethon import types, errors, functions
from telethon.crypto.authkey import AuthKey

NOW = datetime(2026, 10, 8, 8, 0, tzinfo=timezone.utc)


def channel(cid=101, title="设计与灵感", username="design_notes", **kwargs):
    return types.Channel(id=cid, title=title, username=username,
                         photo=types.ChatPhotoEmpty(), date=NOW, broadcast=True, **kwargs)


def message(mid, text="消息内容", days=0, reactions=None, entities=None):
    items = []
    for key, count in (reactions or {}).items():
        if key == "paid":
            reaction = types.ReactionPaid()
        elif key.startswith("custom:"):
            reaction = types.ReactionCustomEmoji(int(key.split(":")[1]))
        else:
            reaction = types.ReactionEmoji(key)
        items.append(types.ReactionCount(reaction=reaction, count=count))
    if entities is None:
        entities = [types.MessageEntityHashtag(
            offset=len(text[:match.start()].encode("utf-16-le")) // 2,
            length=len(match.group().encode("utf-16-le")) // 2,
        ) for match in re.finditer(r"(?<![\w/#])#\w+", text)]
    return types.Message(id=mid, peer_id=types.PeerChannel(101),
                         date=NOW - timedelta(days=days), message=text,
                         entities=entities,
                         reactions=types.MessageReactions(results=items))


class FakeClient:
    require_2fa = True

    def __init__(self, *args, **kwargs):
        self.args, self.kwargs = args, kwargs
        self.session = args[0] if args else None
        self.account_id = 42
        self.authorized = bool(self.session and self.session.auth_key)
        self.connected = False
        self.revoked = False
        self.delay = 0
        self.failure = None
        self.logout_failure = False
        self.requests = []
        self.total_count = 80000
        self.count_inexact = False
        self.count_failure = None
        self.entities = [channel(), channel(102, "技术周刊", "tech_weekly"),
                         channel(103, "归档频道", None), channel(104, "已退出", left=True),
                         SimpleNamespace(id=105, title="群组", broadcast=False)]
        self.messages = [message(1000 - i,
            ["设计系统中的留白与层次：一次清晰的界面整理。 #设计 #UI", "如何通过阅读建立长期的知识体系。 #阅读", "城市散步：观察日常生活里的设计细节。 #设计 #生活"][i % 3],
            days=i, reactions={"❤️": (i * 7) % 109, "👍": (i * 11) % 67, "🔥": (i * 5) % 43})
            for i in range(80)]
        self.messages[0] = message(1000, '<img src=x onerror="window.__xss=1"> HTML 内容需要按文字显示', reactions={"❤": 999})

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def get_me(self):
        return SimpleNamespace(id=self.account_id)

    async def is_user_authorized(self):
        return self.authorized

    async def send_code_request(self, phone):
        self.phone = phone
        return SimpleNamespace(phone_code_hash="fake-hash")

    async def sign_in(self, **kwargs):
        if kwargs.get("password"):
            if kwargs["password"] != "test-password":
                raise errors.PasswordHashInvalidError(request=None)
        else:
            if kwargs.get("code") != "12345":
                raise errors.PhoneCodeInvalidError(request=None)
            if self.require_2fa:
                raise errors.SessionPasswordNeededError(request=None)

        self.authorized = True
        if self.session:
            self.session.set_dc(2, '149.154.167.51', 443)
            self.session.auth_key = AuthKey(b'x' * 256)

    async def log_out(self):
        if self.logout_failure:
            raise OSError("fake failure")
        self.revoked = True
        self.connected = False

    async def iter_dialogs(self, **kwargs):
        for entity in self.entities:
            await asyncio.sleep(0)
            yield SimpleNamespace(entity=entity)

    async def __call__(self, request):
        if isinstance(request, functions.channels.GetMessagesRequest):
            self.requests.append(request)
            wanted = {item.id for item in request.id}
            return SimpleNamespace(messages=[row for row in self.messages if row.id in wanted])
        assert isinstance(request, functions.messages.GetHistoryRequest)
        self.requests.append(request)
        if self.count_failure:
            raise self.count_failure
        return SimpleNamespace(count=self.total_count, inexact=self.count_inexact)

    async def iter_messages(self, channel, **kwargs):
        self.history_args = kwargs
        for index, row in enumerate(self.messages):
            await asyncio.sleep(self.delay)
            if self.failure and index == 1:
                raise self.failure
            if kwargs.get("offset_date") and row.date >= kwargs["offset_date"]:
                continue
            yield row
