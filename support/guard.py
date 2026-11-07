"""Garde de sortie : contrôle de tout texte public avant publication.

Couche 1 : n'importe que `support.models` et la bibliothèque standard. Fonction pure, sans effet de bord.
Appelée par `bot.publish()` sur toute sortie : indice, FAQ, brouillon du rédacteur et texte de l'orga.
"""

import re
import unicodedata

from support.models import FLAG_RE, MAX_REPLY, Verdict

# Caractères invisibles et marqueurs de mise en forme Discord : ils coupent un mot sans rien changer à l'affichage,
# donc un flag écrit avec eux échapperait à FLAG_RE si on ne les retirait pas avant la recherche.
_INVISIBLE = frozenset("​‌‍⁠﻿")
_MARKUP_RE = re.compile(r"[`*_|~]")
_SPACE_RE = re.compile(r"\s+")
_MASS_MENTION_RE = re.compile(r"@everyone|@here|<@&\d+>", re.IGNORECASE)
_INVITE_RE = re.compile(r"discord\.gg/|discord\.com/invite/", re.IGNORECASE)
# FLAG_RE exige une frontière de mot avant le préfixe. Une fois les espaces retirés, le flag est collé au mot
# précédent (« voicibsides{x} ») : la forme normalisée est donc cherchée sans cette frontière.
_GLUED_FLAG_RE = re.compile(r"(ctf|bsides)\{[^}\n]*\}", re.IGNORECASE)


def _normalize(text: str) -> str:
    """Forme comparée pour détecter un flag déguisé : NFKC, sans espaces, sans invisibles ni marqueurs."""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(char for char in text if char not in _INVISIBLE)
    text = _MARKUP_RE.sub("", text)
    return _SPACE_RE.sub("", text)


def check_reply(text: str, max_len: int = MAX_REPLY) -> Verdict:
    """Liste toutes les raisons de refuser `text`, dans l'ordre du tableau de la fiche L11."""
    reasons = []
    if not text.strip():
        reasons.append("empty")
    if len(text) > max_len:
        reasons.append("too_long")
    if FLAG_RE.search(text) or _GLUED_FLAG_RE.search(_normalize(text)):
        reasons.append("flag")
    if _MASS_MENTION_RE.search(text):
        reasons.append("mass_mention")
    if _INVITE_RE.search(text):
        reasons.append("invite")
    return Verdict(ok=not reasons, reasons=tuple(reasons))
