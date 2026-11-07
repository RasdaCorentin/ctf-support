import unittest

from support.models import Mode, Ticket, TicketStatus


def ticket(**overrides):
    fields = dict(
        dm_id=1, channel_id=2, message_id=3, author_id=4, question="aide sqli",
        challenge_key="web/09-sqli", mode=Mode.DEBUG, step_id="reperer", draft="Regarde l'apostrophe.",
        reply=None, status=TicketStatus.WAITING, created_at="2026-10-22T10:00:00+00:00",
    )
    return Ticket(**{**fields, **overrides})


class TicketSerializationTest(unittest.TestCase):
    def test_round_trip_with_mode(self):
        original = ticket()
        data = original.to_dict()
        self.assertEqual(data["mode"], "debug")
        self.assertEqual(data["status"], "waiting")
        self.assertEqual(Ticket.from_dict(data), original)

    def test_round_trip_without_mode(self):
        original = ticket(mode=None, step_id=None, draft=None, status=TicketStatus.CLOSED)
        self.assertEqual(Ticket.from_dict(original.to_dict()), original)


if __name__ == "__main__":
    unittest.main()
