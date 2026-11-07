import unittest

from support import tickets as tk
from support.models import Draft, Mode, SEEN, TicketStatus, WAITING


def _open(store, dm_id, *, author=1, challenge="web/a", question="q", now="2026-10-22T10:00:00+00:00"):
    return tk.open_ticket(store, dm_id=dm_id, channel_id=10, message_id=dm_id, author_id=author,
                          question=question, challenge_key=challenge, now=now)


class OpenTicketTest(unittest.TestCase):
    def test_open_then_find_and_original_untouched(self):
        original = {}
        store = _open(original, 7, question="x" * 600)
        self.assertEqual(original, {})
        ticket = tk.find(store, 7)
        self.assertEqual(ticket.status, TicketStatus.WAITING)
        self.assertEqual(ticket.author_id, 1)
        self.assertEqual(ticket.challenge_key, "web/a")
        self.assertEqual(len(ticket.question), tk.QUESTION_MAX)
        self.assertIsNone(tk.find(store, 99))


class SetDraftTest(unittest.TestCase):
    def test_set_draft_fills_fields_and_keeps_status(self):
        store = _open({}, 7, challenge=None)
        draft = Draft(mode=Mode.HINT, challenge_key="web/a", step_id="s2", reply="Regarde le cookie.")
        ticket = tk.find(tk.set_draft(store, 7, draft), 7)
        self.assertEqual(ticket.draft, "Regarde le cookie.")
        self.assertEqual(ticket.mode, Mode.HINT)
        self.assertEqual(ticket.step_id, "s2")
        self.assertEqual(ticket.challenge_key, "web/a")
        self.assertEqual(ticket.status, TicketStatus.WAITING)

    def test_set_draft_none_clears_only_draft(self):
        store = tk.set_draft(_open({}, 7), 7, Draft(Mode.DEBUG, "web/a", "s1", "Essaie."))
        ticket = tk.find(tk.set_draft(store, 7, None), 7)
        self.assertIsNone(ticket.draft)
        self.assertEqual(ticket.mode, Mode.DEBUG)
        self.assertEqual(ticket.step_id, "s1")


class AnswerCloseTest(unittest.TestCase):
    def test_answer_twice_raises(self):
        store = tk.answer(_open({}, 7), 7, "Réponse.")
        ticket = tk.find(store, 7)
        self.assertEqual(ticket.status, TicketStatus.ANSWERED)
        self.assertEqual(ticket.reply, "Réponse.")
        with self.assertRaises(ValueError):
            tk.answer(store, 7, "encore")

    def test_close_after_answer_raises(self):
        store = tk.answer(_open({}, 7), 7, "Réponse.")
        with self.assertRaises(ValueError):
            tk.close(store, 7)

    def test_close_then_answer_raises(self):
        store = tk.close(_open({}, 7), 7)
        self.assertEqual(tk.find(store, 7).status, TicketStatus.CLOSED)
        with self.assertRaises(ValueError):
            tk.answer(store, 7, "trop tard")


class FindByMessageTest(unittest.TestCase):
    def test_finds_the_ticket_of_a_channel_message_whatever_its_status(self):
        tickets = tk.close(_open({}, dm_id=900), 900)
        self.assertEqual(tk.find_by_message(tickets, 10, 900).dm_id, 900)
        self.assertIsNone(tk.find_by_message(tickets, 10, 901))
        self.assertIsNone(tk.find_by_message(tickets, 11, 900))


class HistoryTest(unittest.TestCase):
    def test_history_keeps_three_most_recent_of_same_author_and_challenge(self):
        store = {}
        for i in range(5):
            store = _open(store, i, question=f"q{i}", now=f"2026-10-22T10:0{i}:00+00:00")
        store = _open(store, 50, author=2, question="autre auteur")
        store = _open(store, 51, challenge="web/b", question="autre challenge")
        self.assertEqual(tk.history(store, 1, "web/a"), ("q2", "q3", "q4"))

    def test_history_without_challenge_is_empty(self):
        store = _open({}, 1)
        self.assertEqual(tk.history(store, 1, None), ())


class PurgeTest(unittest.TestCase):
    def test_purge_removes_old_finished_keeps_waiting_and_recent(self):
        store = _open({}, 1, now="2026-01-01T00:00:00+00:00")
        store = _open(store, 2, now="2026-01-01T00:00:00+00:00")
        store = _open(store, 3, now="2026-10-01T00:00:00+00:00")
        store = tk.answer(store, 1, "vieux")
        store = tk.close(store, 3)
        purged = tk.purge(store, "2026-06-01T00:00:00+00:00")
        # 1 : terminé et ancien, retiré. 2 : en attente, gardé. 3 : terminé mais récent, gardé.
        self.assertEqual(set(purged), {2, 3})


class ReactionsTest(unittest.TestCase):
    def test_reactions_for_each_status(self):
        self.assertEqual(tk.reactions_for(TicketStatus.WAITING), ((SEEN, WAITING), ()))
        self.assertEqual(tk.reactions_for(TicketStatus.ANSWERED), ((), (WAITING,)))
        self.assertEqual(tk.reactions_for(TicketStatus.CLOSED), ((), (WAITING,)))


if __name__ == "__main__":
    unittest.main()
