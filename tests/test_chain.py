"""Chaîne de modèles du rédacteur, hors ligne : session HTTP factice, horloge injectée."""

import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest import mock

import requests

from support.drafter.chain import (DEFAULT_LIMITS, DEFAULT_MODELS, LLMError, ModelChain, next_quota_reset,
                                   parse_quota_error)

DAILY_QUOTA = "GenerateRequestsPerDayPerProjectPerModel-FreeTier"
MINUTE_QUOTA = "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"
T0 = datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc)  # 03:00 à Los Angeles (heure d'été)


def gemini_429(quota_id: str, wrap: bool = False) -> str:
    """Un corps 429 de la forme de Gemini : QuotaFailure, éventuellement enveloppé dans une liste."""
    details = [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                "violations": [{"quotaMetric": "x", "quotaId": quota_id}]}]
    body = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": details}}
    return json.dumps([body] if wrap else body)


DAILY_429 = gemini_429(DAILY_QUOTA)
MINUTE_429 = gemini_429(MINUTE_QUOTA)


class FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "", content: str = "{}", finish: str = "stop",
                 usage=None, malformed: bool = False):
        self.status_code = status_code
        self.text = text
        self._content = content
        self._finish = finish
        self._usage = usage
        self._malformed = malformed

    def json(self):
        if self._malformed:
            raise ValueError("pas du JSON")
        body = {"choices": [{"message": {"content": self._content}, "finish_reason": self._finish}]}
        if self._usage is not None:
            body["usage"] = self._usage
        return body


class FakeSession:
    """Une file de réponses par modèle ; enregistre chaque appel."""

    def __init__(self, **replies):
        self.replies = {model: list(r) for model, r in replies.items()}
        self.models_called: list[str] = []
        self.headers: list[dict] = []
        self.timeouts: list = []
        self.payloads: list[dict] = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.models_called.append(json["model"])
        self.headers.append(headers)
        self.timeouts.append(timeout)
        self.payloads.append(json)
        reply = self.replies[json["model"]].pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class FakeClock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self):
        return self.now


class EnvTestCase(unittest.TestCase):
    """Isole les variables SUPPORT_LLM_* de l'environnement réel."""

    def setUp(self):
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in list(os.environ):
            if key.startswith("SUPPORT_LLM_"):
                del os.environ[key]


class FromEnvTest(EnvTestCase):
    def test_defaults_to_the_drafter_chain_and_the_eight_second_read_timeout(self):
        chain = ModelChain.from_env(session=FakeSession())
        self.assertEqual(chain.models, list(DEFAULT_MODELS))
        self.assertEqual(chain.timeout, (1.0, 8.0))

    def test_models_come_from_the_ordered_list(self):
        os.environ["SUPPORT_LLM_MODELS"] = "a, b,,c"
        self.assertEqual(ModelChain.from_env(session=FakeSession()).models, ["a", "b", "c"])

    def test_timeout_is_the_read_timeout_and_falls_back_on_garbage(self):
        os.environ["SUPPORT_LLM_TIMEOUT"] = "5"
        self.assertEqual(ModelChain.from_env(session=FakeSession()).timeout, (1.0, 5.0))
        os.environ["SUPPORT_LLM_TIMEOUT"] = "beaucoup"
        self.assertEqual(ModelChain.from_env(session=FakeSession()).timeout, (1.0, 8.0))

    def test_empty_chain_is_rejected(self):
        with self.assertRaises(ValueError):
            ModelChain([])


