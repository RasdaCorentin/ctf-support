import asyncio
import tempfile
from datetime import datetime, timezone
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import discord

from support import bot
from support import storage as store
from support import tickets as tk
from support.models import SEEN, WAITING, TicketStatus


def _target(author="auteur"):
    target = SimpleNamespace(id=42, author=author)
    target.reply = mock.AsyncMock()
    return target


class PublishTest(unittest.TestCase):
    def test_refused_text_is_never_sent(self):
        target = _target()
        with mock.patch.object(bot, "DRY_RUN", False):
            published, reasons = asyncio.run(bot.publish(target, "le flag est bsides{abcdef0123456789}"))
        self.assertFalse(published)
        self.assertEqual(reasons, ("flag",))
        target.reply.assert_not_awaited()

    def test_too_long_text_is_never_sent(self):
        target = _target()
        with mock.patch.object(bot, "DRY_RUN", False):
            published, reasons = asyncio.run(bot.publish(target, "x" * (bot.MAX_REPLY + 1)))
        self.assertEqual((published, reasons), (False, ("too_long",)))
        target.reply.assert_not_awaited()

    def test_http_error_is_reported_not_raised(self):
        target = _target()
        target.reply.side_effect = discord.HTTPException(SimpleNamespace(status=500, reason="boom"), "boom")
        with mock.patch.object(bot, "DRY_RUN", False):
            result = asyncio.run(bot.publish(target, "Regarde le cookie."))
        self.assertEqual(result, (False, ("http",)))

    def test_valid_text_is_sent_with_mentions_restricted_to_author(self):
        target = _target(author="auteur")
        with mock.patch.object(bot, "DRY_RUN", False):
            result = asyncio.run(bot.publish(target, "Regarde le cookie."))
        self.assertEqual(result, (True, ()))
        target.reply.assert_awaited_once()
        kwargs = target.reply.await_args.kwargs
        self.assertTrue(kwargs["mention_author"])
        self.assertFalse(kwargs["allowed_mentions"].everyone)
        self.assertEqual(kwargs["allowed_mentions"].users, ["auteur"])

    def test_dry_run_logs_without_sending(self):
        target = _target()
        self.assertTrue(bot.DRY_RUN)  # défaut : aucune publication réelle
        result = asyncio.run(bot.publish(target, "Regarde le cookie."))
        self.assertEqual(result, (True, ()))
        target.reply.assert_not_awaited()


def _ticket_message(text="bug sur la page", author_id=7):
    message = mock.Mock()
    message.author = SimpleNamespace(id=author_id, bot=False, roles=[], display_name="joueur")
    message.guild = object()
    message.channel = SimpleNamespace(id=10)
    message.content = text
    message.id = 555
    message.jump_url = "https://discord.test/jump"
    message.add_reaction = mock.AsyncMock()
    message.remove_reaction = mock.AsyncMock()
    message.reply = mock.AsyncMock()
    return message


class FormatTest(unittest.TestCase):
    def test_format_dm_truncates_question_and_keeps_link(self):
        text = bot.format_dm("x" * 900, 7, "https://discord.test/jump")
        self.assertIn("https://discord.test/jump", text)
        self.assertNotIn("x" * 501, text)
        self.assertIn("<@7>", text)

    def test_format_dm_stays_under_discord_limit_with_many_lines(self):
        question = "a\n" * 400
        text = bot.format_dm(question, 7, "https://discord.test/jump", draft="b" * 180, note="✅ envoyé : " + "c" * 180)
        self.assertLess(len(text), 2000)

    def test_parse_custom_id(self):
        self.assertEqual(bot.parse_custom_id("ticket:send:123"), ("send", 123))
        self.assertEqual(bot.parse_custom_id("ticket:close:9"), ("close", 9))
        self.assertIsNone(bot.parse_custom_id("ticket:drop:123"))
        self.assertIsNone(bot.parse_custom_id("ticket:send:abc"))
        self.assertIsNone(bot.parse_custom_id("autre:send:123"))

    def test_view_disables_send_without_draft_and_all_when_closed(self):
        active = bot.ticket_view(5, active=True, has_draft=False)
        disabled = {item.action: item.item.disabled for item in active.children}
        self.assertEqual(disabled, {"send": True, "edit": False, "close": False})
        closed = bot.ticket_view(5, active=False, has_draft=True)
        self.assertTrue(all(item.item.disabled for item in closed.children))


class TicketFlowTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state = Path(self._tmp.name)
        patches = [
            mock.patch.object(store, "STATE_DIR", self.state),
            mock.patch.object(bot, "CHANNEL_IDS", frozenset({10})),
            mock.patch.object(bot, "DRY_RUN", False),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.dm = mock.Mock(id=900)
        self.dm.edit = mock.AsyncMock()
        self.owner = mock.Mock(id=1, bot=False)
        self.owner.send = mock.AsyncMock(return_value=self.dm)
        self.owner.dm_channel.fetch_message = mock.AsyncMock(return_value=self.dm)
        patch = mock.patch.object(bot, "OWNER", self.owner)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self._tmp.cleanup)

    def react(self, message, user_id=1, emoji=SEEN):
        """👀 (par défaut du propriétaire, id 1) posé sur `message` dans le salon tickets."""
        asyncio.run(self.react_async(message, user_id, emoji))

    async def react_async(self, message, user_id=1, emoji=SEEN):
        channel = mock.Mock()
        channel.fetch_message = mock.AsyncMock(return_value=message)
        payload = SimpleNamespace(emoji=emoji, user_id=user_id, channel_id=10, message_id=message.id)
        with mock.patch.object(bot.client, "get_channel", return_value=channel):
            await bot.client.on_raw_reaction_add(payload)

    def test_channel_message_alone_opens_nothing(self):
        message = _ticket_message("Salut, t'as pris les carottes ?")
        asyncio.run(bot.client.on_message(message))
        self.owner.send.assert_not_awaited()
        message.add_reaction.assert_not_awaited()
        self.assertEqual(store.load_tickets(), {})

    def test_channel_message_is_never_published(self):
        message = _ticket_message("bsides{abcdef0123456789}")
        self.react(message)
        message.reply.assert_not_awaited()
        message.add_reaction.assert_any_await(SEEN)
        message.add_reaction.assert_any_await(WAITING)
        self.owner.send.assert_awaited_once()

    def test_owner_eye_opens_a_waiting_ticket(self):
        message = _ticket_message("bug sur la page")
        self.react(message)
        ticket = tk.find(store.load_tickets(), 900)
        self.assertIsNotNone(ticket)
        self.assertEqual(ticket.status, TicketStatus.WAITING)
        self.assertEqual((ticket.channel_id, ticket.message_id, ticket.author_id), (10, 555, 7))

    def test_authorized_user_eye_opens_a_ticket(self):
        with mock.patch.object(bot, "TRIGGER_USER_IDS", frozenset({50})):
            self.react(_ticket_message(), user_id=50)
        self.assertIsNotNone(tk.find(store.load_tickets(), 900))

    def test_player_eye_or_other_emoji_opens_nothing(self):
        message = _ticket_message()
        self.react(message, user_id=7)
        self.react(message, emoji="👍")
        self.owner.send.assert_not_awaited()
        self.assertEqual(store.load_tickets(), {})

    def test_message_written_by_a_bot_opens_nothing(self):
        message = _ticket_message()
        message.author.bot = True
        self.react(message)
        self.owner.send.assert_not_awaited()

    def test_second_eye_opens_no_second_ticket(self):
        message = _ticket_message()
        self.react(message)
        self.react(message)
        self.owner.send.assert_awaited_once()

    def test_simultaneous_eyes_open_one_ticket(self):
        message = _ticket_message()

        async def both():
            await asyncio.gather(self.react_async(message), self.react_async(message, user_id=50))

        with mock.patch.object(bot, "TRIGGER_USER_IDS", frozenset({50})):
            asyncio.run(both())
        self.owner.send.assert_awaited_once()
        self.assertEqual(len(store.load_tickets()), 1)

    def test_dry_run_adds_no_reaction(self):
        message = _ticket_message()
        with mock.patch.object(bot, "DRY_RUN", True):
            self.react(message)
        message.add_reaction.assert_not_awaited()

    def test_relay_publishes_once_and_marks_answered(self):
        store.save_tickets(tk.open_ticket({}, dm_id=900, channel_id=10, message_id=555, author_id=7,
                                          question="q", challenge_key=None, now="2026-10-22T10:00:00+00:00"))
        original = _ticket_message()
        channel = mock.Mock()
        channel.fetch_message = mock.AsyncMock(return_value=original)
        reply = _ticket_message("Regarde le cookie.")
        reply.author = self.owner
        reply.guild = None
        reply.reference = SimpleNamespace(message_id=900)
        reply.channel.send = mock.AsyncMock()
        reply.content = "Regarde le cookie."
        with mock.patch.object(bot.client, "get_channel", return_value=channel), \
                mock.patch.object(bot, "apply_reactions", mock.AsyncMock()) as reactions:
            asyncio.run(bot.client.on_message(reply))
            asyncio.run(bot.client.on_message(reply))
        original.reply.assert_awaited_once()
        reactions.assert_awaited_once_with(original, TicketStatus.ANSWERED)
        self.assertEqual(tk.find(store.load_tickets(), 900).status, TicketStatus.ANSWERED)
        reply.channel.send.assert_awaited_once_with("Ce ticket est déjà traité.")


    def test_unexpected_error_is_reported_to_owner(self):
        message = _ticket_message()
        with mock.patch.object(bot, "apply_reactions", mock.AsyncMock(side_effect=RuntimeError("boom"))):
            self.react(message)
        self.owner.send.assert_awaited_once()
        self.assertEqual(self.owner.send.await_args.args[0], "Erreur sur message 555 du salon : RuntimeError")

    def test_concurrent_answers_publish_once(self):
        store.save_tickets(tk.open_ticket({}, dm_id=900, channel_id=10, message_id=555, author_id=7,
                                          question="q", challenge_key=None, now="2026-10-22T10:00:00+00:00"))
        original = _ticket_message()

        async def race():
            return await asyncio.gather(
                bot.answer_ticket(900, "Regarde le cookie."),
                bot.answer_ticket(900, "Autre réponse."),
                return_exceptions=True,
            )

        with mock.patch.object(bot, "fetch_ticket_message", mock.AsyncMock(return_value=original)), \
                mock.patch.object(bot, "refresh_dm", mock.AsyncMock()):
            results = asyncio.run(race())
        original.reply.assert_awaited_once()
        self.assertEqual(sum(isinstance(r, bot.AlreadyHandled) for r in results), 1)
        self.assertEqual(tk.find(store.load_tickets(), 900).status, TicketStatus.ANSWERED)

    def test_purge_keeps_recent_and_waiting_tickets(self):
        old = "2026-01-01T00:00:00+00:00"
        now = datetime.now(timezone.utc).isoformat()
        tickets = tk.open_ticket({}, dm_id=1, channel_id=10, message_id=1, author_id=7,
                                 question="vieux en attente", challenge_key=None, now=old)
        tickets = tk.open_ticket(tickets, dm_id=2, channel_id=10, message_id=2, author_id=7,
                                 question="vieux clos", challenge_key=None, now=old)
        tickets = tk.close(tickets, 2)
        tickets = tk.open_ticket(tickets, dm_id=3, channel_id=10, message_id=3, author_id=7,
                                 question="récent clos", challenge_key=None, now=now)
        tickets = tk.close(tickets, 3)
        store.save_tickets(tickets)
        asyncio.run(bot.purge_old_tickets())
        self.assertEqual(set(store.load_tickets()), {1, 3})


class IsTriggerTest(unittest.TestCase):
    def trigger(self, emoji="👀", user_id=1, channel_id=10):
        return bot.is_trigger(emoji, user_id, channel_id, channels=frozenset({10, 11}), allowed=frozenset({50}),
                              owner_id=1)

    def test_eye_from_owner_or_authorized_user_in_any_ticket_channel(self):
        self.assertTrue(self.trigger())
        self.assertTrue(self.trigger(user_id=50))
        self.assertTrue(self.trigger(channel_id=11))  # second salon tickets

    def test_anything_else_is_not_a_trigger(self):
        self.assertFalse(self.trigger(user_id=7))  # joueur
        self.assertFalse(self.trigger(emoji="👍"))
        self.assertFalse(self.trigger(channel_id=12))  # salon hors liste

    def test_parse_ids_accepts_commas_and_spaces(self):
        self.assertEqual(bot.parse_ids("50, 60 70,"), frozenset({50, 60, 70}))
        self.assertEqual(bot.parse_ids(""), frozenset())


if __name__ == "__main__":
    unittest.main()
