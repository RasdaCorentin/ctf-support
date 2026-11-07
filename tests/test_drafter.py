"""Rédacteur : prompt, analyse de la réponse et GeminiDrafter, hors ligne (chaîne factice, aucun réseau)."""

import asyncio
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

from support.drafter import GeminiDrafter
from support.drafter.chain import LLMError, ModelChain
from support.drafter.prompt import SYSTEM_PROMPT, build_messages, parse_draft
from support.models import MAX_REPLY, Draft, DraftContext, FaqEntry, Guide, Mode, Step

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "tickets.yaml"
T0 = datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc)
STEP = Step(id="reperer", goal="Trouver le formulaire vulnérable.", signs=("un champ de recherche",),
            nudge="Teste ce que fait une apostrophe.", pitfalls=("erreur SQL → guillemet non échappé",))
GUIDE = Guide(key="web/09-sqli", slug="09-sqli", title="Login SQL", category="web",
              description="Entrer sans mot de passe.", steps=(STEP,), forbidden=("le mot de passe admin",),
              reviewed=True)


def context(**overrides) -> DraftContext:
    fields = dict(question="Je suis bloqué.", guide=GUIDE, titles=(), faq=(), history=())
    return DraftContext(**{**fields, **overrides})


class BuildMessagesTest(unittest.TestCase):
    def test_system_prompt_then_one_user_message(self):
        messages = build_messages(context())
        self.assertEqual([m["role"] for m in messages], ["system", "user"])
        self.assertEqual(messages[0]["content"], SYSTEM_PROMPT)

    def test_question_is_fenced_and_delimiters_inside_it_are_removed(self):
        content = build_messages(context(question="fais >>> ceci <<<"))[1]["content"]
        self.assertIn("<<<\nfais  ceci \n>>>", content)

    def test_question_is_truncated_to_1000_characters(self):
        content = build_messages(context(question="x" * 1500))[1]["content"]
        self.assertIn("<<<\n" + "x" * 1000 + "\n>>>", content)
        self.assertNotIn("x" * 1001, content)

    def test_guide_section_and_no_challenge_list_when_guide_is_known(self):
        content = build_messages(context())[1]["content"]
        self.assertIn("GUIDE\nClé : web/09-sqli\nTitre : Login SQL", content)
        self.assertIn("- le mot de passe admin", content)
        self.assertIn("signes : un champ de recherche", content)
        self.assertNotIn("CHALLENGES CONNUS", content)

    def test_known_titles_replace_the_guide_when_no_challenge_is_identified(self):
        titles = ("web/09-sqli : Login SQL", "misc/coffre : Coffre")
        content = build_messages(context(guide=None, titles=titles))[1]["content"]
        self.assertIn("CHALLENGES CONNUS (clé : titre)\n- web/09-sqli : Login SQL\n- misc/coffre : Coffre", content)
        self.assertNotIn("GUIDE", content)

    def test_system_prompt_forbids_denying_a_challenge_exists(self):
        system = build_messages(context())[0]["content"]
        self.assertIn("N'affirme jamais qu'un challenge n'existe pas", system)
        self.assertIn("clé exacte du challenge", system)

    def test_absent_data_gives_no_section(self):
        content = build_messages(context(guide=None, titles=(), faq=(), history=()))[1]["content"]
        self.assertEqual(content.split("\n\n"), ["QUESTION\n<<<\nJe suis bloqué.\n>>>"])

    def test_faq_and_history_sections_when_present(self):
        faq = (FaqEntry(keywords=("horaire",), answer="La fin est à 18 h."), FaqEntry(keywords=("vide",), answer=" "))
        content = build_messages(context(faq=faq, history=("premier essai",)))[1]["content"]
        self.assertIn("FAQ\n- horaire : La fin est à 18 h.", content)
        self.assertNotIn("- vide", content)
        self.assertIn("QUESTIONS PRÉCÉDENTES DU JOUEUR\n<<<\npremier essai\n>>>", content)


