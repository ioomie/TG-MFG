import json
import sys
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import LocalServer, Runtime
from diagnostics import CORE_VERSION, Diagnostics, error_fields
from fakes import FakeClient


class DiagnosticTests(unittest.TestCase):
    def test_bounded_whitelisted_log_does_not_include_secrets_or_exception_text(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'debug.log'
            diagnostic = Diagnostics(log, True)
            diagnostic.record('operation', operation='connect', api_hash='private-api-hash', phone='private-phone',
                              password='private-password', body='private-body', token='private-token',
                              **error_fields(OSError(10061, 'private-error-message')))
            text = log.read_text()
            self.assertNotIn('private-', text)
            self.assertIn('10061', text)
            for _ in range(220):
                diagnostic.record('http', route='/api/status', status=200)
            self.assertEqual(len(diagnostic.snapshot()['events']), 200)
            diagnostic.close()

    def test_navigation_allowed_but_cross_site_api_and_iframe_still_rejected(self):
        server = LocalServer(0, Runtime(FakeClient), portal_origin='https://portal.example')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(path, headers=None, method='GET', body=None):
            conn = HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            conn.request(method,path,body=body,headers=headers or {})
            r=conn.getresponse(); status,data=r.status,r.read(); conn.close()
            return status,data
        try:
            navigation={'Sec-Fetch-Site':'cross-site','Sec-Fetch-Mode':'navigate','Sec-Fetch-Dest':'document'}
            self.assertEqual(request('/',navigation)[0],200)
            self.assertEqual(request('/index.html',navigation)[0],200)
            self.assertEqual(request('/',{**navigation,'Sec-Fetch-Dest':'iframe'})[0],403)
            self.assertEqual(request('/api/bootstrap',navigation)[0],403)
            self.assertEqual(request('/api/status',{**navigation,'X-App-Token':server.token})[0],403)
            self.assertEqual(request('/api/diagnostics')[0],403)
            headers={'X-App-Token':server.token,'Content-Type':'application/json'}
            self.assertEqual(request('/api/debug',headers,'POST','{"enabled":true}')[0],200)
            self.assertTrue(server.diagnostics.enabled)
            self.assertEqual(request('/api/debug',headers,'POST','{"enabled":"yes"}')[0],400)
            report=json.loads(request('/api/diagnostics',headers)[1])
            self.assertEqual(report['telegram_stage'],'disconnected')
            self.assertEqual(report['core_version'],CORE_VERSION)
            self.assertNotIn(server.token,json.dumps(report))
            self.assertEqual(report['portal_origin'],'https://portal.example')
            status,_=request('/api/connect',{**headers,**navigation,'Origin':'https://evil.example'},'POST','{}')
            self.assertEqual(status,403)
            self.assertIsNone(server.runtime.service.client)
        finally:
            server.shutdown();server.server_close();server.runtime.close();thread.join(timeout=3)

    def test_runtime_connect_error_records_type_and_errno_only(self):
        class FailingClient(FakeClient):
            async def connect(self):
                raise OSError(10061,'private-secret-value')
        runtime=Runtime(FailingClient)
        try:
            with self.assertRaises(OSError):
                runtime.call('connect',{'api_id':123,'api_hash':'b'*32,'proxy_enabled':False})
            report=json.dumps(runtime.diagnostics.snapshot())
            self.assertIn('10061',report)
            self.assertNotIn('private-secret-value',report)
            self.assertNotIn('b'*32,report)
        finally:
            runtime.close()


if __name__ == '__main__':
    unittest.main()
