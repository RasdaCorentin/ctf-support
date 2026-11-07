import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from support.storage import load_guides
from tools.make_guides import make_guides

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "ctf-mini"
SECRETS = ("FIXTURE_SECRET", "FIXTURE_SOLUTION_MARKER")


def _read_tree(root: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(root.rglob("*")) if p.is_file())


class MakeGuidesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.guides = Path(self._tmp.name) / "guides"

    def test_cree_le_guide_avec_les_champs_publics(self):
        created, errors = make_guides(FIXTURE, self.guides)

        self.assertIn("web/01-ok", created)
        data = yaml.safe_load((self.guides / "web" / "01-ok.yaml").read_text(encoding="utf-8"))
        self.assertEqual(data["title"], "Cookie maison")
        self.assertEqual(data["category"], "web")
        self.assertIn("panneau d'administration", data["description"])
        self.assertEqual(data["steps"], [])
        self.assertFalse(data["reviewed"])
        self.assertIn("web/02-leaky : description contient un format de flag, description laissée vide", errors)

    def test_aucun_secret_dans_les_fichiers_crees(self):
        make_guides(FIXTURE, self.guides)

        content = _read_tree(self.guides)
        for secret in SECRETS:
            self.assertNotIn(secret, content)

    def test_ne_lit_que_les_challenge_yaml(self):
        reads = []
        original_read_text = Path.read_text
        original_open = Path.open

        def spy_read_text(self, *args, **kwargs):
            reads.append(self)
            return original_read_text(self, *args, **kwargs)

        def spy_open(self, mode="r", *args, **kwargs):
            if "r" in mode and "w" not in mode:
                reads.append(self)
            return original_open(self, mode, *args, **kwargs)

        with patch.object(Path, "read_text", spy_read_text), patch.object(Path, "open", spy_open):
            make_guides(FIXTURE, self.guides)

        self.assertTrue(reads)
        for path in reads:
            self.assertTrue(path.name.startswith("challenge.y"), f"fichier lu hors liste blanche : {path.name}")

    def test_description_avec_flag_videe_et_erreur_sans_le_flag(self):
        created, errors = make_guides(FIXTURE, self.guides)

        self.assertIn("web/02-leaky", created)
        data = yaml.safe_load((self.guides / "web" / "02-leaky.yaml").read_text(encoding="utf-8"))
        self.assertEqual(data["description"], "")
        self.assertTrue(any(e.startswith("web/02-leaky :") for e in errors))
        self.assertNotIn("FIXTURE_SECRET", " ".join(errors))

    def test_tag_personnalise_accepte_et_old_ignore_et_yaml_invalide_signale(self):
        created, errors = make_guides(FIXTURE, self.guides)

        self.assertIn("web/03-custom-tag", created)
        self.assertFalse((self.guides / "web" / "old_04-legacy.yaml").exists())
        self.assertNotIn("web/old_04-legacy", created)
        self.assertNotIn("crypto/01-broken", created)
        self.assertTrue(any(e.startswith("crypto/01-broken :") for e in errors))

    def test_guide_existant_non_ecrase(self):
        make_guides(FIXTURE, self.guides)
        target = self.guides / "web" / "01-ok.yaml"
        target.write_text("title: \"modifié à la main\"\n", encoding="utf-8")
        before = target.read_text(encoding="utf-8")

        created, _ = make_guides(FIXTURE, self.guides)

        self.assertNotIn("web/01-ok", created)
        self.assertEqual(target.read_text(encoding="utf-8"), before)

    def test_squelette_se_charge_sans_erreur(self):
        make_guides(FIXTURE, self.guides)

        guides, errors = load_guides(self.guides)

        self.assertIn("web/01-ok", guides)
        self.assertEqual(guides["web/01-ok"].title, "Cookie maison")
        self.assertEqual(guides["web/01-ok"].category, "web")
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
