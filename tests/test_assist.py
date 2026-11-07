import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from support import assist
from support import bot
from support import storage as store
from support import tickets as tk
from support.drafter import FakeDrafter
from support.drafter.chain import ModelChain
from support.models import Draft, FaqEntry, Guide, Mode, Step, TicketStatus


def _guide(key: str, title: str) -> Guide:
    step = Step(id="s1", goal="trouver la page", signs=(), nudge="regarde le code", pitfalls=())
    return Guide(key=key, slug=key.split("/", 1)[1], title=title, category=key.split("/", 1)[0],
                 description="énoncé", steps=(step,), forbidden=(), reviewed=True)


GUIDES = {
    "web/sqli": _guide("web/sqli", "SQL injection"),
    "web/xss": _guide("web/xss", "Cross-site scripting"),
}


class BuildContextTest(unittest.TestCase):
    def test_known_challenge_gives_its_guide_and_no_titles(self):
        context = assist.build_context("bloqué", "web/sqli", GUIDES, [], ())
        self.assertIs(context.guide, GUIDES["web/sqli"])
        self.assertEqual(context.titles, ())

    def test_unknown_challenge_gives_all_titles_and_no_guide(self):
        context = assist.build_context("bloqué", None, GUIDES, [], ())
        self.assertIsNone(context.guide)
        self.assertEqual(context.titles, ("web/sqli : SQL injection", "web/xss : Cross-site scripting"))

    def test_faq_entries_with_empty_answer_are_dropped(self):
        faq = [
            FaqEntry(keywords=("horaire",), answer="Le CTF commence à 9 h."),
            FaqEntry(keywords=("vide",), answer="   "),
        ]
        context = assist.build_context("quand ?", None, GUIDES, faq, ())
        self.assertEqual(context.faq, (faq[0],))

    def test_history_and_question_are_passed_as_is(self):
        context = assist.build_context("et maintenant ?", "web/sqli", GUIDES, [], ("premier", "deuxième"))
        self.assertEqual(context.question, "et maintenant ?")
        self.assertEqual(context.history, ("premier", "deuxième"))


def _message(text: str, author_id: int = 7):
    message = SimpleNamespace(id=555, content=text, jump_url="https://discord.test/jump")
    message.author = SimpleNamespace(id=author_id, bot=False, roles=[])
    message.channel = SimpleNamespace(id=10)
    return message


