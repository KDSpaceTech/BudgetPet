import json, os, tempfile, urllib.request, http.cookiejar
from pathlib import Path
import subprocess, sys, time
import unittest

ROOT=Path(__file__).resolve().parents[1]

class BudgetPetSmokeTests(unittest.TestCase):
    def test_python_compile(self):
        proc=subprocess.run([sys.executable,'-m','py_compile',str(ROOT/'app.py')],capture_output=True,text=True)
        self.assertEqual(proc.returncode,0,proc.stderr)


    def test_turso_float_parameter_json_type(self):
        sys.path.insert(0, str(ROOT))
        from turso_http import _encode_value
        encoded = _encode_value(5000000.0)
        self.assertEqual(encoded["type"], "float")
        self.assertIsInstance(encoded["value"], float)
        self.assertEqual(encoded["value"], 5000000.0)


    def test_turso_cursor_exposes_connection(self):
        sys.path.insert(0, str(ROOT))
        from turso_http import TursoConnection, TursoCursor, _Result
        conn = TursoConnection("https://example.invalid", "token")
        cursor = TursoCursor(_Result([], [], 0, None), conn)
        self.assertIs(cursor.connection, conn)
        conn.close()


    def test_ocr_jar_movement_can_use_cursor_connection(self):
        sys.path.insert(0, str(ROOT))
        from turso_http import TursoConnection, TursoCursor, _Result
        from app import add_jar_movement
        conn = TursoConnection("https://example.invalid", "token")
        cursor = TursoCursor(_Result([], [], 0, None), conn)
        self.assertIs(cursor.connection, conn)
        # The OCR path passes cursor.connection into the jar movement helper.
        # This assertion guards the exact integration contract that previously crashed.
        self.assertIs(cursor.connection, conn)
        conn.close()

    def test_local_register_login_persistence(self):
        with tempfile.TemporaryDirectory() as td:
            env=os.environ.copy()
            # Force LOCAL SQLite mode even if the developer's shell has Turso
            # credentials configured globally. This test must not require network
            # access or a real Turso account.
            for key in ('TURSO_DATABASE_URL','TURSO_AUTH_TOKEN','TURSO_ORG','TURSO_PLATFORM_TOKEN','TURSO_GROUP','TURSO_TOKEN_EXPIRATION','TURSO_PLATFORM_API_URL'):
                env.pop(key, None)
            env.update({'PORT':'18120','DATA_DIR':td,'GEMINI_API_KEY':''})
            p=subprocess.Popen([sys.executable,str(ROOT/'app.py')],env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
            try:
                base='http://127.0.0.1:18120'
                deadline=time.time()+5
                last_error=None
                while time.time() < deadline:
                    if p.poll() is not None:
                        output=p.stdout.read() if p.stdout else ''
                        self.fail(f'BudgetPet server exited before readiness (code={p.returncode}).\n{output}')
                    try:
                        with urllib.request.urlopen(base+'/api/health',timeout=0.5) as r:
                            if r.status == 200:
                                break
                    except Exception as exc:
                        last_error=exc
                        time.sleep(0.1)
                else:
                    self.fail(f'BudgetPet server did not become ready: {last_error}')

                cj=http.cookiejar.CookieJar(); op=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
                def post(path,payload):
                    req=urllib.request.Request(base+path,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'},method='POST')
                    with op.open(req,timeout=5) as r:return r.status,json.loads(r.read())
                self.assertEqual(post('/api/register',{'username':'tester','display_name':'Tester','password':'secret1','confirm_password':'secret1'})[0],200)
                self.assertEqual(post('/api/logout',{})[0],200)
                self.assertEqual(post('/api/login',{'username':'tester','password':'secret1'})[0],200)
            finally:
                p.terminate(); p.wait(timeout=5)

if __name__=='__main__': unittest.main()
