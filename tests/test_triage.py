import unittest

from support import triage
from support.models import FLAG_RE, Guide


def guide(key: str, title: str) -> Guide:
    category, slug = key.split("/")
    return Guide(key=key, slug=slug, title=title, category=category, description="",
                 steps=(), forbidden=(), reviewed=False)


WAVE = guide("forensics/01-wave", "Onde Cachee")
LOGIN = guide("web/01-login", "Login Bypass")
GUIDES = [WAVE, LOGIN]


class NormalizeTest(unittest.TestCase):
    def test_accents_and_case(self):
        self.assertEqual(triage.normalize("Bloqué SUR l'Onde Cachée"), "bloque sur l'onde cachee")


class MatchGuidesTest(unittest.TestCase):
    def match(self, text):
        return triage.match_guides(triage.loosen(text), GUIDES)

    def test_title(self):
        self.assertEqual(self.match("bloqué sur l'Onde Cachée"), [WAVE])

    def test_slug(self):
        self.assertEqual(self.match("aide pour 01-login"), [LOGIN])

    def test_two_challenges(self):
        self.assertEqual(self.match("Onde Cachee ou Login Bypass ?"), [WAVE, LOGIN])

    def test_no_challenge(self):
        self.assertEqual(self.match("bonjour tout le monde"), [])


def by_key(*guides: Guide) -> dict[str, Guide]:
    return {g.key: g for g in guides}


SQLI = guide("web/09-sqli", "Injection Classique")
XSS = guide("web/03-xss", "Scripts Tiers")
SQLI_OTHER = guide("pwn/10-sqli", "Dépassement")
COOKIE = guide("web/baby-cookie", "Biscuit Magique")


class IdentifyTest(unittest.TestCase):
    def identify(self, text, *guides):
        return triage.identify(text, by_key(*guides))

    def test_title(self):
        self.assertEqual(self.identify("bloqué sur Injection Classique", SQLI, XSS), "web/09-sqli")

    def test_numbered_slug_suffix(self):
        self.assertEqual(self.identify("sqli marche pas", SQLI, XSS), "web/09-sqli")

    def test_slug_written_with_spaces_or_underscores(self):
        # Le joueur cite le dossier, pas le titre public, et rarement avec le tiret
        for text in ("Je suis bloque au début de baby cookie", "baby_cookie ?", "BABY-COOKIE"):
            self.assertEqual(self.identify(text, COOKIE, SQLI), "web/baby-cookie", text)

    def test_numbered_slug_written_with_spaces(self):
        self.assertEqual(self.identify("le 09 sqli est cassé", SQLI, XSS), "web/09-sqli")

    def test_two_challenges(self):
        self.assertIsNone(self.identify("Injection Classique ou Scripts Tiers ?", SQLI, XSS))

    def test_no_challenge(self):
        self.assertIsNone(self.identify("bonjour tout le monde", SQLI, XSS))

    def test_accents_and_case(self):
        self.assertEqual(self.identify("BLOQUÉ SUR l'Onde CACHÉE", WAVE), "forensics/01-wave")

    def test_ambiguous_suffix_is_not_an_alias(self):
        self.assertIsNone(self.identify("sqli marche pas", SQLI, SQLI_OTHER))

    def test_short_suffix_is_not_an_alias(self):
        self.assertIsNone(self.identify("xss marche pas", XSS, WAVE))


class FlagPatternTest(unittest.TestCase):
    def test_bsides_and_ctf_prefixes(self):
        self.assertIsNotNone(FLAG_RE.search("le flag est bsides{abc}"))
        self.assertIsNotNone(FLAG_RE.search("CTF{x}"))


if __name__ == "__main__":
    unittest.main()