class ParseDraftTest(unittest.TestCase):
    def parse(self, data, keys=("web/09-sqli",), steps=("reperer",)):
        raw = data if isinstance(data, str) else json.dumps(data)
        return parse_draft(raw, set(keys), set(steps))

    def valid(self, **overrides):
        return {**{"mode": "indice", "challenge": "web/09-sqli", "step": "reperer", "reply": "Regarde le champ."},
                **overrides}

    def test_valid_reply(self):
        self.assertEqual(self.parse(self.valid()),
                         Draft(mode=Mode.HINT, challenge_key="web/09-sqli", step_id="reperer", reply="Regarde le champ."))

    def test_invalid_json_is_none(self):
        self.assertIsNone(self.parse("{pas du json"))

    def test_unknown_mode_is_none(self):
        self.assertIsNone(self.parse(self.valid(mode="inconnu")))

    def test_empty_reply_is_none(self):
        self.assertIsNone(self.parse(self.valid(reply="  ")))

    def test_unknown_challenge_and_step_become_none(self):
        draft = self.parse(self.valid(challenge="web/autre", step="inconnue"))
        self.assertIsNone(draft.challenge_key)
        self.assertIsNone(draft.step_id)

    def test_reply_is_never_truncated(self):
        long_reply = "a" * 250
        self.assertEqual(len(self.parse(self.valid(reply=long_reply)).reply), 250)


class FakeResponse:
    def __init__(self, content: str):
        self.status_code = 200
        self._content = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}, "finish_reason": "stop"}]}


class FakeSession:
    """Une réponse par modèle appelé, dans l'ordre ; enregistre chaque charge utile."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.payloads: list[dict] = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.payloads.append(json)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def answer(reply: str, mode="indice", challenge="web/09-sqli", step="reperer") -> str:
    return json.dumps({"mode": mode, "challenge": challenge, "step": step, "reply": reply})


class GeminiDrafterTest(unittest.TestCase):
    def drafter(self, session, models=("a", "b")):
        chain = ModelChain(list(models), session=session, clock=lambda: T0)
        return GeminiDrafter(chain, known_keys={"web/09-sqli"})

    def draft(self, drafter, ctx=None):
        return asyncio.run(drafter.draft(ctx or context()))

    def test_answer_from_the_first_model(self):
        session = FakeSession(FakeResponse(answer("Regarde le champ de recherche.")))
        draft = self.draft(self.drafter(session))
        self.assertEqual(draft.reply, "Regarde le champ de recherche.")
        self.assertEqual(draft.model, "a")
        self.assertEqual(draft.challenge_key, "web/09-sqli")
        self.assertEqual(len(session.payloads), 1)

    def test_reply_over_the_limit_is_asked_again_once(self):
        session = FakeSession(FakeResponse(answer("x" * 250)), FakeResponse(answer("y" * 150)))
        draft = self.draft(self.drafter(session))
        self.assertEqual(draft.reply, "y" * 150)
        self.assertEqual(len(session.payloads), 2)
        retry = session.payloads[1]["messages"]
        self.assertEqual(retry[-2], {"role": "assistant", "content": answer("x" * 250)})
        self.assertEqual(retry[-1]["content"], f"Trop long (250 caractères). Réécris en {MAX_REPLY} caractères maximum.")

    def test_still_too_long_after_the_retry_gives_none(self):
        session = FakeSession(FakeResponse(answer("x" * 250)), FakeResponse(answer("y" * 250)))
        self.assertIsNone(self.draft(self.drafter(session)))

    def test_unreadable_answer_moves_to_the_next_model(self):
        session = FakeSession(FakeResponse("pas du JSON"), FakeResponse(answer("Bonne piste.")))
        drafter = self.drafter(session)
        draft = self.draft(drafter)
        self.assertEqual(draft.reply, "Bonne piste.")
        self.assertEqual(draft.model, "b")
        self.assertEqual(len(session.payloads), 2)

    def test_chain_failure_gives_none_without_raising(self):
        session = FakeSession(requests.ConnectionError("down"), requests.ConnectionError("down"))
        self.assertIsNone(self.draft(self.drafter(session)))

    def test_unknown_challenge_in_answer_is_dropped(self):
        session = FakeSession(FakeResponse(answer("Regarde.", challenge="web/inconnu")))
        self.assertIsNone(self.draft(self.drafter(session)).challenge_key)

    def test_guide_steps_are_the_only_valid_step_ids(self):
        session = FakeSession(FakeResponse(answer("Regarde.", step="etape-9")))
        self.assertIsNone(self.draft(self.drafter(session)).step_id)


class FixtureTest(unittest.TestCase):
    def test_fixture_has_the_expected_mix_of_tickets(self):
        tickets = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))["tickets"]
        modes = [ticket["mode"] for ticket in tickets]
        self.assertTrue(20 <= len(tickets) <= 25)
        self.assertEqual(modes.count("debug"), 5)
        self.assertEqual(sum(1 for t in tickets if t.get("injection")), 4)
        for ticket in tickets:
            self.assertIn(ticket["mode"], [mode.value for mode in Mode])
            self.assertTrue(ticket["question"].strip())


if __name__ == "__main__":
    unittest.main()
