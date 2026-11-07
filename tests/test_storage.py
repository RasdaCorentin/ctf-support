import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import storage
from support.models import Mode, Ticket, TicketStatus

GUIDE = """\
title: "Injection Classique"
description: "Connectez-vous en tant qu'administrateur."
reviewed: false
forbidden:
  - "la requête de contournement complète"
steps:
  - id: reperer
    goal: "Comprendre que l'entrée arrive telle quelle dans une requête SQL."
    signs: ["parle d'erreur SQL"]
    nudge: "Que se passe-t-il avec une apostrophe ?"
    pitfalls: ["aucune erreur visible → compare les comportements"]
"""


def step(i: int, goal: str = "Objectif.") -> str:
    return f'  - id: s{i}\n    goal: "{goal}"\n    nudge: "Piste."\n'


class LoadGuidesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel: str, content: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_valid_guide(self):
        self.write("web/09-sqli.yaml", GUIDE)
        guides, errors = storage.load_guides(self.root)
        self.assertEqual(errors, [])
        guide = guides["web/09-sqli"]
        self.assertEqual((guide.category, guide.slug, guide.title), ("web", "09-sqli", "Injection Classique"))
        self.assertEqual(guide.steps[0].id, "reperer")
        self.assertEqual(guide.steps[0].signs, ("parle d'erreur SQL",))
        self.assertFalse(guide.reviewed)

    def test_flag_disables_guide_and_is_not_echoed(self):
        self.write("web/09-sqli.yaml", GUIDE.replace("Que se passe-t-il", "bsides{s3cr3t} puis"))
        guides, errors = storage.load_guides(self.root)
        self.assertEqual(guides, {})
        self.assertIn("web/09-sqli", errors[0])
        self.assertNotIn("s3cr3t", errors[0])

    def test_too_many_steps(self):
        self.write("web/x.yaml", "title: X\nsteps:\n" + "".join(step(i) for i in range(7)))
        guides, errors = storage.load_guides(self.root)
        self.assertEqual(guides, {})
        self.assertIn("7 étapes", errors[0])

    def test_field_too_long(self):
        self.write("web/x.yaml", "title: X\nsteps:\n" + step(0, "a" * 201))
        guides, errors = storage.load_guides(self.root)
        self.assertEqual(guides, {})
        self.assertIn("201", errors[0])

    def test_long_public_description_is_allowed(self):
        self.write("web/x.yaml", f'title: X\ndescription: "{"d" * 500}"\nsteps:\n' + step(0))
        guides, errors = storage.load_guides(self.root)
        self.assertEqual(errors, [])
        self.assertIn("web/x", guides)

    def test_invalid_yaml_is_reported_not_fatal(self):
        self.write("web/broken.yaml", "steps: [\n")
        guides, errors = storage.load_guides(self.root)
        self.assertEqual(guides, {})
        self.assertEqual(len(errors), 1)


class StateDirTest(unittest.TestCase):
    def test_state_dir_from_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"SUPPORT_STATE_DIR": tmp}):
                module = importlib.reload(storage)
                self.assertEqual(module.STATE_DIR, Path(tmp))
                ticket = Ticket(1, 2, 3, 4, "q", None, Mode.HINT, None, None, None,
                                TicketStatus.WAITING, "2026-10-22T10:00:00+00:00")
                module.save_tickets({1: ticket})
                self.assertEqual(module.load_tickets(), {1: ticket})
                self.assertTrue((Path(tmp) / "tickets.json").exists())
        importlib.reload(storage)


if __name__ == "__main__":
    unittest.main()
