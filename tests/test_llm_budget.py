"""growth-lab S2 (llm_client) wired into the BCF writer (MASTER_PLAN #6, plan-bcf action 2).

* Gemini backup: 404/429/5xx/timeout/empty -> next Gemini model now; one back-off pass if every
  model failed for a passing reason; 400/401/403 raise at once (run 39810389929 died on the first 503).
* Groq budget ledger (per model, per UTC day): writer calls record their tokens, a TPD 429 marks the
  model spent, and the cloud run stops BEFORE rendering when the writer (with no Gemini backup) or
  the upload gate's review model has no budget left.
No network: llm_client._http_post is faked.
"""
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import llm_client  # noqa: E402
import story_generator as sg  # noqa: E402


def _ok(text="VERDICT"):
    return 200, {}, json.dumps({"choices": [{"message": {"content": text}}]})


class _Transport:
    """Fake llm_client._http_post: answers come from a list of (status, headers, text) or callables."""

    def __init__(self, answers):
        self.answers, self.models = list(answers), []

    def __call__(self, url, body, headers, timeout):
        self.models.append(body["model"])
        a = self.answers.pop(0)
        return a(body) if callable(a) else a


class GeminiFallThrough(unittest.TestCase):
    def setUp(self):
        mock.patch.object(sg, "GOOGLE_API_KEY", "k", create=True).start()
        mock.patch.dict(os.environ, {"GOOGLE_API_KEY": "k", "GROQ_API_KEY": "g"}).start()
        mock.patch.object(sg, "_groq_sibling_call", side_effect=RuntimeError("tokens per day")).start()
        self.sleeps = []
        mock.patch.object(sg.time, "sleep", self.sleeps.append).start()

    def tearDown(self):
        mock.patch.stopall()

    def _run(self, answers):
        t = _Transport(answers)
        with mock.patch.object(llm_client, "_http_post", t):
            resp = sg._fallback_call(model="openai/gpt-oss-120b", max_tokens=50,
                                     messages=[{"role": "user", "content": "hi"}])
        return resp, t

    def test_503_then_200_on_the_next_model(self):
        resp, t = self._run([(503, {}, "high demand"), _ok()])
        self.assertEqual(resp.choices[0].message.content, "VERDICT")
        self.assertEqual(t.models, list(sg.GEMINI_MODELS[:2]))
        self.assertEqual(self.sleeps, [])

    def test_all_503_then_200_after_one_back_off(self):
        n = len(sg.GEMINI_MODELS)
        resp, t = self._run([(503, {}, "busy")] * n + [_ok()])
        self.assertEqual(resp.choices[0].message.content, "VERDICT")
        self.assertEqual(len(self.sleeps), 1)
        self.assertLessEqual(self.sleeps[0], 30)

    def test_401_raises_without_trying_other_models(self):
        with self.assertRaises(RuntimeError) as cm:
            self._run([(401, {}, "bad key"), _ok()])
        self.assertIn("401", str(cm.exception))

    def test_every_model_404_raises_every_model_unavailable(self):
        with self.assertRaises(RuntimeError) as cm:
            self._run([(404, {}, "gone")] * len(sg.GEMINI_MODELS))
        self.assertIn("every model unavailable", str(cm.exception))
        self.assertEqual(self.sleeps, [])          # a retired model is never waited for

    def test_never_sends_groq_models_to_gemini_or_groq(self):
        resp, t = self._run([_ok()])
        self.assertEqual(t.models, [sg.GEMINI_MODELS[0]])


def _rate_limit(msg):
    import httpx
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    return sg.RateLimitError(msg, response=httpx.Response(429, request=req), body=None)


def _reply(text="ok", tokens=1234):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
                           usage=SimpleNamespace(total_tokens=tokens))


