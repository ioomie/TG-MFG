"""Real async proxy handshakes and Telethon sockets; all traffic stays on loopback."""
import asyncio
import logging
import socket
import sys
import unittest
from collections import defaultdict
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import TelegramService, AppError, error_status
from diagnostics import Diagnostics
from network_proxy import parse_proxy, probe_proxy
from telethon.network.connection import connection as transport
from telethon.network.connection.tcpfull import ConnectionTcpFull
from fakes import FakeClient


class ProxyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.targets=[]
        self.failures=[]
        self.server=None
        self.loop=asyncio.get_running_loop()
        self.native_connect=self.loop.sock_connect

    async def asyncTearDown(self):
        self.loop.sock_connect=self.native_connect
        if self.server:
            self.server.close();await self.server.wait_closed()

    async def proxy(self,kind):
        async def handle(reader,writer):
            try:
                if kind=='socks5':
                    version,count=await reader.readexactly(2)
                    self.assertEqual(version,5);await reader.readexactly(count)
                    writer.write(b'\x05\x00');await writer.drain()
                    version,command,_,atyp=await reader.readexactly(4)
                    self.assertEqual((version,command),(5,1))
                    if atyp==1: host=socket.inet_ntoa(await reader.readexactly(4))
                    elif atyp==3: host=(await reader.readexactly((await reader.readexactly(1))[0])).decode()
                    else: raise AssertionError('Unexpected address family')
                    port=int.from_bytes(await reader.readexactly(2),'big')
                    writer.write(b'\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x01')
                else:
                    header=(await reader.readuntil(b'\r\n\r\n')).decode()
                    method,target,_=header.split('\r\n',1)[0].split()
                    self.assertEqual(method,'CONNECT');host,port=target.rsplit(':',1);port=int(port)
                    writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                self.targets.append((host,port))
                await writer.drain()
                data=await reader.read(32)
                if data:
                    self.assertEqual(data,b'proxy-ping');writer.write(b'proxy-pong');await writer.drain()
            except Exception as exc:
                self.failures.append(exc)
            finally:
                writer.close();await writer.wait_closed()
        self.server=await asyncio.start_server(handle,'127.0.0.1',0)
        return parse_proxy({'proxy_enabled':True,'proxy_type':kind,'proxy_host':'127.0.0.1',
                            'proxy_port':self.server.sockets[0].getsockname()[1]})

    def windows_native_connect(self,calls):
        # Proactor ConnectEx uses a socket handle rather than a Python connect override.
        # This wrapper reproduces that behavior on the development Mac.
        original=self.native_connect
        class NativeView:
            def __init__(self,sock): self.sock=sock
            def __getattr__(self,name): return getattr(self.sock,name)
            def connect(self,address): return socket.socket.connect(self.sock,address)
        async def connect(sock,address):
            calls.append(address);sock.setblocking(False)
            try:return await original(sock if sys.platform == "win32" else NativeView(sock),address)
            except Exception:
                sock.close()
                raise
        return connect

    async def tunnel(self,kind):
        proxy=await self.proxy(kind)
        calls=[];self.loop.sock_connect=self.windows_native_connect(calls)
        target=('127.0.0.2',54321)
        connection=ConnectionTcpFull(*target,1,loggers=defaultdict(lambda:logging.getLogger('test')),proxy=proxy)
        sock=await connection._proxy_connect(timeout=2)
        try:
            await self.loop.sock_sendall(sock,b'proxy-ping')
            self.assertEqual(await self.loop.sock_recv(sock,32),b'proxy-pong')
        finally: sock.close()
        await asyncio.sleep(0)
        self.assertEqual(self.targets,[target])
        self.assertEqual(calls,[('127.0.0.1',proxy['port'])])
        self.assertFalse(self.failures)

    async def test_socks5_real_telethon_tunnel_uses_proxy_under_native_socket_semantics(self):
        await self.tunnel('socks5')

    async def test_http_real_telethon_tunnel_uses_proxy_under_native_socket_semantics(self):
        await self.tunnel('http')

    async def test_legacy_fallback_reproduces_direct_connection_bug(self):
        proxy=await self.proxy('socks5')
        reserved=socket.socket();reserved.bind(('127.0.0.1',0));port=reserved.getsockname()[1];reserved.close()
        calls=[];self.loop.sock_connect=self.windows_native_connect(calls)
        connection=ConnectionTcpFull('127.0.0.1',port,1,loggers=defaultdict(lambda:logging.getLogger('test')),proxy=proxy)
        with patch.object(transport,'python_socks',None),self.assertRaises(OSError):
            await connection._proxy_connect(timeout=1)
        self.assertEqual(calls,[('127.0.0.1',port)])
        self.assertEqual(self.targets,[])

    async def test_probe_and_login_pass_the_same_proxy_dictionary(self):
        proxy=await self.proxy('socks5')
        diagnostic=Diagnostics()
        result=await probe_proxy(proxy,diagnostic,dest_host='127.0.0.2',dest_port=54321,timeout=2)
        self.assertTrue(result['ok'])
        self.assertEqual(diagnostic.snapshot()['events'][-1]['result'],'ok')
        service=TelegramService(FakeClient)
        data={'api_id':123,'api_hash':'a'*32,'proxy_enabled':True,'proxy_type':'socks5',
              'proxy_host':'127.0.0.1','proxy_port':proxy['port']}
        try:
            with patch('app.probe_proxy',return_value={'ok':True}) as probe:
                await service.connect(data)
            self.assertEqual(service.client.kwargs['proxy'],proxy)
            probe.assert_awaited_once()
        finally: await service._disconnect()

    async def test_closed_proxy_fails_without_fallback(self):
        proxy=await self.proxy('socks5')
        self.server.close();await self.server.wait_closed()
        with self.assertRaises(Exception) as raised:
            await probe_proxy(proxy,Diagnostics(),dest_host='127.0.0.2',dest_port=54321,timeout=1)
        self.assertEqual(error_status(raised.exception),503)
        self.assertEqual(self.targets,[])

    async def test_invalid_proxy_settings_and_disabled_probe(self):
        for data in ({'proxy_type':'unknown','proxy_host':'127.0.0.1','proxy_port':7890},
                     {'proxy_type':'socks5','proxy_host':'socks5://127.0.0.1','proxy_port':7890},
                     {'proxy_type':'socks5','proxy_host':'127.0.0.1','proxy_port':0}):
            with self.assertRaises(ValueError):parse_proxy({'proxy_enabled':True,**data})
        service=TelegramService(FakeClient)
        with self.assertRaises(AppError):await service.test_proxy({'proxy_enabled':False})


if __name__=='__main__':unittest.main()
