"""skill-gap-planner の依存ゼロ回帰テスト。"""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).with_name("server.py")
SPEC = importlib.util.spec_from_file_location("skill_gap_server", MODULE_PATH)
server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server)

INSTALLER_PATH = Path(__file__).with_name("install_launchagent.py")
INSTALLER_SPEC = importlib.util.spec_from_file_location("skill_gap_installer", INSTALLER_PATH)
installer = importlib.util.module_from_spec(INSTALLER_SPEC)
INSTALLER_SPEC.loader.exec_module(installer)


class StateTest(unittest.TestCase):
    def test_default_state_has_expected_collections(self):
        self.assertEqual(server.default_state(), {"version": 1, "revision": 0, "jobs": [], "skills": [], "sales_leads": []})

    def test_state_is_saved_atomically_and_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            value = {"version": 99, "revision": 0, "jobs": [{"id": "j1", "raw_text": "求人本文"}], "skills": [{"id": "s1", "name": "Python", "status": "todo"}], "sales_leads": []}
            with mock.patch.object(server, "ROOT", Path(tmp)), mock.patch.object(server, "DATA_FILE", path):
                revision = server.save_state(value, expected_revision=0)
                loaded = server.load_state()
            self.assertEqual(revision, 1)
            self.assertEqual(loaded["version"], 1)
            self.assertEqual(loaded["jobs"][0]["id"], "j1")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["skills"][0]["id"], "s1")

    def test_invalid_state_is_rejected(self):
        with self.assertRaises(ValueError):
            server.save_state({"jobs": "not-a-list", "skills": [], "sales_leads": []})

    def test_legacy_state_gets_empty_sales_leads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text(json.dumps({"version": 1, "revision": 0, "jobs": [], "skills": []}), encoding="utf-8")
            with mock.patch.object(server, "DATA_FILE", path):
                loaded = server.load_state()
        self.assertEqual(loaded["sales_leads"], [])

    def test_invalid_sales_lead_status_is_rejected(self):
        state = server.default_state()
        state["sales_leads"] = [{"id": "l1", "company": "Example", "status": "unknown"}]
        with self.assertRaisesRegex(ValueError, "営業候補"):
            server.validate_state(state)

    def test_stale_revision_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            value = {"version": 1, "revision": 0, "jobs": [], "skills": [], "sales_leads": []}
            with mock.patch.object(server, "ROOT", Path(tmp)), mock.patch.object(server, "DATA_FILE", path):
                server.save_state(value, expected_revision=0)
                with self.assertRaises(server.StateConflictError):
                    server.save_state(value, expected_revision=0)