class BudgetLedger(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(self.path)
        sg.enable_budget(self.path)
        mock.patch.object(sg, "_use_fallback", False).start()
        mock.patch.object(sg, "_dead_groq_siblings", set()).start()

    def tearDown(self):
        mock.patch.stopall()
        sg.enable_budget(None)
        for p in (self.path, self.path + ".tmp"):
            if os.path.exists(p):
                os.remove(p)

    def _ledger(self):
        with open(self.path, encoding="utf-8") as fh:
            return json.load(fh)["models"]

    def test_a_writer_call_records_its_tokens(self):
        client = mock.Mock()
        client.chat.completions.create.return_value = _reply(tokens=1500)
        sg._groq_call(client, model="openai/gpt-oss-120b", messages=[])
        self.assertEqual(self._ledger()["openai/gpt-oss-120b"]["tokens"], 1500)

    def test_a_daily_429_marks_the_model_spent_with_the_org_figure(self):
        client = mock.Mock()
        client.chat.completions.create.side_effect = _rate_limit(
            "Rate limit reached for model `openai/gpt-oss-120b` on tokens per day (TPD): "
            "Limit 200000, Used 199800, Requested 1200.")
        with mock.patch.object(sg, "_fallback_call", return_value=_reply()):
            sg._groq_call(client, model="openai/gpt-oss-120b", messages=[])
        m = self._ledger()["openai/gpt-oss-120b"]
        self.assertTrue(m["exhausted"])
        self.assertEqual(m["org_used"], 199800)

    def test_a_spent_primary_is_skipped_without_a_call(self):
        sg.groq_budget().mark_exhausted("openai/gpt-oss-120b")
        client = mock.Mock()
        with mock.patch.object(sg, "_fallback_call", return_value=_reply("fb")) as fb:
            resp = sg._groq_call(client, model="openai/gpt-oss-120b", messages=[])
        client.chat.completions.create.assert_not_called()
        fb.assert_called_once()
        self.assertEqual(resp.choices[0].message.content, "fb")

    def test_a_spent_sibling_is_skipped(self):
        sg.groq_budget().mark_exhausted("openai/gpt-oss-20b")
        client = mock.Mock()
        with mock.patch.object(sg, "Groq", return_value=client):
            with self.assertRaises(RuntimeError) as cm:
                sg._groq_sibling_call(model="x", messages=[])
        client.chat.completions.create.assert_not_called()
        self.assertIn("budget", str(cm.exception).lower())

    def test_review_tokens_from_the_gate_are_recorded(self):
        import upload_gate
        sg.record_review({"ai": {"available": True, "tokens": 6100}})
        self.assertEqual(self._ledger()[upload_gate.AI_MODEL]["tokens"], 6100)
        sg.record_review({"ai": {"available": False, "quota": True}})
        self.assertTrue(self._ledger()[upload_gate.AI_MODEL]["exhausted"])


class Preflight(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(self.path)
        sg.enable_budget(self.path)

    def tearDown(self):
        mock.patch.stopall()
        sg.enable_budget(None)
        for p in (self.path, self.path + ".tmp"):
            if os.path.exists(p):
                os.remove(p)

    def _spend_writers(self):
        b = sg.groq_budget()
        for m in [sg.GROQ_MODEL] + [m for m, _ in sg.GROQ_SIBLING_MODELS]:
            b.mark_exhausted(m)

    def test_fresh_budget_is_ok(self):
        self.assertTrue(sg.writer_preflight().ok)

    def test_disabled_ledger_never_blocks(self):
        sg.enable_budget(None)
        self.assertTrue(sg.writer_preflight().ok)

    def test_spent_writers_without_gemini_stop_the_run(self):
        self._spend_writers()
        with mock.patch.object(sg, "GOOGLE_API_KEY", "", create=True):
            res = sg.writer_preflight()
        self.assertFalse(res.ok)
        self.assertIn("budget exhausted", res.reason.lower())

    def test_spent_writers_with_gemini_carry_on_on_the_backup(self):
        self._spend_writers()
        with mock.patch.object(sg, "GOOGLE_API_KEY", "k", create=True):
            res = sg.writer_preflight()
        self.assertTrue(res.ok)
        self.assertIn("gemini", res.reason.lower())

    def test_spent_review_model_stops_the_run(self):
        import upload_gate
        sg.groq_budget().mark_exhausted(upload_gate.AI_MODEL)
        with mock.patch.object(sg, "GOOGLE_API_KEY", "k", create=True):
            res = sg.writer_preflight()
        self.assertFalse(res.ok)
        self.assertIn(upload_gate.AI_MODEL, res.reason)


class CloudRunStopsBeforeRendering(unittest.TestCase):
    def test_no_render_when_preflight_fails(self):
        import main_documentary as md
        import notify
        import tiktok_buffer
        out = tempfile.NamedTemporaryFile(delete=False)
        out.close()
        made = []
        try:
            for target, attr, val in [
                (md, "_generate_one", lambda: made.append(1) or ({}, "x.mp4")),
                (notify, "send_alert", lambda *a, **k: True),
                (tiktok_buffer, "is_configured", lambda: True),
                (sg, "writer_preflight", lambda: SimpleNamespace(
                    ok=False, reason="Groq budget exhausted: need 9000 tokens on one of [qwen=0]")),
                (md, "_app", lambda *a, **k: None),
            ]:
                mock.patch.object(target, attr, val).start()
            mock.patch.dict(os.environ, {"GITHUB_OUTPUT": out.name, "GITHUB_EVENT_NAME": "workflow_dispatch"}).start()
            md.run_cloud_deliver(count=1)
            self.assertEqual(made, [])
            with open(out.name, encoding="utf-8") as fh:
                self.assertIn("catchup=false", fh.read())
        finally:
            mock.patch.stopall()
            os.remove(out.name)


if __name__ == "__main__":
    unittest.main()
