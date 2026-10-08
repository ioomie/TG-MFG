"""Exercise Telethon's actual batch iterator with an offline transport and clock."""
import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import AppError, TelegramService, parse_scan_interval
from telethon import types
from telethon.client.messages import MessageMethods
from telethon._updates.entitycache import EntityCache
from fakes import FakeClient, channel, message


class VirtualClock:
    def __init__(self):
        self.now = 1000.0
        self.real_sleep = asyncio.sleep

    async def sleep(self, delay):
        self.now += max(0, delay)
        await self.real_sleep(0)


class HistoryTransport(FakeClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._self_id = 42
        self._mb_entity_cache = EntityCache()
        self.clock = VirtualClock()
        self.first_request = asyncio.Event()
        self.history_requests = []
        self.messages = [message(5000-i) for i in range(501)]
        self.network_latency = 0

    async def get_input_entity(self, entity):
        return types.InputPeerChannel(entity.id, 0)

    def iter_messages(self, entity, **kwargs):
        return MessageMethods.iter_messages(self, entity, **kwargs)

    async def __call__(self, request):
        self.history_requests.append({'time': self.clock.now, 'limit':request.limit})
        self.first_request.set()
        self.clock.now += self.network_latency
        # Model an actual asynchronous network response before cached rows are consumed.
        await self.clock.real_sleep(0)
        rows = [row for row in self.messages if not request.offset_id or row.id < request.offset_id]
        return SimpleNamespace(count=len(self.messages), messages=rows[:request.limit], users=[], chats=[channel()])


class ScanSpeedTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service = TelegramService(HistoryTransport)
        await self.service.connect({'api_id':123, 'api_hash':'a'*32})
        await self.service.send_code({'phone':'+8613800000000'})
        await self.service.sign_in({'code':'12345'})
        await self.service.sign_in({'password':'test-password'})
        await self.service.list_channels()
        self.client = self.service.client

    async def asyncTearDown(self):
        await self.service._disconnect()

    def clock_patches(self):
        return (patch('telethon.requestiter.time.time',lambda:self.client.clock.now),
                patch('telethon.requestiter.asyncio.sleep',self.client.clock.sleep))

    async def test_real_iterator_uses_100_row_batches_and_live_interval(self):
        time_patch, sleep_patch = self.clock_patches()
        with time_patch, sleep_patch:
            scan = await self.service.start_scan({'channel_id':'101', 'limit':500})
            self.assertEqual(scan['request_interval'],1)
            await self.client.first_request.wait()
            updated = await self.service.set_scan_speed({'scan_id':scan['scan_id'], 'request_interval':2})
            self.assertEqual(updated['scan_id'],scan['scan_id'])
            await self.service.scan_task
        result = await self.service.results()
        self.assertEqual(len(result['messages']),500)
        self.assertEqual(self.service.scan['status'],'done')
        self.assertEqual([r['limit'] for r in self.client.history_requests],[100]*5)
        self.assertEqual([r['time'] for r in self.client.history_requests],[1000,1002,1004,1006,1008])
        self.assertEqual(len({row['id'] for row in result['messages']}),500)

    async def test_thirty_second_pacing_and_network_time_do_not_delay_each_message(self):
        self.client.network_latency = 4
        time_patch, sleep_patch = self.clock_patches()
        with time_patch, sleep_patch:
            await self.service.start_scan({'channel_id':'101','limit':500,'request_interval':30})
            await self.service.scan_task
        self.assertEqual(self.service.scan['count'],500)
        self.assertEqual(self.service.scan['status'],'done')
        self.assertEqual([r['time'] for r in self.client.history_requests],[1000,1030,1060,1090,1120])

    async def test_cancel_during_batch_wait_preserves_one_batch_and_stops_requests(self):
        waiting = asyncio.Event()
        real_sleep = asyncio.sleep
        async def sleep(delay):
            if delay > 0:
                waiting.set()
                await asyncio.Event().wait()
            else:
                await real_sleep(0)
        with patch('telethon.requestiter.time.time',lambda:self.client.clock.now), patch('telethon.requestiter.asyncio.sleep',sleep):
            await self.service.start_scan({'channel_id':'101','limit':500,'request_interval':30})
            await asyncio.wait_for(waiting.wait(),2)
            stopped = await self.service.cancel_scan()
        self.assertEqual(stopped['status'],'cancelled')
        self.assertEqual(stopped['count'],100)
        self.assertEqual(len(self.client.history_requests),1)

    async def test_validation_auth_and_stale_task_cannot_change_speed(self):
        self.assertEqual(parse_scan_interval({}),1)
        for value in (0.5,1,2,5,10,30):
            self.assertEqual(parse_scan_interval({'request_interval':value}),value)
        for value in (None,True,'5',0,-1,31,float('nan'),float('inf')):
            with self.subTest(value=value), self.assertRaises(AppError):
                await self.service.start_scan({'channel_id':'101','limit':500,'request_interval':value})
        time_patch,sleep_patch=self.clock_patches()
        with time_patch,sleep_patch:
            scan=await self.service.start_scan({'channel_id':'101','limit':500,'request_interval':5})
            with self.assertRaises(AppError) as raised:
                await self.service.set_scan_speed({'scan_id':'stale-task','request_interval':30})
            self.assertEqual(raised.exception.status,409)
            self.assertEqual(self.service.scan['request_interval'],5)
            await self.service.scan_task
        with self.assertRaises(AppError):
            await self.service.set_scan_speed({'scan_id':scan['scan_id'],'request_interval':1})
        await self.service._disconnect()
        with self.assertRaises(AppError) as raised:
            await self.service.set_scan_speed({'scan_id':scan['scan_id'],'request_interval':1})
        self.assertEqual(raised.exception.status,401)