class CompleteTest(unittest.TestCase):
    def chain(self, models, session, clock=None):
        return ModelChain(models, session=session, clock=clock or FakeClock(T0))

    def test_first_model_answers(self):
        s = FakeSession(a=[FakeResponse(content="ok")], b=[])
        chain = self.chain(["a", "b"], s)
        self.assertEqual(chain.complete_with_model([]), ("ok", "a"))
        self.assertEqual(s.models_called, ["a"])

    def test_read_timeout_goes_to_the_next_model_at_once(self):
        s = FakeSession(a=[requests.ReadTimeout("lent")], b=[FakeResponse(content="b")])
        chain = self.chain(["a", "b"], s)
        self.assertEqual(chain.complete([]), "b")
        self.assertEqual(s.models_called, ["a", "b"])
        self.assertEqual(chain.exhausted, {})  # un délai n'écarte pas le modèle

    def test_per_minute_429_goes_to_the_next_model_and_keeps_the_model(self):
        s = FakeSession(a=[FakeResponse(429, MINUTE_429), FakeResponse(content="a")],
                        b=[FakeResponse(content="b")])
        chain = self.chain(["a", "b"], s)
        self.assertEqual(chain.complete([]), "b")
        self.assertEqual(chain.complete([]), "a")
        self.assertEqual(s.models_called, ["a", "b", "a"])

    def test_server_and_connection_errors_go_to_the_next_model(self):
        s = FakeSession(a=[FakeResponse(503), requests.ConnectionError("down")],
                        b=[FakeResponse(content="b"), FakeResponse(content="b")])
        chain = self.chain(["a", "b"], s)
        self.assertEqual(chain.complete([]), "b")
        self.assertEqual(chain.complete([]), "b")
        self.assertEqual(s.models_called, ["a", "b", "a", "b"])
        self.assertEqual(chain.exhausted, {})

    def test_other_4xx_skips_the_model_for_this_call_only(self):
        s = FakeSession(a=[FakeResponse(400), FakeResponse(content="back")], b=[FakeResponse(content="b")])
        chain = self.chain(["a", "b"], s)
        self.assertEqual(chain.complete([]), "b")
        self.assertEqual(chain.complete([]), "back")
        self.assertEqual(s.models_called, ["a", "b", "a"])

    def test_daily_quota_excludes_the_model_until_pacific_midnight(self):
        clock = FakeClock(T0)
        s = FakeSession(a=[FakeResponse(429, DAILY_429), FakeResponse(content="back")],
                        b=[FakeResponse(content="1"), FakeResponse(content="2"), FakeResponse(content="3")])
        chain = self.chain(["a", "b"], s, clock)
        self.assertEqual(chain.complete([]), "1")
        clock.now = datetime(2026, 10, 22, 16, 0, tzinfo=timezone.utc)  # 09:00 à Los Angeles
        self.assertEqual(chain.complete([]), "2")
        clock.now = datetime(2026, 10, 23, 6, 59, tzinfo=timezone.utc)  # 23:59 à Los Angeles, la veille
        self.assertEqual(chain.complete([]), "3")
        self.assertEqual(s.models_called, ["a", "b", "b", "b"])  # a n'a pas été rappelé
        clock.now = datetime(2026, 10, 23, 7, 0, tzinfo=timezone.utc)  # minuit à Los Angeles
        self.assertEqual(chain.complete([]), "back")
        self.assertEqual(s.models_called[-1], "a")

    def test_daily_quota_in_list_wrapped_body_is_recognised(self):
        s = FakeSession(a=[FakeResponse(429, gemini_429(DAILY_QUOTA, wrap=True))], b=[FakeResponse(content="b")])
        chain = self.chain(["a", "b"], s)
        self.assertEqual(chain.complete([]), "b")
        self.assertIn("a", chain.exhausted)

    def test_not_found_excludes_the_model_until_pacific_midnight(self):
        clock = FakeClock(T0)
        s = FakeSession(a=[FakeResponse(404, '{"error": {"message": "no longer available"}}')],
                        b=[FakeResponse(content="1"), FakeResponse(content="2")])
        chain = self.chain(["a", "b"], s, clock)
        self.assertEqual(chain.complete([]), "1")
        self.assertEqual(chain.complete([]), "2")
        self.assertEqual(s.models_called, ["a", "b", "b"])
        self.assertEqual(chain.exhausted["a"], datetime(2026, 10, 23, 7, 0, tzinfo=timezone.utc))

    def test_content_filter_goes_to_the_next_model(self):
        s = FakeSession(a=[FakeResponse(content="", finish="content_filter")], b=[FakeResponse(content="b")])
        self.assertEqual(self.chain(["a", "b"], s).complete([]), "b")

    def test_empty_content_goes_to_the_next_model(self):
        s = FakeSession(a=[FakeResponse(content="   ")], b=[FakeResponse(content="b")])
        self.assertEqual(self.chain(["a", "b"], s).complete([]), "b")

    def test_malformed_response_goes_to_the_next_model(self):
        s = FakeSession(a=[FakeResponse(malformed=True)], b=[FakeResponse(content="b")])
        self.assertEqual(self.chain(["a", "b"], s).complete([]), "b")

    def test_accept_refusal_goes_to_the_next_model(self):
        s = FakeSession(a=[FakeResponse(content="non")], b=[FakeResponse(content="oui")])
        chain = self.chain(["a", "b"], s)
        self.assertEqual(chain.complete_with_model([], accept=lambda raw: raw == "oui"), ("oui", "b"))

    def test_all_models_failing_raises_with_each_reason(self):
        s = FakeSession(a=[FakeResponse(429, DAILY_429)], b=[FakeResponse(500)])
        with self.assertRaisesRegex(LLMError, "a: .*quota du jour.*b: .*HTTP 500"):
            self.chain(["a", "b"], s).complete([])

    def test_response_format_defaults_to_json_mode_and_is_passed_through(self):
        schema = {"type": "json_schema", "json_schema": {"name": "r", "schema": {"type": "object"}, "strict": True}}
        s = FakeSession(a=[FakeResponse(), FakeResponse()])
        chain = self.chain(["a"], s)
        chain.complete([])
        chain.complete([], response_format=schema)
        self.assertEqual(s.payloads[0]["response_format"], {"type": "json_object"})
        self.assertEqual(s.payloads[1]["response_format"], schema)

    def test_timeout_is_one_second_to_connect_and_eight_to_read(self):
        s = FakeSession(a=[FakeResponse()])
        self.chain(["a"], s).complete([])
        self.assertEqual(s.timeouts, [(1.0, 8.0)])

    def test_api_key_is_sent_as_bearer_only_when_set(self):
        s = FakeSession(a=[FakeResponse(), FakeResponse()])
        ModelChain(["a"], session=s, api_key="k").complete([])
        ModelChain(["a"], session=s, api_key="").complete([])
        self.assertEqual(s.headers, [{"Authorization": "Bearer k"}, {}])

    def test_usage_counts_answered_requests_per_model(self):
        s = FakeSession(a=[FakeResponse(usage={"prompt_tokens": 100, "completion_tokens": 20}),
                           FakeResponse(usage={"prompt_tokens": 50, "completion_tokens": 5}),
                           FakeResponse(429, DAILY_429)],
                        b=[FakeResponse()])
        chain = self.chain(["a", "b"], s)
        for _ in range(3):
            chain.complete([])
        self.assertEqual(chain.usage, {
            "a": {"requests": 2, "prompt_tokens": 150, "completion_tokens": 25},
            "b": {"requests": 1, "prompt_tokens": 0, "completion_tokens": 0},
        })


