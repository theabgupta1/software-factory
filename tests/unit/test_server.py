"""Factory Console server: API shape, caching, and the security model (Host, token, Origin, paths, CSP)."""

from __future__ import annotations

import http.client
import json
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from support import Driver, RepoCase  # noqa: E402

from factory_core.server import make_server  # noqa: E402
from factory_core.store import Store  # noqa: E402


class ConsoleServer(RepoCase):
    def setUp(self):
        super().setUp()
        self.agents.verdicts["security"] = ["CHANGES", "PASS"]
        eng = self.engine()
        self.job = eng.new_job("Rate limit signup", size="S")
        Driver(eng, self.agents).run(self.job["id"])
        self.httpd, self.state = make_server(self.paths, port=0)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def req(self, method, path, body=None, headers=None, host=None, key=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        hdrs = {"Host": host or f"127.0.0.1:{self.port}"}
        if key:
            hdrs["X-Factory-Token"] = self.state.token
        hdrs.update(headers or {})
        data = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
        conn.request(method, path, body=data, headers=hdrs)
        res = conn.getresponse()
        raw = res.read()
        conn.close()
        try:
            parsed = json.loads(raw) if raw else None
        except ValueError:
            parsed = raw.decode("utf-8", "replace")
        return res.status, dict(res.getheaders()), parsed

    def write_headers(self, **extra):
        return {"Content-Type": "application/json", "X-Factory-Token": self.state.token,
                "Origin": f"http://127.0.0.1:{self.port}", **extra}

    # ---- reading -------------------------------------------------------------------------------
    def test_state_lists_jobs_and_supports_etag(self):
        status, headers, data = self.req("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(data["jobs"][0]["id"], self.job["id"])
        self.assertEqual(data["jobs"][0]["status"], "merged")
        status2, _, _ = self.req("GET", "/api/state", headers={"If-None-Match": headers["ETag"]})
        self.assertEqual(status2, 304)

    def test_job_detail_has_every_section(self):
        status, _, data = self.req("GET", f"/api/jobs/{self.job['id']}")
        self.assertEqual(status, 200)
        for key in ("job", "events", "crew", "artifacts", "messages", "stages", "workflow", "quality"):
            self.assertIn(key, data)
        steps = {c["step"] for c in data["crew"]}
        self.assertIn("review.r2/security", steps)
        self.assertEqual([r["result"] for r in data["quality"]["matrix"]], ["changes", "pass"])

    def test_diff_and_artifact(self):
        status, _, diff = self.req("GET", f"/api/jobs/{self.job['id']}/diff")
        self.assertEqual(status, 200)
        self.assertTrue(diff["available"])
        self.assertIn("feature.py", {f["path"] for f in diff["files"]})
        status, _, art = self.req("GET", f"/api/jobs/{self.job['id']}/artifact?path=spec.md")
        self.assertEqual(status, 200)
        self.assertTrue(art["text"].startswith("STATUS: READY"))

    def test_catalog_and_settings(self):
        status, _, cat = self.req("GET", "/api/catalog")
        self.assertEqual(status, 200)
        self.assertEqual(len(cat["agents"]), 25)
        self.assertEqual(cat["workflows"][0]["id"], "feature")
        status, _, settings = self.req("GET", "/api/settings")
        self.assertEqual(status, 200)
        self.assertIn("doctor", settings)
        self.assertTrue(any(r["id"] == "no-push" for r in settings["guard"]))

    def test_index_never_carries_the_key_and_sets_security_headers(self):
        status, headers, body = self.req("GET", "/", key=False)
        self.assertEqual(status, 200)
        self.assertNotIn(self.state.token, body)
        self.assertNotIn("factory-token", body)
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["X-Frame-Options"], "DENY")

    def test_static_assets_and_traversal(self):
        status, headers, _ = self.req("GET", "/assets/js/app.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers["Content-Type"])
        for bad in ("/assets/../lib/factory_core/server.py", "/assets/%2e%2e/guard-policy.json", "/assets/.hidden",
                    "/assets/js/../../../hooks/run.py"):
            with self.subTest(path=bad):
                self.assertEqual(self.req("GET", bad)[0], 404)

    # ---- security ------------------------------------------------------------------------------
    def test_foreign_host_header_is_refused(self):
        for host in ("evil.example", f"evil.example:{self.port}", "127.0.0.1:1"):
            with self.subTest(host=host):
                self.assertEqual(self.req("GET", "/api/state", host=host)[0], 421)

    def test_artifact_paths_are_confined_to_the_job_folder(self):
        job_dir = self.paths.job_dir(self.job["id"])
        outside = self.tmp / "secret.md"
        outside.write_text("secret")
        (job_dir / "link.md").symlink_to(outside)
        for bad in ("../../config.json", "/etc/passwd", "..%2f..%2fx.md", "link.md", "job.json/../spec.md", ""):
            with self.subTest(path=bad):
                status, _, data = self.req("GET", f"/api/jobs/{self.job['id']}/artifact?path={bad}")
                self.assertIn(status, (400, 404), f"{bad} → {status} {data}")
                self.assertNotIn("secret", json.dumps(data))

    def test_bad_and_unknown_job_ids(self):
        self.assertEqual(self.req("GET", "/api/jobs/..%2f..%2fetc")[0], 400)
        self.assertEqual(self.req("GET", "/api/jobs/999-nope")[0], 404)

    def test_every_api_read_needs_the_key(self):
        for path in ("/api/state", f"/api/jobs/{self.job['id']}", f"/api/jobs/{self.job['id']}/diff",
                     f"/api/jobs/{self.job['id']}/artifact?path=spec.md", "/api/catalog", "/api/settings"):
            with self.subTest(path=path):
                status, _, data = self.req("GET", path, key=False)
                self.assertEqual(status, 401)
                self.assertIn("?token=", data["error"])
                self.assertEqual(self.req("GET", path, key=False, headers={"X-Factory-Token": "x" * 32})[0], 401)
        self.assertEqual(self.req("GET", "/assets/js/app.js", key=False)[0], 200, "static files carry no data")

    def test_only_loopback_binds_and_clients(self):
        from factory_core import server
        from factory_core.util import FactoryError

        for addr in ("127.0.0.1", "127.8.9.1", "::1", "::ffff:127.0.0.1", "localhost"):
            self.assertTrue(server.loopback(addr), addr)
        for addr in ("0.0.0.0", "192.168.1.5", "::", "10.0.0.1", "evil.example", ""):
            self.assertFalse(server.loopback(addr), addr)
        with self.assertRaises(FactoryError) as err:
            make_server(self.paths, host="0.0.0.0", port=0)
        self.assertIn("--insecure-host", str(err.exception))

    def test_writes_need_token_origin_and_json(self):
        path = f"/api/jobs/{self.job['id']}/messages"
        body = {"to": {"agent": "builder"}, "text": "hello"}
        no_token = {k: v for k, v in self.write_headers().items() if k != "X-Factory-Token"}
        self.assertEqual(self.req("POST", path, body, no_token, key=False)[0], 401)
        self.assertEqual(self.req("POST", path, body, self.write_headers(**{"X-Factory-Token": "nope"}))[0], 401)
        self.assertEqual(self.req("POST", path, body, self.write_headers(Origin="http://evil.example"))[0], 403)
        self.assertEqual(self.req("POST", path, body, self.write_headers(**{"Content-Type": "text/plain"}))[0], 403)
        self.assertEqual(self.req("POST", path, b"x" * 70000, self.write_headers())[0], 413)
        self.assertEqual(self.req("POST", path, b"{nope", self.write_headers())[0], 400)
        self.assertEqual(self.req("POST", path, {"to": {"agent": "builder"}, "text": "  "}, self.write_headers())[0], 400)
        self.assertEqual(self.req("POST", path, {"to": {"agent": "nobody"}, "text": "x"}, self.write_headers())[0], 400)
        self.assertEqual(Store(self.paths).messages(self.job["id"]), [], "nothing was written by refused requests")

    def test_message_and_backlog_writes(self):
        status, _, msg = self.req("POST", f"/api/jobs/{self.job['id']}/messages",
                                  {"to": {"role": "orchestrator"}, "text": "Pause please"}, self.write_headers())
        self.assertEqual(status, 201)
        stored = Store(self.paths).messages(self.job["id"])
        self.assertEqual(stored[0]["id"], msg["id"])
        self.assertEqual(stored[0]["to"], {"role": "orchestrator"})
        status, _, _ = self.req("POST", "/api/backlog", {"text": "Add dark mode"}, self.write_headers())
        self.assertEqual(status, 201)
        self.assertIn("- [ ] Add dark mode", self.paths.backlog.read_text())
        self.assertEqual(self.req("POST", "/api/backlog", {"text": "a\nb"}, self.write_headers())[0], 400)

    def test_activity_only_serves_transcripts_under_claude_projects(self):
        job_dir = self.paths.job_dir(self.job["id"])
        rogue = self.tmp / "rogue.jsonl"
        rogue.write_text(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "x"}]}}) + "\n")
        with open(job_dir / "telemetry.jsonl", "a") as fh:
            fh.write(json.dumps({"type": "agent.spawned", "agent_id": "a1", "step": "spec", "transcript": str(rogue)}) + "\n")
        status, _, data = self.req("GET", f"/api/jobs/{self.job['id']}/agents/a1/activity")
        self.assertEqual(status, 200)
        self.assertTrue(data["missing"])
        self.assertEqual(data["items"], [])
        self.assertEqual(self.req("GET", f"/api/jobs/{self.job['id']}/agents/zzz/activity")[0], 404)


if __name__ == "__main__":
    unittest.main()