class DraftFlowTest(unittest.TestCase):
    """Brouillon dans le DM de validation, bout en bout hors ligne, avec FakeDrafter."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state = Path(self._tmp.name)
        self.dm = mock.Mock(id=900)
        self.dm.edit = mock.AsyncMock()
        self.dm.reply = mock.AsyncMock()
        self.owner = mock.Mock(id=1, bot=False)
        self.owner.send = mock.AsyncMock(return_value=self.dm)
        self.owner.dm_channel.fetch_message = mock.AsyncMock(return_value=self.dm)
        patches = [
            mock.patch.object(store, "STATE_DIR", self.state),
            mock.patch.object(bot, "DRY_RUN", True),
            mock.patch.object(bot, "OWNER", self.owner),
            mock.patch.object(bot, "GUIDES", dict(GUIDES)),
            mock.patch.object(bot, "FAQ", []),
            mock.patch.object(bot, "LLM_MAX_QUEUE", 10),
            mock.patch.object(bot, "DRAFTER", None),  # restauré après chaque test, comme les autres globaux
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def _run(self, drafter, text="bloqué sur sqli", *, close_before_draft=False):
        """Ouvre le ticket comme forward_to_owner, puis attend la tâche de brouillon."""
        message = _message(text)
        bot.DRAFTER = drafter

        async def scenario():
            await bot.forward_to_owner(message)
            if close_before_draft:
                # Clos à la main pendant la rédaction, comme close_ticket le ferait
                store.save_tickets(tk.close(store.load_tickets(), 900))
            await asyncio.gather(*tuple(bot._DRAFT_TASKS))

        asyncio.run(scenario())
        return store.load_tickets()

    def test_draft_is_stored_and_shown_with_mode_step_and_model(self):
        drafter = FakeDrafter([Draft(mode=Mode.HINT, challenge_key=None, step_id="s1",
                                     reply="Regarde le paramètre id.", model="gemini-test")])
        tickets = self._run(drafter)
        ticket = tk.find(tickets, 900)
        self.assertEqual(ticket.draft, "Regarde le paramètre id.")
        self.assertEqual(ticket.mode, Mode.HINT)
        self.assertEqual(ticket.challenge_key, "web/sqli")
        self.assertEqual(drafter.contexts[0].guide, GUIDES["web/sqli"])
        content = self.dm.edit.await_args_list[-1].kwargs.get("content")
        self.assertIn("Regarde le paramètre id.", content)
        self.assertIn("Mode : indice", content)
        self.assertIn("trouver la page", content)
        self.assertIn("gemini-test", content)

    def test_no_draft_tells_owner_with_a_new_message(self):
        tickets = self._run(FakeDrafter([None]))
        self.assertIsNone(tk.find(tickets, 900).draft)
        self.assertIn(bot.NO_DRAFT_NOTE, self.dm.edit.await_args_list[-1].kwargs["content"])
        self.dm.reply.assert_awaited_once()
        self.assertIn("https://discord.test/jump", self.dm.reply.await_args.args[0])
        self.assertEqual(self.owner.send.await_count, 1)  # le DM du ticket seul, aucune erreur signalée

    def test_draft_arriving_after_manual_close_touches_nothing(self):
        drafter = FakeDrafter([Draft(mode=Mode.HINT, challenge_key=None, step_id="s1", reply="Indice.")])
        tickets = self._run(drafter, close_before_draft=True)
        ticket = tk.find(tickets, 900)
        self.assertEqual(ticket.status, TicketStatus.CLOSED)
        self.assertIsNone(ticket.draft)
        self.assertEqual(self.dm.edit.await_count, 1)  # seul le réglage des boutons à l'ouverture
        self.dm.reply.assert_not_awaited()

    def test_second_call_with_the_identified_guide_replaces_the_first_draft(self):
        drafter = FakeDrafter([
            Draft(mode=Mode.HINT, challenge_key="web/sqli", step_id=None, reply="Premier brouillon, sans guide."),
            Draft(mode=Mode.HINT, challenge_key="web/sqli", step_id="s1", reply="Second brouillon, guidé."),
        ])
        tickets = self._run(drafter, text="bonjour, j'ai une question")
        self.assertEqual(len(drafter.contexts), 2)
        self.assertIsNone(drafter.contexts[0].guide)
        self.assertEqual(drafter.contexts[1].guide, GUIDES["web/sqli"])
        self.assertEqual(tk.find(tickets, 900).draft, "Second brouillon, guidé.")

    def test_failed_second_call_keeps_the_first_draft(self):
        drafter = FakeDrafter([
            Draft(mode=Mode.HINT, challenge_key="web/sqli", step_id=None, reply="Premier brouillon, sans guide."),
            None,
        ])
        tickets = self._run(drafter, text="bonjour, j'ai une question")
        self.assertEqual(len(drafter.contexts), 2)
        self.assertEqual(tk.find(tickets, 900).draft, "Premier brouillon, sans guide.")
        self.dm.reply.assert_not_awaited()

    def test_draft_refused_by_guard_is_never_shown_nor_stored(self):
        flag = "Le flag est bsides{abcdef0123456789}"
        drafter = FakeDrafter([Draft(mode=Mode.DEBUG, challenge_key=None, step_id="s1", reply=flag)])
        tickets = self._run(drafter)
        self.assertIsNone(tk.find(tickets, 900).draft)
        content = self.dm.edit.await_args_list[-1].kwargs["content"]
        self.assertIn("refusé par la garde", content)
        self.assertNotIn("abcdef0123456789", content)
        view = self.dm.edit.await_args_list[-1].kwargs["view"]
        disabled = {item.action: item.item.disabled for item in view.children}
        self.assertTrue(disabled["send"])  # Envoyer reste désactivé, Modifier reste disponible
        self.assertFalse(disabled["edit"])
        events = (self.state / "events.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("abcdef0123456789", events)

    def test_unexpected_error_in_draft_is_reported_to_owner(self):
        drafter = mock.Mock(draft=mock.AsyncMock(side_effect=RuntimeError("boom")))
        self._run(drafter)
        self.assertEqual(self.owner.send.await_args_list[-1].args[0],
                         "Erreur sur brouillon du ticket 900 : RuntimeError")

    def test_full_queue_skips_the_draft_and_says_so(self):
        drafter = FakeDrafter([Draft(mode=Mode.HINT, challenge_key=None, step_id=None, reply="Indice.")])
        with mock.patch.object(bot, "LLM_MAX_QUEUE", 0):
            tickets = self._run(drafter)
        self.assertEqual(drafter.contexts, [])
        self.assertIsNone(tk.find(tickets, 900).draft)
        self.assertIn(bot.FULL_QUEUE_NOTE, self.dm.edit.await_args_list[-1].kwargs["content"])



class StartupTest(unittest.TestCase):
    """LLM_ENABLED=1 : la clé est exigée au démarrage, et une seule chaîne est créée. Rien ne se connecte."""

    def _main(self, env):
        # main() écrit des globaux : chacun est restauré à la sortie du test
        names = ("CHANNEL_IDS", "DRY_RUN", "TRIGGER_USER_IDS", "LLM_CHAIN", "LLM_CONCURRENCY", "LLM_MAX_QUEUE",
                 "_DRAFT_SLOTS")
        patches = [mock.patch.object(bot, name, getattr(bot, name)) for name in names]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(bot, "load_env"), mock.patch.object(bot.client, "run") as run:
            bot.main()
        return run

    def test_llm_enabled_without_key_refuses_to_start_without_showing_it(self):
        env = {"TICKETS_CHANNEL_IDS": "10", "DISCORD_TOKEN": "secret-token", "LLM_ENABLED": "1"}
        with self.assertRaises(SystemExit) as raised:
            self._main(env)
        self.assertIn("SUPPORT_LLM_API_KEY", str(raised.exception))
        self.assertNotIn("secret-token", str(raised.exception))

    def test_no_ticket_channel_refuses_to_start(self):
        with self.assertRaises(SystemExit) as raised:
            self._main({"TICKETS_CHANNEL_IDS": " ", "DISCORD_TOKEN": "t"})
        self.assertIn("TICKETS_CHANNEL_IDS", str(raised.exception))

    def test_llm_enabled_with_key_builds_one_chain(self):
        env = {"TICKETS_CHANNEL_IDS": "10", "DISCORD_TOKEN": "t", "LLM_ENABLED": "1",
               "SUPPORT_LLM_API_KEY": "k-test"}
        with mock.patch.object(bot, "LLM_CHAIN", None):
            run = self._main(env)
            self.assertIsInstance(bot.LLM_CHAIN, ModelChain)
        run.assert_called_once_with("t")

    def test_llm_disabled_builds_no_chain(self):
        with mock.patch.object(bot, "LLM_CHAIN", None):
            self._main({"TICKETS_CHANNEL_IDS": "10,11", "DISCORD_TOKEN": "t"})
            self.assertIsNone(bot.LLM_CHAIN)


if __name__ == "__main__":
    unittest.main()
