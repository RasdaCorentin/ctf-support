"""Contrôle de fuite. Dépôts CTF temporaires uniquement : le vrai dépôt n'est jamais lu."""

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path

from tools import check_leaks

FLAG = "bsides{0123456789abcdef}"  # partie intérieure : 16 caractères hexadécimaux
KEY = "web/xss-1"


def make_ctf(root: Path, flag: str) -> Path:
    """Crée un dépôt CTF factice avec un challenge web/xss-1 portant ce flag."""
    ctf = root / "ctf"
    challenge = ctf / "web" / "xss-1"
    challenge.mkdir(parents=True)
    (challenge / "flag.txt").write_text(flag + "\n", encoding="utf-8")
    (challenge / "challenge.yaml").write_text(f'name: XSS 1\nflags:\n  - "{flag}"\n', encoding="utf-8")
    (challenge / "README.md").write_text(f"Énoncé. Le flag est {flag}\n", encoding="utf-8")
    return ctf


class CheckLeaksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.ctf = make_ctf(self.root, FLAG)

    def write_guide(self, name: str, content: str) -> Path:
        path = self.root / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_flag_complet_recopie_detecte_avec_fichier_et_ligne(self):
        guide = self.write_guide("guide.yaml", f"title: x\nsteps:\n  - nudge: \"{FLAG}\"\n")
        hits = check_leaks.scan(check_leaks.collect_secrets(self.ctf), [guide])
        self.assertEqual(hits, [(guide, 3, KEY)])

    def test_partie_interieure_seule_detectee(self):
        guide = self.write_guide("faq.yaml", "answer: la valeur est 0123456789abcdef\n")
        hits = check_leaks.scan(check_leaks.collect_secrets(self.ctf), [guide])
        self.assertEqual(hits, [(guide, 1, KEY)])

    def test_interieur_de_4_caracteres_ignore(self):
        ctf = make_ctf(Path(tempfile.mkdtemp(dir=self.root)), "ctf{abcd}")
        secrets = check_leaks.collect_secrets(ctf)
        self.assertNotIn("abcd", secrets)
        guide = self.write_guide("court.yaml", "answer: abcd\n")
        self.assertEqual(check_leaks.scan(secrets, [guide]), [])

    def test_interieur_espace_detecte(self):
        guide = self.write_guide("espace.yaml", "answer: 0123 4567 89ab cdef\n")
        hits = check_leaks.scan(check_leaks.collect_secrets(self.ctf), [guide])
        self.assertEqual(hits, [(guide, 1, KEY)])

    def test_sortie_de_main_ne_contient_jamais_le_secret(self):
        guide = self.write_guide("leak.yaml", f"ok\nnudge: {FLAG}\n")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = check_leaks.main(["--ctf-repo", str(self.ctf), str(guide)])
        self.assertEqual(code, 1)
        self.assertIn(f"{guide}:2 : flag du challenge {KEY}", out.getvalue())
        combined = out.getvalue() + err.getvalue()
        self.assertNotIn("0123456789abcdef", combined)
        self.assertNotIn(FLAG, combined)

    def test_depot_propre_renvoie_zero(self):
        guide = self.write_guide("propre.yaml", "answer: rien à signaler\n")
        with contextlib.redirect_stdout(io.StringIO()):
            code = check_leaks.main(["--ctf-repo", str(self.ctf), str(guide)])
        self.assertEqual(code, 0)

    def test_depot_absent_renvoie_deux(self):
        with contextlib.redirect_stderr(io.StringIO()):
            code = check_leaks.main(["--ctf-repo", str(self.root / "absent")])
        self.assertEqual(code, 2)

    def run_main(self, *paths) -> int:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return check_leaks.main(["--ctf-repo", str(self.ctf), *map(str, paths)])

    def test_challenge_yaml_illisible_renvoie_deux(self):
        (self.ctf / "web" / "xss-1" / "challenge.yaml").write_text("flags: [\n", encoding="utf-8")
        guide = self.write_guide("propre.yaml", "answer: rien\n")
        self.assertEqual(self.run_main(guide), 2)

    @unittest.skipIf(os.geteuid() == 0, "root lit un fichier même sans droit de lecture")
    def test_flag_txt_sans_droit_de_lecture_renvoie_deux(self):
        flag_txt = self.ctf / "web" / "xss-1" / "flag.txt"
        flag_txt.chmod(0)
        self.addCleanup(flag_txt.chmod, 0o600)
        guide = self.write_guide("propre.yaml", "answer: rien\n")
        self.assertEqual(self.run_main(guide), 2)


if __name__ == "__main__":
    unittest.main()