class LocalLimitsTest(unittest.TestCase):
    """RPM et RPD suivis localement : un modèle à sa limite n'est pas appelé."""

    def test_model_at_its_per_minute_limit_is_skipped_without_a_call(self):
        clock = FakeClock(T0)
        s = FakeSession(a=[FakeResponse(content="a")] * 6, b=[FakeResponse(content="b")])
        chain = ModelChain(["a", "b"], session=s, clock=clock, limits={"a": (5, 20)})
        for _ in range(5):
            self.assertEqual(chain.complete([]), "a")
        self.assertEqual(chain.complete([]), "b")
        self.assertEqual(s.models_called, ["a"] * 5 + ["b"])
        clock.now = T0 + timedelta(seconds=61)  # la fenêtre d'une minute s'est vidée
        self.assertEqual(chain.complete([]), "a")

    def test_refused_attempts_count_towards_the_per_minute_limit(self):
        s = FakeSession(a=[FakeResponse(429, MINUTE_429), FakeResponse(429, MINUTE_429)],
                        b=[FakeResponse(content="b")] * 3)
        chain = ModelChain(["a", "b"], session=s, clock=FakeClock(T0), limits={"a": (2, 20)})
        for _ in range(3):
            self.assertEqual(chain.complete([]), "b")
        self.assertEqual(s.models_called, ["a", "b", "a", "b", "b"])  # a sauté au 3e appel

    def test_model_at_its_per_day_limit_is_skipped_until_pacific_midnight(self):
        clock = FakeClock(T0)
        s = FakeSession(a=[FakeResponse(content="a")] * 2, b=[FakeResponse(content="b")] * 3)
        chain = ModelChain(["a", "b"], session=s, clock=clock, limits={"a": (1000, 2)})
        self.assertEqual(chain.complete([]), "a")
        clock.now = T0 + timedelta(hours=2)
        self.assertEqual(chain.complete([]), "a")
        clock.now = T0 + timedelta(hours=4)
        self.assertEqual(chain.complete([]), "b")
        clock.now = datetime(2026, 10, 23, 7, 0, tzinfo=timezone.utc)  # minuit à Los Angeles
        s.replies["a"].append(FakeResponse(content="a"))
        self.assertEqual(chain.complete([]), "a")

    def test_default_limits_match_the_free_tier_table(self):
        self.assertEqual(ModelChain(list(DEFAULT_MODELS), session=FakeSession()).limits, DEFAULT_LIMITS)


    def test_concurrent_calls_never_exceed_the_per_minute_limit(self):
        # Rédactions parallèles (asyncio.to_thread) : la vérification et la réservation sont atomiques
        s = FakeSession(a=[FakeResponse(content="a")] * 40, b=[FakeResponse(content="b")] * 40)
        chain = ModelChain(["a", "b"], session=s, clock=FakeClock(T0), limits={"a": (5, 20)})
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: chain.complete_with_model([]), range(40)))
        self.assertEqual(sum(1 for _, model in results if model == "a"), 5)
        self.assertTrue(all(content == model for content, model in results))


class QuotaParsingTest(unittest.TestCase):
    def test_parse_quota_error_tolerates_every_shape(self):
        cases = [
            (DAILY_429, [DAILY_QUOTA], True),
            (gemini_429(MINUTE_QUOTA, wrap=True), [MINUTE_QUOTA], False),
            ('{"error": {"details": [{"quotaId": "%s"}]}}' % MINUTE_QUOTA, [MINUTE_QUOTA], False),
            ('{"error": {"details": "nope"}}', [], False),
            ("[]", [], False),
            ("Quota exceeded: GenerateRequestsPerDay (not JSON)", [], True),
            ("", [], False),
            (None, [], False),
        ]
        for text, ids, daily in cases:
            with self.subTest(text=text):
                info = parse_quota_error(text)
                self.assertEqual((info.quota_ids, info.daily), (ids, daily))

    def test_next_quota_reset_is_the_next_pacific_midnight(self):
        self.assertEqual(next_quota_reset(datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc)),
                         datetime(2026, 10, 23, 7, 0, tzinfo=timezone.utc))
        self.assertEqual(next_quota_reset(datetime(2026, 10, 22, 6, 59, tzinfo=timezone.utc)),
                         datetime(2026, 10, 22, 7, 0, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
