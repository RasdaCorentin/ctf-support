"""Contrats partagés par tous les modules : guide de challenge, brouillon, ticket, verdict de la garde.

Couche 0 : n'importe rien du projet. Ce fichier est figé pendant les lots parallèles (plan/README.md §4).
"""

import re
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Protocol

MAX_REPLY = 180  # longueur maximale de tout message public
GUIDE_MAX_STEPS = 6
GUIDE_FIELD_MAX = 200  # au-delà, un champ de guide devient une solution rédigée
FLAG_RE = re.compile(r"\b(ctf|bsides)\{[^}\n]*\}", re.IGNORECASE)
SEEN = "👀"
WAITING = "🕐"


class Mode(str, Enum):
    HINT = "indice"  # le joueur ne sait pas où aller
    DEBUG = "debug"  # le joueur a une piste qui échoue
    BUG = "bug"  # problème d'infrastructure
    LOGISTICS = "logistique"  # horaires, format, connexion
    OFF_TOPIC = "hors_sujet"


@dataclass(frozen=True)
class Step:
    id: str
    goal: str  # ce que le joueur doit accomplir à cette étape
    signs: tuple[str, ...]  # ce que dit ou montre un joueur arrivé là
    nudge: str  # coup de pouce pour franchir l'étape, sans la résoudre
    pitfalls: tuple[str, ...]  # "symptôme → cause", pour le mode debug


@dataclass(frozen=True)
class Guide:
    key: str  # "<categorie>/<dossier>", unique
    slug: str
    title: str
    category: str
    description: str  # énoncé public
    steps: tuple[Step, ...]
    forbidden: tuple[str, ...]  # ce qu'il ne faut jamais dire, décrit sans la valeur
    reviewed: bool


@dataclass(frozen=True)
class FaqEntry:
    keywords: tuple[str, ...]
    answer: str


@dataclass(frozen=True)
class DraftContext:
    question: str
    guide: Guide | None
    titles: tuple[str, ...]  # titres connus, quand aucun challenge n'est identifié
    faq: tuple[FaqEntry, ...]
    history: tuple[str, ...]  # questions précédentes du joueur sur ce challenge, la plus récente en dernier


@dataclass(frozen=True)
class Draft:
    mode: Mode
    challenge_key: str | None
    step_id: str | None  # étape où le rédacteur situe le joueur
    reply: str
    model: str | None = None  # modèle qui a rédigé, affiché dans le DM


class Drafter(Protocol):
    async def draft(self, context: DraftContext) -> Draft | None:
        """None : pas de brouillon (aucun modèle n'a répondu, refus, réponse illisible)."""
        ...


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reasons: tuple[str, ...] = ()  # codes stables : "empty", "too_long", "flag", "mass_mention", "invite"


class TicketStatus(str, Enum):
    WAITING = "waiting"  # 👀🕐 posés, DM envoyé
    ANSWERED = "answered"  # réponse publiée, 🕐 retiré
    CLOSED = "closed"  # clos sans réponse publique, 🕐 retiré


@dataclass(frozen=True)
class Ticket:
    dm_id: int
    channel_id: int
    message_id: int
    author_id: int
    question: str
    challenge_key: str | None
    mode: Mode | None
    step_id: str | None
    draft: str | None
    reply: str | None  # texte effectivement publié
    status: TicketStatus
    created_at: str  # ISO 8601 UTC

    def to_dict(self) -> dict:
        data = asdict(self)
        data["mode"] = self.mode.value if self.mode else None
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Ticket":
        mode = data.get("mode")
        return cls(**{
            **data,
            "mode": Mode(mode) if mode else None,
            "status": TicketStatus(data["status"]),
        })
