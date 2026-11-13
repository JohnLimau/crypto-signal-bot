"""Local credentials and end-to-end authentication regression tests."""
import json
import tempfile
import time
import unittest
from pathlib import Path

from aiohttp import ClientSession, CookieJar

from dashboard import start_dashboard
from local_auth import LocalAuth, LOCKOUT_SECONDS
from state import StateStore


DUMMY_PASSWORD = "dummy-only-test-password-123"


class LocalCredentialTests(unittest.TestCase):
    def test_password_is_hashed_salted_and_kept_on_disk_locally(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"data"/"security"/"auth.json"
            LocalAuth.write_credentials(path,"testuser",DUMMY_PASSWORD)
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(DUMMY_PASSWORD,text)
            record = json.loads(text)
            self.assertIn("password_hash",record)
            self.assertEqual(record["algorithm"],"scrypt-n32768-r8-p1")
            auth = LocalAuth(path)
            self.assertTrue(auth.verify("testuser",DUMMY_PASSWORD))
            self.assertFalse(auth.verify("testuser","not-the-password"))
            self.assertFalse(auth.verify("someone",DUMMY_PASSWORD))
            self.assertTrue(path.is_file())

    def test_password_length_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"auth.json"
            with self.assertRaises(ValueError):
                LocalAuth.write_credentials(path,"me","short")
            self.assertFalse(path.exists())
            with self.assertRaises(ValueError):
                LocalAuth.write_credentials(path,"!invalid!",DUMMY_PASSWORD)

    def test_sessions_are_in_ram_revoke_and_expire(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"auth.json"
            LocalAuth.write_credentials(path,"testuser",DUMMY_PASSWORD)
            auth = LocalAuth(path)
            token = auth.issue_session(now=100)
            self.assertTrue(auth.is_authenticated(token,now=101))
            self.assertNotIn(token,path.read_text(encoding="utf-8"))
            self.assertFalse(auth.is_authenticated(token,now=100+8*3600))
            token2 = auth.issue_session(now=100)
            auth.revoke(token2)
            self.assertFalse(auth.is_authenticated(token2,now=101))
            self.assertFalse(LocalAuth(path).is_authenticated(token,now=101))

    def test_missing_credential_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RuntimeError):
                LocalAuth(Path(directory)/"missing.json")

    def test_rate_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"auth.json"
            LocalAuth.write_credentials(path,"testuser",DUMMY_PASSWORD)
            auth=LocalAuth(path)
            for attempt in range(5):
                auth.fail(now=100+attempt)
            self.assertTrue(auth.is_limited(now=106))
            self.assertFalse(auth.is_limited(now=104+LOCKOUT_SECONDS+1))


class AuthenticatedDashboardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        data=Path(self.temp.name)
        self.store=StateStore(data/"signals.sqlite3")
        path=data/"security"/"auth.json"
        LocalAuth.write_credentials(path,"testuser",DUMMY_PASSWORD)
        self.auth=LocalAuth(path)
        status=lambda:{"healthy":True,"ready_markets":1,"connected":True,"symbols":{"BTCUSDT":{}}}
        self.runner=await start_dashboard("127.0.0.1",0,status,self.store.history,self.store,auth=self.auth)
        site=next(iter(self.runner.sites))
        port=site._server.sockets[0].getsockname()[1]
        self.base=f"http://127.0.0.1:{port}"
        self.client=ClientSession(cookie_jar=CookieJar(unsafe=True))

    async def asyncTearDown(self):
        await self.client.close()
        await self.runner.cleanup()
        self.store.close()
        self.temp.cleanup()

    async def post(self,path,values):
        return await self.client.post(self.base+path,json=values,headers={"Origin":self.base})

    async def test_all_dashboard_pages_and_api_are_protected(self):
        async with self.client.get(self.base+"/",allow_redirects=False) as r:
            self.assertEqual(r.status,302)
            self.assertEqual(r.headers["Location"],"/login")
        for path in ("/api/status","/api/history","/api/signals",
                     "/api/performance","/api/export.csv","/api/health",
                     "/api/research/accuracy","/api/research/replay",
                     "/assets/app.js","/assets/research.js","/assets/themes.css",
                     "/api/signals/1/blackbox","/api/auth/me"):
            with self.subTest(path=path):
                async with self.client.get(self.base+path) as r:
                    self.assertEqual(r.status,401)
        for path in ("/login","/assets/login.css","/assets/login.js"):
            async with self.client.get(self.base+path) as r:
                self.assertEqual(r.status,200)

    async def test_login_cookie_and_logout_revocation(self):
        async with self.client.post(self.base+"/api/auth/login",
                                    json={"username":"testuser","password":DUMMY_PASSWORD}) as r:
            self.assertEqual(r.status,403,"Cross-origin protection requires Origin")
        async with await self.post("/api/auth/login",{
                "username":"testuser","password":"wrong password"}) as r:
            self.assertEqual(r.status,401)
        async with await self.post("/api/auth/login",{
                "username":"testuser","password":DUMMY_PASSWORD}) as r:
            self.assertEqual(r.status,200)
            cookie=r.headers.get("Set-Cookie","")
            self.assertIn("HttpOnly",cookie)
            self.assertIn("SameSite=Strict",cookie)
            self.assertNotIn("Secure",cookie,"Loopback HTTP must work for local access")
        async with self.client.get(self.base+"/api/status") as r:
            self.assertEqual(r.status,200)
        async with self.client.get(self.base+"/api/auth/me") as r:
            self.assertEqual((await r.json())["username"],"testuser")
        async with self.client.post(self.base+"/api/auth/logout",headers={"Origin":self.base}) as r:
            self.assertEqual(r.status,200)
        async with self.client.get(self.base+"/api/status") as r:
            self.assertEqual(r.status,401)

    async def test_failed_attempts_throttled(self):
        for attempt in range(5):
            async with await self.post("/api/auth/login",{
                    "username":"testuser","password":"bad"}) as r:
                self.assertEqual(r.status,401)
        async with await self.post("/api/auth/login",{
                "username":"testuser","password":DUMMY_PASSWORD}) as r:
            self.assertEqual(r.status,429)
            self.assertIn("Retry-After",r.headers)


if __name__=="__main__":
    unittest.main()
