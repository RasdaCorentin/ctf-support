"""Contrôle de fuite : repère les flags réels du dépôt CTF recopiés dans les guides ou la FAQ.

Outil hors ligne. Le processus du bot ne lit jamais de flag : seul cet outil ouvre le dépôt CTF.
La sortie n'affiche jamais un secret, seulement l'emplacement et la clé du challenge.

Usage :
    venv/bin/python -m tools.check_leaks [--ctf-repo CHEMIN] [chemins...]
"""

import argparse
import os
import re
import sys
import unicodedata
from pathlib import Path

import yaml

from support.models import FLAG_RE

HERE = Path(__file__).resolve().parent.parent
DEFAULT_PATHS = ("guides", "faq.yaml")
MIN_INNER = 8  # en dessous, la partie intérieure produit trop de faux positifs
FLAG_WRAPPER = re.compile(r"^\w+\{(.*)\}$", re.DOTALL)


class _Loader(yaml.SafeLoader):
    pass


# Certains challenge.yaml utilisent des tags personnalisés (!filecontents) : on les ignore
_Loader.add_multi_constructor("!", lambda loader, suffix, node: None)


def _norm(text: str) -> str:
    """Minuscules, sans espaces ni caractères de largeur nulle (catégorie Cf, comme U+200B)."""
    return "".join(c for c in text.casefold() if not c.isspace() and unicodedata.category(c) != "Cf")


def _expand(flag: str) -> set[str]:
    """Le flag complet, et sa partie intérieure si elle est assez longue pour être un secret."""
    flag = flag.strip()
    secrets = {flag} if flag else set()
    match = FLAG_WRAPPER.match(flag)
    if match and len(_norm(match.group(1))) >= MIN_INNER:
        secrets.add(match.group(1).strip())
    return secrets


def _read(path: Path, errors: list[str]) -> str | None:
    """Contenu du fichier, ou None s'il n'existe pas. Un fichier présent mais illisible est une erreur :
    un flag qu'on ne peut pas lire est un flag qu'on ne contrôle pas."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return None
    except OSError as exc:
        errors.append(f"{path}: {exc.strerror}")
        return None


def _yaml_flags(challenge_dir: Path, errors: list[str]) -> list[str]:
    for name in ("challenge.yaml", "challenge.yml"):
        text = _read(challenge_dir / name, errors)
        if text is None:
            continue
        try:
            data = yaml.load(text, Loader=_Loader) or {}
        except yaml.YAMLError:
            # Le contenu n'est pas recopié : seul le chemin est signalé
            errors.append(f"{challenge_dir / name}: YAML illisible, flags non contrôlés")
            return []
        flags = data.get("flags") if isinstance(data, dict) else None
        if isinstance(flags, str):
            return [flags]
        if isinstance(flags, list):
            return [item for item in flags if isinstance(item, str)]
        if isinstance(flags, dict):
            return [value for value in flags.values() if isinstance(value, str)]
        return []
    return []


def collect_secrets(ctf_repo: Path, errors: list[str] | None = None) -> dict[str, str]:
    """Secret → clé du challenge. Sources : flag.txt, champ flags de challenge.yaml, motif FLAG_RE dans README.md.

    Les sources présentes mais illisibles sont ajoutées à `errors`.
    """
    errors = [] if errors is None else errors
    secrets: dict[str, str] = {}
    for challenge_dir in sorted(ctf_repo.glob("*/*")):
        if not challenge_dir.is_dir():
            continue
        key = f"{challenge_dir.parent.name}/{challenge_dir.name}"
        found: set[str] = set()
        flag_text = _read(challenge_dir / "flag.txt", errors)
        if flag_text:
            found |= _expand(flag_text)
        for flag in _yaml_flags(challenge_dir, errors):
            found |= _expand(flag)
        readme = _read(challenge_dir / "README.md", errors) or ""
        for match in FLAG_RE.finditer(readme):
            found |= _expand(match.group(0))
        for secret in found:
            if _norm(secret):
                secrets.setdefault(secret, key)
    return secrets


def _files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files += sorted(p for p in path.rglob("*") if p.is_file())
        elif path.is_file():
            files.append(path)
    return files


def scan(secrets: dict[str, str], paths: list[Path], errors: list[str] | None = None) -> list[tuple[Path, int, str]]:
    """(fichier, ligne, clé du challenge) pour chaque secret trouvé. Comparaison insensible à la casse.

    Les fichiers illisibles sont ajoutés à `errors`.
    """
    errors = [] if errors is None else errors
    needles = {(_norm(secret), key) for secret, key in secrets.items()}
    hits: set[tuple[Path, int, str]] = set()
    for file in _files(paths):
        text = _read(file, errors)
        if text is None:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            norm_line = _norm(line)
            for needle, key in needles:
                if needle in norm_line:
                    hits.add((file, number, key))
    return sorted(hits)


def _display(path: Path) -> str:
    try:
        return str(path.relative_to(HERE))
    except ValueError:
        return str(path)


def main(argv=None) -> int:
    """0 : aucune fuite ; 1 : fuite ; 2 : contrôle impossible (dépôt introuvable, fichier illisible)."""
    parser = argparse.ArgumentParser(description="Contrôle de fuite des flags réels dans les guides et la FAQ.")
    parser.add_argument("--ctf-repo", default=os.environ.get("CTF_REPO", str(HERE.parent / "ctf-cyber-tour")))
    parser.add_argument("paths", nargs="*", type=Path, help="fichiers ou dossiers à analyser (défaut : guides/ et faq.yaml)")
    args = parser.parse_args(argv)

    ctf_repo = Path(args.ctf_repo)
    if not ctf_repo.is_dir():
        print(f"[ERREUR] dépôt CTF introuvable : {ctf_repo}", file=sys.stderr)
        return 2

    paths = args.paths or [HERE / name for name in DEFAULT_PATHS]
    errors: list[str] = []
    secrets = collect_secrets(ctf_repo, errors)
    leaks = scan(secrets, paths, errors)
    for path, number, key in leaks:
        print(f"{_display(path)}:{number} : flag du challenge {key}")
    for error in errors:
        print(f"[ERREUR] {error}", file=sys.stderr)
    if leaks:
        print(f"{len(leaks)} fuite(s) détectée(s)")
        return 1
    if errors:
        # Échec fermé : une source non lue ne permet pas d'affirmer l'absence de fuite
        print(f"contrôle incomplet : {len(errors)} fichier(s) illisible(s)")
        return 2
    print(f"aucune fuite ({len(secrets)} secrets comparés)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
