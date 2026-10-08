"""Persistence lifecycle, account isolation, and exact-ID reconciliation, offline."""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import TelegramService, AppError
from storage import LocalStore, StorageError, MacKeychain
from telethon import types, functions
from fakes import FakeClient, message, NOW


class TestVault:
    def __init__(self):
        self.record = None
        self.fail = False

    def read(self):
        return self.record

    def write(self, record):
        if self.fail:
            raise StorageError('测试：安全存储不可用')
        self.record = json.loads(json.dumps(record))

    def delete(self):
        self.record = None


class PersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault = TestVault()
        self.services = []
        self.service = self.new_service()

    def new_service(self, factory=FakeClient):
        service = TelegramService(factory, store=LocalStore(self.root, self.vault))
        service.initialize_storage()
        self.services.append(service)
        return service

    async def asyncTearDown(self):
        for service in self.services:
            if service.restore_task:
                await service.restore_task
            await service._disconnect()
        self.tmp.cleanup()

    async def login(self, service=None):
        service = service or self.service
        await service.connect({'api_id':123, 'api_hash':'a'*32})
        await service.send_code({'phone':'+8613800000000'})
        await service.sign_in({'code':'12345'})
        await service.sign_in({'password':'test-password'})
        await service.list_channels()

    async def scan(self, service=None, **options):
        service = service or self.service
        await service.start_scan({'channel_id':'101', 'limit':500, **options})
        await service.scan_task
        return await service.results()

    async def test_default_writes_nothing_and_report_explains_missing_rows(self):
        await self.login()
        client = self.service.client
        client.messages = [message(10), types.MessageService(id=9, peer_id=types.PeerChannel(101),
                           date=NOW, action=types.MessageActionChannelCreate('test')),
                           message(8), message(8), types.MessageEmpty(7, types.PeerChannel(101))]
        result = await self.scan()
        self.assertEqual(result['scan']['checked'],5)
        self.assertEqual(result['scan']['count'],2)
        self.assertEqual(result['scan']['skipped'], {'service':1,'unsupported':1,'outside_dates':0,'duplicate':1})
        self.assertEqual(result['scan']['stop_reason'],'history_exhausted')
        self.assertEqual(list(self.root.iterdir()),[])
        self.assertIsNone(self.vault.record)

    async def test_snapshots_survive_restart_offline_with_blocks_and_multiple_channels(self):
        await self.login()
        await self.service.storage_settings({'save_messages':True, 'retain_login':False})
        await self.scan()
        await self.service.set_message_block({'channel_id':'101','message_id':1000,'blocked':True})
        await self.scan(channel_id='102')
        await self.service.close()
        restarted = self.new_service()
        state = await restarted.status()
        self.assertEqual(state['stage'],'disconnected')
        self.assertEqual(len(state['cached_channels']),2)
        self.assertIsNone(restarted.client)
        result = await restarted.open_cache({'channel_id':'101'})
        self.assertTrue(result['scan']['restored'])
        self.assertTrue(result['messages'][0]['blocked'])
        await restarted.set_message_block({'channel_id':'101','message_id':1000,'blocked':False})
        again = self.new_service()
        self.assertFalse((await again.open_cache({'channel_id':'101'}))['messages'][0]['blocked'])
        await again.storage_settings({'save_messages':False,'retain_login':False})
        self.assertTrue(again.rows)
        self.assertFalse((self.root / 'messages.sqlite3').exists())
        self.assertEqual((await self.new_service().status())['cached_channels'],[])

    async def test_retained_login_auto_restores_and_stop_does_not_revoke(self):
        await self.service.storage_settings({'save_messages':True,'retain_login':True})
        await self.login()
        await self.scan()
        client = self.service.client
        await self.service.close()
        self.assertFalse(client.revoked)
        self.assertIsNotNone(self.vault.record)
        restarted = self.new_service()
        await restarted.restore_login()
        await restarted.restore_task
        self.assertEqual(restarted.stage,'ready')
        self.assertEqual(len(restarted.rows),80)
        self.assertFalse(hasattr(restarted.client,'phone'))
        settings = json.loads((self.root / 'preferences.json').read_text())
        self.assertNotIn('session', settings)
        self.assertNotIn('api_hash', settings)
        self.assertEqual(set(self.vault.record),{'session','credentials'})
        self.assertNotIn('phone',self.vault.record['credentials'])
        self.assertNotIn('password',self.vault.record['credentials'])
        await restarted.disconnect()
        self.assertIsNone(self.vault.record)
        self.assertFalse((self.root / 'messages.sqlite3').exists())
        self.assertFalse(restarted.store.preferences['retain_login'])

    async def test_login_vault_failure_keeps_switch_off_and_current_login_usable(self):
        await self.login()
        self.vault.fail = True
        with self.assertRaises(StorageError):
            await self.service.storage_settings({'save_messages':False,'retain_login':True})
        self.assertFalse(self.service.store.preferences['retain_login'])
        self.assertEqual(self.service.stage,'ready')
        self.assertIsNone(self.vault.record)

    async def test_restoration_network_failure_preserves_cache_and_allows_retry(self):
        await self.service.storage_settings({'save_messages':True,'retain_login':True})
        await self.login()
        await self.scan()
        await self.service.close()
        class Broken(FakeClient):
            async def connect(self):
                raise OSError('offline')
        restarted = self.new_service(Broken)
        await restarted.restore_login()
        await restarted.restore_task
        self.assertEqual(restarted.stage,'disconnected')
        self.assertEqual(len(restarted.rows),80)
        self.assertIsNotNone(self.vault.record)
        self.assertTrue((await restarted.status())['persistence_error'])
        restarted.factory = FakeClient
        await restarted.restore_login()
        await restarted.restore_task
        self.assertEqual(restarted.stage,'ready')

    async def test_revoked_login_is_cleared_and_another_account_has_separate_cache(self):
        await self.service.storage_settings({'save_messages':True,'retain_login':True})
        await self.login()
        await self.scan()
        await self.service.close()
        class Revoked(FakeClient):
            async def is_user_authorized(self):
                return False
        restarted = self.new_service(Revoked)
        await restarted.restore_login()
        await restarted.restore_task
        self.assertFalse(restarted.store.preferences['retain_login'])
        self.assertIsNone(self.vault.record)
        self.assertEqual(len(restarted.rows),80)
        class Another(FakeClient):
            async def get_me(self):
                return SimpleNamespace(id=99)
        another = self.new_service(Another)
        await self.login(another)
        self.assertEqual(another.rows,[])
        self.assertEqual(another.cached,{})
        self.assertEqual(another.blocked_messages,{})
        self.assertTrue(another.store.snapshots('42'))

    async def test_refresh_updates_text_reactions_unavailable_and_keeps_unconfirmed(self):
        await self.login()
        await self.service.storage_settings({'save_messages':True,'retain_login':False})
        await self.scan()
        before = {row['id']:dict(row) for row in self.service.rows}
        self.service.client.messages = [message(1000,'changed #new',reactions={'👍':555}),types.MessageEmpty(999, types.PeerChannel(101))] + self.service.client.messages[3:]
        await self.service.refresh_messages({'scan_id':self.service.scan['scan_id'], 'channel_id':self.service.scan['channel_id'], 'request_interval':0.5})
        await self.service.scan_task
        result = await self.service.results()
        rows = {row['id']:row for row in result['messages']}
        self.assertEqual(rows[1000]['text'],'changed #new')
        self.assertEqual(rows[1000]['previous_text'], before[1000]['text'])
        self.assertEqual(rows[1000]['total'],555)
        self.assertTrue(rows[999]['unavailable'])
        self.assertEqual({k:v for k,v in rows[998].items() if k != 'blocked'},before[998])
        self.assertEqual(result['scan']['changes']['unconfirmed'],1)
        self.assertEqual(result['scan']['changes']['unavailable'],1)
        self.assertEqual(result['scan']['count'],79)
        request = self.service.client.requests[-1]
        self.assertIsInstance(request,functions.channels.GetMessagesRequest)
        self.assertEqual(len(request.id),80)
        restarted = self.new_service()
        self.assertEqual((await restarted.open_cache({'channel_id':'101'}))['messages'], result['messages'])

    async def test_refresh_partial_failure_and_cancel_preserve_unchecked_rows(self):
        await self.login()
        self.service.client.messages = [message(5000-i) for i in range(201)]
        await self.scan()
        original = [dict(row) for row in self.service.rows]
        calls = []
        class Updating(FakeClient):
            async def __call__(self, request):
                calls.append(request)
                if len(calls) > 1:
                    raise OSError('offline')
                return SimpleNamespace(messages=[message(item.id,'updated') for item in request.id])
        original_client = self.service.client
        self.service.client = Updating(original_client.session)
        await self.service.refresh_messages({'scan_id':self.service.scan['scan_id'], 'channel_id':self.service.scan['channel_id'], 'request_interval':0.5})
        await self.service.scan_task
        self.assertEqual(self.service.scan['status'],'error')
        self.assertEqual(self.service.rows[0]['text'],'updated')
        self.assertEqual(self.service.rows[100:],original[100:])
        gate = asyncio.Event()
        class Waiting(FakeClient):
            async def __call__(self, request):
                gate.set()
                await asyncio.Event().wait()
        self.service.client = Waiting(original_client.session)
        await self.service.refresh_messages({'scan_id':self.service.scan['scan_id'], 'channel_id':self.service.scan['channel_id'], 'request_interval':30})
        await asyncio.wait_for(gate.wait(),2)
        before_cancel = [dict(row) for row in self.service.rows]
        await self.service.cancel_scan()
        self.assertEqual(self.service.rows,before_cancel)
        self.assertEqual(self.service.scan['status'],'cancelled')

    async def test_offline_clear_deletes_saved_data_but_does_not_claim_remote_logout(self):
        await self.service.storage_settings({'save_messages':True,'retain_login':True})
        await self.login()
        await self.scan()
        await self.service.close()
        offline = self.new_service()
        with self.assertRaises(AppError) as raised:
            await offline.disconnect()
        self.assertIn('did not confirm logout',str(raised.exception))
        self.assertIsNone(self.vault.record)
        self.assertFalse((self.root / 'messages.sqlite3').exists())
        self.assertFalse(offline.store.preferences['retain_login'])

    async def test_refresh_rejects_stale_snapshot(self):
        await self.login()
        await self.scan()
        with self.assertRaises(AppError) as raised:
            await self.service.refresh_messages({'scan_id':'old-snapshot', 'channel_id':'101', 'request_interval':1})
        self.assertEqual(raised.exception.status,409)
        self.assertEqual(self.service.client.requests,[])

    async def test_disk_write_failure_is_visible_and_partial_scan_can_be_saved(self):
        await self.login()
        await self.service.storage_settings({'save_messages':True,'retain_login':False})
        self.service.client.failure = OSError('offline')
        with patch.object(self.service.store,'save',side_effect=OSError('disk full')):
            await self.scan()
        self.assertEqual(len(self.service.rows),1)
        self.assertEqual(self.service.scan['stop_reason'],'error')
        self.assertIn('could not be saved',(await self.service.status())['persistence_error'])
        self.service.client.failure = None
        await self.service.close()
        self.assertEqual(len(self.new_service().rows),1)

    async def test_protected_restore_uses_saved_proxy_without_direct_fallback(self):
        await self.service.storage_settings({'save_messages':False,'retain_login':True})
        data = {'api_id':123,'api_hash':'a'*32,'proxy_enabled':True,'proxy_type':'socks5','proxy_host':'127.0.0.1','proxy_port':7890}
        with patch('app.probe_proxy',return_value={'ok':True}):
            await self.service.connect(data)
            await self.service.send_code({'phone':'+8613800000000'})
            await self.service.sign_in({'code':'12345'})
            await self.service.sign_in({'password':'test-password'})
            await self.service.close()
            restarted = self.new_service()
            await restarted.restore_login()
            await restarted.restore_task
        self.assertEqual(restarted.stage,'ready')
        self.assertEqual(restarted.client.kwargs['proxy']['addr'],'127.0.0.1')
        self.assertEqual(restarted.client.kwargs['proxy']['port'],7890)
        self.assertEqual(restarted.client.kwargs['proxy']['proxy_type'],'socks5')


if __name__ == '__main__':
    unittest.main()
