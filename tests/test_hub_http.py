import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from http.client import HTTPConnection
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hub_server import make_handler
from state_store import StateStore


class LiveHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.old=os.environ.get('CCG_STATE_ROOT')
        os.environ['CCG_STATE_ROOT']=self.tmp.name
        root=Path(self.tmp.name)/'live project';root.mkdir()
        self.store=StateStore(root)
        self.store.set_chat_enabled(True)
        self.csrf='test-csrf-secret'
        self.secret='test-login-secret'
        self.server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(self.store,self.secret,self.csrf))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.port=self.server.server_port

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(3)
        if self.old is None:os.environ.pop('CCG_STATE_ROOT',None)
        else:os.environ['CCG_STATE_ROOT']=self.old
        self.tmp.cleanup()

    def req(self,method,path,body=None,headers=None):
        con=HTTPConnection('127.0.0.1',self.port,timeout=3)
        con.request(method,path,body=body,headers=headers or {})
        result=con.getresponse()
        code=result.status
        raw=result.read()
        out=dict(result.getheaders())
        con.close()
        return code,out,raw

    def test_local_auth_csrf_and_real_ui(self):
        code,_,_=self.req('GET','/')
        self.assertEqual(code,401)
        code,headers,_=self.req('GET','/login?token='+self.secret)
        self.assertEqual(code,303)
        cookie=headers['Set-Cookie'].split(';')[0]
        h={'Cookie':cookie}
        code,_,ui=self.req('GET','/',headers=h)
        self.assertEqual(code,200)
        self.assertIn(b'Continuity Hub',ui)
        code,_,js=self.req('GET','/app.js',headers=h)
        self.assertEqual(code,200)
        self.assertIn(b'/api/messages',js)
        code,_,resp=self.req('GET','/api/session',headers=h)
        self.assertEqual(code,200)
        self.assertEqual(json.loads(resp)['csrf'],self.csrf)
        content=json.dumps({'department':'dept-1','body':'Hello from operator'})
        h2={**h,'Content-Type':'application/json','Origin':f'http://127.0.0.1:{self.port}'}
        code,_,_=self.req('POST','/api/operator-message',body=content,headers=h2)
        self.assertEqual(code,403)
        h2['X-CCG-CSRF']=self.csrf
        code,_,response=self.req('POST','/api/operator-message',body=content,headers=h2)
        self.assertEqual(code,201)
        self.assertEqual(json.loads(response)['delivery'],'SAVED_NOT_SENT_TO_CODEX')
        code,_,result=self.req('GET','/api/messages?department=dept-1',headers=h)
        self.assertEqual(code,200)
        self.assertEqual(json.loads(result)['messages'][0]['role'],'operator')

    def test_departments_empty_until_used_and_existing_history_preserved(self):
        h = {'Cookie': 'CCG_SESSION=' + self.secret}
        code, _, result = self.req('GET', '/api/state', headers=h)
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(result)['departments'], [])
        code, _, ui = self.req('GET', '/', headers=h)
        self.assertNotIn(b'dept-1', ui)
        self.assertNotIn(b'dept-5', ui)
        self.assertIn(b'id="departments"', ui)
        for i in range(1, 6):
            self.store.post_message(f'dept-{i}', 'sol', 'sol', 'Existing record')
        self.store.post_message('research', 'sol', 'sol', 'Custom record')
        code, _, result = self.req('GET', '/api/state', headers=h)
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(result)['departments'],
                         [f'dept-{i}' for i in range(1, 6)] + ['research'])
        code, _, result = self.req('GET', '/api/messages?department=all', headers=h)
        self.assertEqual(code, 200)
        self.assertEqual(len(json.loads(result)['messages']), 6)

if __name__=='__main__':unittest.main()