class OpenAITest(unittest.TestCase):
    def test_extracts_nested_responses_api_text(self):
        body = {"output": [{"content": [{"type": "output_text", "text": '{"ok":true}'}]}]}
        self.assertEqual(server.extract_output_text(body), '{"ok":true}')

    def test_short_job_posting_is_rejected_before_api_call(self):
        with self.assertRaisesRegex(ValueError, "30文字"):
            server.analyze_job({"text": "短すぎます"})

    def test_missing_api_key_has_actionable_error(self):
        with mock.patch.object(server, "get_openai_api_key", return_value=""):
            with self.assertRaisesRegex(RuntimeError, "APIキー"):
                server.call_openai("指示", "入力", "schema", {"type": "object"})

    def test_invalid_key_is_rejected_before_keychain_access(self):
        with self.assertRaisesRegex(ValueError, "形式"):
            server.save_openai_api_key("not-a-key")

    def test_api_request_disables_storage(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def read(self):
                return b'{"output_text":"{}"}'

        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True):
            with mock.patch.object(server.urllib.request, "urlopen", return_value=FakeResponse()) as opened:
                server.call_openai("指示", "入力", "schema", {"type": "object"})
        request = opened.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertIs(payload["store"], False)

    def test_chat_is_not_duplicated_inside_skill_context(self):
        payload = {"skill": {"id": "s1", "name": "Python", "chat": [{"role": "user", "text": "古い会話"}]},
                   "message": "相談", "history": [{"role": "user", "text": "直近"}]}
        with mock.patch.object(server, "call_openai", return_value={}) as called:
            server.refine_plan(payload)
        context = json.loads(called.call_args.args[1])
        self.assertNotIn("chat", context["対象スキル"])
        self.assertEqual(len(context["直近の会話"]), 1)

    def test_job_search_uses_web_search_and_filters_invalid_url(self):
        parsed = {"search_summary": "候補", "jobs": [
            {"title": "有効", "url": "https://jobs.example.com/1"},
            {"title": "検索根拠なし", "url": "https://made-up.example/2"},
        ]}
        raw = {"output": [{"type": "web_search_call", "results": [
            {"title": "求人", "url": "https://jobs.example.com/1"},
        ]}]}
        with mock.patch.object(server, "call_openai", return_value=(parsed, raw)) as called:
            result = server.search_jobs({"keyword": "TypeScript", "areas": ["東京都"], "contract": "正社員"})
        self.assertEqual([x["title"] for x in result["jobs"]], ["有効"])
        self.assertEqual(called.call_args.kwargs["tools"], [{"type": "web_search"}])
        self.assertEqual(called.call_args.kwargs["model"], "gpt-5.5")
        self.assertEqual(called.call_args.kwargs["include"], ["web_search_call.action.sources"])
        self.assertTrue(called.call_args.kwargs["return_raw"])
        self.assertEqual(called.call_args.kwargs["tool_choice"], "required")

    def test_job_search_rejects_short_keyword(self):
        with self.assertRaisesRegex(ValueError, "2〜100文字"):
            server.search_jobs({"keyword": "T", "areas": []})

    def test_job_search_enforces_twelve_result_limit(self):
        jobs = [{"title": str(i), "url": f"https://jobs.example.com/{i}"} for i in range(15)]
        raw = {"output": [{"type": "web_search_call", "action": {"sources": [
            {"url": x["url"]} for x in jobs
        ]}}]}
        with mock.patch.object(server, "call_openai", return_value=({"search_summary": "候補", "jobs": jobs}, raw)):
            result = server.search_jobs({"keyword": "TypeScript", "areas": []})
        self.assertEqual(len(result["jobs"]), 12)

    def test_job_url_comparison_normalizes_safe_variations(self):
        source = "https://JOBS.example.com:443/post/123/?utm_source=search&a=1#details"
        candidate = "https://jobs.example.com/post/123?a=1"
        self.assertEqual(server.canonical_job_url(source), server.canonical_job_url(candidate))
        self.assertNotEqual(server.canonical_job_url(source), server.canonical_job_url("https://jobs.example.com/post/999?a=1"))


class RequestGuardTest(unittest.TestCase):
    def test_only_expected_local_origin_is_allowed(self):
        handler = object.__new__(server.Handler)
        handler.headers = {"Host": f"127.0.0.1:{server.PORT}", "Origin": f"http://127.0.0.1:{server.PORT}"}
        self.assertTrue(handler.request_is_local())
        handler.headers = {"Host": f"127.0.0.1:{server.PORT}", "Origin": "https://example.com"}
        self.assertFalse(handler.request_is_local())


class LaunchAgentTest(unittest.TestCase):
    def test_launchagent_is_local_and_does_not_embed_api_key(self):
        config = installer.build_config(Path("/usr/bin/python3"), Path("/repo/server.py"), Path("/repo"), Path("/tmp/app.log"))
        self.assertTrue(config["RunAtLoad"])
        self.assertTrue(config["KeepAlive"])
        self.assertEqual(config["EnvironmentVariables"]["SKILL_GAP_PORT"], "8766")
        self.assertEqual(config["EnvironmentVariables"]["NO_BROWSER"], "1")
        self.assertNotIn("OPENAI_API_KEY", config["EnvironmentVariables"])


if __name__ == "__main__":
    unittest.main()
