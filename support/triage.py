"""Repérage déterministe du challenge cité dans un ticket, sans Discord ni I/O.

Le tri ne décide plus de la réponse : tout message est rédigé puis relu par l'orga. Il sert seulement à
choisir le guide à donner au rédacteur.
"""

import re
import unicodedata

from support.models import Guide


def normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def has_any(norm: str, words) -> bool:
    # Lookarounds plutôt que \b : les titres peuvent se terminer par un caractère non alphanumérique
    return any(re.search(rf"(?<!\w){re.escape(w)}(?!\w)", norm) for w in words)


def loosen(text: str) -> str:
    """Forme normalisée où tirets, soulignés et espaces se valent : « baby cookie » désigne « baby-cookie »."""
    return re.sub(r"[-_\s]+", " ", normalize(text)).strip()


def match_guides(norm: str, guides) -> list[Guide]:
    """Guides dont le titre ou le slug apparaît dans le texte déjà passé par loosen()."""
    return [g for g in guides if has_any(norm, (loosen(g.title), loosen(g.slug)))]


_NUMERIC_PREFIX = re.compile(r"^\d+-")
_ALIAS_MIN = 4  # un suffixe plus court (« xss ») est trop générique pour identifier un challenge


def _aliases(guides: list[Guide]) -> dict[str, str]:
    """Suffixe sans préfixe numérique (« sqli » pour « 09-sqli ») → clé du guide.

    Un suffixe partagé par plusieurs guides, ou égal au slug d'un autre, n'est pas un alias : il ne
    désignerait pas un seul challenge.
    """
    stripped = {g.key: _NUMERIC_PREFIX.sub("", g.slug, count=1) for g in guides}
    aliases = {}
    for g in guides:
        suffix = loosen(stripped[g.key])
        if suffix == loosen(g.slug) or len(suffix) < _ALIAS_MIN:
            continue
        owners = [h for h in guides if loosen(h.slug) == suffix or loosen(stripped[h.key]) == suffix]
        if len(owners) == 1:
            aliases[suffix] = g.key
    return aliases


def identify(text: str, guides: dict[str, Guide]) -> str | None:
    """Clé du seul challenge cité dans le texte, ou None (aucun, ou plusieurs)."""
    norm = loosen(text)
    keys = {g.key for g in match_guides(norm, guides.values())}
    keys |= {key for alias, key in _aliases(list(guides.values())).items() if has_any(norm, (alias,))}
    return keys.pop() if len(keys) == 1 else None
