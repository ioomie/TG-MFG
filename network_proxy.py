"""Async proxy configuration and bounded tunnel probe; never falls back to direct TCP."""
import asyncio
import ipaddress
import re
import time
from python_socks import ProxyType
from python_socks.async_.asyncio import Proxy
from telethon.client.telegrambaseclient import DEFAULT_IPV4_IP, DEFAULT_PORT
from diagnostics import error_fields


def parse_proxy(data):
    if not isinstance(data.get('proxy_enabled', False), bool):
        raise ValueError('The proxy switch must be a boolean value.')
    if not data.get('proxy_enabled'):
        return None
    kind = data.get('proxy_type', 'socks5')
    if kind not in ('socks5', 'http'):
        raise ValueError('Only SOCKS5 and HTTP CONNECT proxies are supported.')
    host = str(data.get('proxy_host', '')).strip()
    try:
        port = int(data.get('proxy_port', 0))
    except (TypeError, ValueError):
        raise ValueError('Invalid proxy port format.') from None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?', host):
            raise ValueError('Enter a proxy host such as 127.0.0.1, without a protocol prefix or credentials.') from None
    if not 1 <= port <= 65535:
        raise ValueError('Enter a valid proxy port.')
    return {'proxy_type':kind,'addr':host,'port':port,'rdns':True}


async def probe_proxy(proxy, diagnostics, dest_host=DEFAULT_IPV4_IP, dest_port=DEFAULT_PORT, timeout=8):
    started = time.monotonic()
    metadata = {'proxy_type':proxy['proxy_type'],'proxy_backend':'python-socks',
                'proxy_host':proxy['addr'],'proxy_port':proxy['port']}
    diagnostics.record('proxy_handshake',result='started',**metadata)
    sock = None
    try:
        client = Proxy.create(ProxyType.SOCKS5 if proxy['proxy_type']=='socks5' else ProxyType.HTTP,
                              proxy['addr'],proxy['port'],rdns=True)
        sock = await asyncio.wait_for(client.connect(dest_host=dest_host,dest_port=dest_port,timeout=timeout),timeout+1)
        diagnostics.record('proxy_handshake',result='ok',duration_ms=round((time.monotonic()-started)*1000),**metadata)
        return {'ok':True,'proxy_type':proxy['proxy_type'],'proxy_backend':'python-socks',
                'duration_ms':round((time.monotonic()-started)*1000),
                'message':'The proxy handshake succeeded and a TCP tunnel to Telegram was established. This test does not verify account login.'}
    except Exception as exc:
        diagnostics.record('proxy_handshake',result='error',duration_ms=round((time.monotonic()-started)*1000),**metadata,**error_fields(exc))
        raise
    finally:
        if sock is not None:
            sock.close()
