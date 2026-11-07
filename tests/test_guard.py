"""Tests hors ligne de la garde de sortie."""

import unittest

from support.guard import check_reply
from support.models import MAX_REPLY


class CheckReplyTests(unittest.TestCase):
    def test_longueur_limite_incluse(self):
        self.assertTrue(check_reply("a" * MAX_REPLY).ok)
        self.assertEqual(check_reply("a" * (MAX_REPLY + 1)).reasons, ("too_long",))

    def test_longueur_parametrable(self):
        self.assertEqual(check_reply("abcd", max_len=3).reasons, ("too_long",))
        self.assertTrue(check_reply("abc", max_len=3).ok)

    def test_vide_ou_espaces(self):
        self.assertEqual(check_reply("").reasons, ("empty",))
        self.assertEqual(check_reply("  \n\t ").reasons, ("empty",))

    def test_flag_directement(self):
        for text in ["bsides{abc}", "CTF{x}", "ctf{x}", "le format est bsides{...}"]:
            with self.subTest(text=text):
                self.assertEqual(check_reply(text).reasons, ("flag",))

    def test_flag_deguise(self):
        for text in [
            "b s i d e s { x }",
            "bsides​{x}",
            "ｂｓｉｄｅｓ｛x｝",
            "`bsides`{x}",
            "*b*s*i*d*e*s{x}",
            "voici b s i d e s { x }",
            "le flag : c t f {x}",
        ]:
            with self.subTest(text=text):
                self.assertEqual(check_reply(text).reasons, ("flag",))

    def test_mentions_de_masse(self):
        for text in ["@everyone", "@here", "<@&42>"]:
            with self.subTest(text=text):
                self.assertEqual(check_reply(text).reasons, ("mass_mention",))

    def test_mention_utilisateur_autorisee(self):
        self.assertTrue(check_reply("<@42> regarde le guide").ok)

    def test_invitation(self):
        self.assertEqual(check_reply("https://discord.gg/xyz").reasons, ("invite",))
        self.assertEqual(check_reply("https://discord.com/invite/xyz").reasons, ("invite",))

    def test_plusieurs_defauts_dans_l_ordre(self):
        text = "@everyone bsides{x} https://discord.gg/abc"
        self.assertEqual(check_reply(text).reasons, ("flag", "mass_mention", "invite"))
        too_long = "@here " + "a" * MAX_REPLY
        self.assertEqual(check_reply(too_long).reasons, ("too_long", "mass_mention"))

    def test_texte_normal_accents_et_emojis(self):
        verdict = check_reply("Regarde l'étape 2, le cookie est côté client 👀")
        self.assertTrue(verdict.ok)
        self.assertEqual(verdict.reasons, ())


if __name__ == "__main__":
    unittest.main()
