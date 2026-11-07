"""Lecture des guides et de la FAQ, persistance locale de l'état du bot."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import yaml

from support.models import FLAG_RE, GUIDE_FIELD_MAX, GUIDE_MAX_STEPS, FaqEntry, Guide, Step, Ticket

HERE = Path(__file__).resolve().parent.parent
GUIDES_DIR = HERE / "guides"
FAQ_PATH = HERE / "faq.yaml"
# Surchargeable pour qu'un worktree ou un test n'écrive jamais dans l'état du bot en service
STATE_DIR = Path(os.environ.get("SUPPORT_STATE_DIR", HERE / "state"))


def _texts(raw) -> tuple[str, ...]:
    return tuple(str(item).strip() for item in raw or [])


def _parse_guide(key: str, data: dict) -> Guide:
    steps = tuple(
        Step(
            id=str(raw["id"]).strip(),
            goal=str(raw.get("goal", "")).strip(),
            signs=_texts(raw.get("signs")),
            nudge=str(raw.get("nudge", "")).strip(),
            pitfalls=_texts(raw.get("pitfalls")),
        )
        for raw in data.get("steps") or []
    )
    category, slug = key.split("/", 1)
    return Guide(
        key=key,
        slug=slug,
        title=str(data.get("title", slug)).strip(),
        category=category,
        description=str(data.get("description", "")).strip(),
        steps=steps,
        forbidden=_texts(data.get("forbidden")),
        reviewed=bool(data.get("reviewed", False)),
    )


def _written_fields(guide: Guide) -> list[str]:
    """Champs rédigés pour le guide. L'énoncé, repris du dépôt CTF, n'en fait pas partie."""
    fields = [*guide.forbidden]
    for step in guide.steps:
        fields += [step.id, step.goal, step.nudge, *step.signs, *step.pitfalls]
    return fields


def guide_problem(guide: Guide) -> str | None:
    """Raison d'écarter un guide, ou None. Le message ne recopie jamais le contenu fautif."""
    written = _written_fields(guide)
    if any(FLAG_RE.search(field) for field in [guide.title, guide.description, *written]):
        return "contient un format de flag"
    if len(guide.steps) > GUIDE_MAX_STEPS:
        return f"{len(guide.steps)} étapes, maximum {GUIDE_MAX_STEPS}"
    longest = max((len(field) for field in written), default=0)
    if longest > GUIDE_FIELD_MAX:
        return f"un champ fait {longest} caractères, maximum {GUIDE_FIELD_MAX}"
    return None


def load_guides(guides_dir: Path = GUIDES_DIR) -> tuple[dict[str, Guide], list[str]]:
    """Charge guides/<categorie>/<dossier>.yaml. Un guide invalide ou dangereux est ignoré et signalé."""
    guides, errors = {}, []
    for path in sorted(guides_dir.glob("*/*.yaml")):
        key = f"{path.parent.name}/{path.stem}"
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            guide = _parse_guide(key, data)
        except (OSError, yaml.YAMLError, KeyError, TypeError, AttributeError, ValueError) as exc:
            errors.append(f"{key} : fichier invalide ({type(exc).__name__})")
            continue
        # Fail closed : un guide douteux n'atteint jamais le rédacteur
        if problem := guide_problem(guide):
            errors.append(f"{key} : {problem}, guide ignoré")
            continue
        guides[key] = guide
    return guides, errors


def load_faq(path: Path = FAQ_PATH) -> list[FaqEntry]:
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return [
        FaqEntry(keywords=tuple(entry.get("keywords", [])), answer=str(entry.get("answer", "")).strip())
        for entry in data
    ]


def load_tickets() -> dict[int, Ticket]:
    """Tickets transmis à l'orga, indexés par identifiant du DM."""
    try:
        raw = json.loads((STATE_DIR / "tickets.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return {int(dm_id): Ticket.from_dict(data) for dm_id, data in raw.items()}


def save_tickets(tickets: dict[int, Ticket]) -> None:
    _write_state_json("tickets.json", {str(dm_id): t.to_dict() for dm_id, t in tickets.items()})


def _write_state_json(name: str, data: dict) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    tmp = STATE_DIR / f"{name}.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_DIR / name)


def log_event(kind: str, user_id: int, text: str, reply: str) -> None:
    """Journal JSONL de tous les tickets et relais."""
    STATE_DIR.mkdir(exist_ok=True)
    event = {
        "at": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "user_id": user_id,
        "text": text[:500],
        "reply": reply,
    }
    with (STATE_DIR / "events.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
