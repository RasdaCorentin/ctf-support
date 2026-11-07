"""Crée un squelette de guide par challenge, à partir des seuls champs publics du dépôt CTF.

Liste blanche : seuls `name` et `description` de challenge.yaml sont extraits. Aucun autre fichier du
challenge n'est lu (flag, solution, liste des flags). Un guide existant n'est jamais écrasé.

Usage :
    venv/bin/python -m tools.make_guides --ctf-repo ../ctf-cyber-tour
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

from support.models import FLAG_RE
from support.storage import GUIDES_DIR

ALLOWED_FIELDS = ("name", "description")


class _Loader(yaml.SafeLoader):
    pass


# Certains challenge.yaml utilisent des tags personnalisés (!filecontents) : on les ignore
_Loader.add_multi_constructor("!", lambda loader, suffix, node: None)


def public_fields(challenge_dir: Path) -> tuple[dict | None, str | None]:
    """(champs publics, erreur). Ne lit que challenge.yaml ou challenge.yml.

    Une description qui contient un flag est vidée : le guide est tout de même créé, l'erreur est signalée.
    Sans nom, ou si le fichier est illisible, rien n'est créé (champs à None).
    """
    path = next(
        (challenge_dir / name for name in ("challenge.yaml", "challenge.yml") if (challenge_dir / name).exists()),
        None,
    )
    if path is None:
        return None, "pas de challenge.yaml"
    try:
        data = yaml.load(path.read_text(encoding="utf-8"), Loader=_Loader) or {}
    except (OSError, yaml.YAMLError) as exc:
        return None, f"challenge.yaml illisible ({type(exc).__name__})"
    if not isinstance(data, dict):
        return None, "challenge.yaml n'est pas un mapping"

    fields = {name: str(data.get(name, "") or "").strip() for name in ALLOWED_FIELDS}
    if not fields["name"]:
        return None, "pas de champ 'name'"
    if FLAG_RE.search(fields["name"]):
        return None, "le nom contient un format de flag, rien créé"
    if FLAG_RE.search(fields["description"]):
        fields["description"] = ""
        return fields, "description contient un format de flag, description laissée vide"
    return fields, None


def _skeleton(category: str, fields: dict) -> str:
    # json.dumps produit une chaîne YAML valide, quel que soit le contenu
    return (
        "# Rédiger les étapes, puis passer reviewed à true.\n"
        f"title: {json.dumps(fields['name'], ensure_ascii=False)}\n"
        f"category: {category}\n"
        f"description: {json.dumps(fields['description'], ensure_ascii=False)}\n"
        "reviewed: false\n"
        "forbidden: []\n"
        "steps: []\n"
    )


def make_guides(ctf_repo: Path, guides_dir: Path) -> tuple[list[str], list[str]]:
    """(clés créées, erreurs). N'écrase jamais un fichier existant."""
    created, errors = [], []
    challenge_dirs = sorted({p.parent for p in ctf_repo.glob("*/*/challenge.y*ml")})
    for challenge_dir in challenge_dirs:
        if challenge_dir.name.startswith("old_"):
            continue
        category = challenge_dir.parent.name
        key = f"{category}/{challenge_dir.name}"
        target = guides_dir / category / f"{challenge_dir.name}.yaml"
        if target.exists():
            continue

        fields, problem = public_fields(challenge_dir)
        if problem:
            errors.append(f"{key} : {problem}")
        if fields is None:
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_skeleton(category, fields), encoding="utf-8")
        created.append(key)
    return created, errors


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Crée les squelettes de guides depuis le dépôt CTF.")
    parser.add_argument("--ctf-repo", type=Path, required=True, help="racine du dépôt CTF")
    args = parser.parse_args(argv)

    if not args.ctf_repo.is_dir():
        print(f"[ERREUR] dépôt CTF introuvable : {args.ctf_repo}", file=sys.stderr)
        return 2

    created, errors = make_guides(args.ctf_repo, GUIDES_DIR)
    for error in errors:
        print(f"[ERREUR] {error}")
    print(f"{len(created)} créés, {len(errors)} en erreur")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
