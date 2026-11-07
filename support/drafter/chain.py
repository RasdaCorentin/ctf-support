"""Chaîne de modèles Gemini du rédacteur : un appel essaie les modèles du meilleur au moins bon.

Reprise de jobradar/llm.py, adaptée à un usage interactif : le joueur attend, donc un échec passe au modèle
suivant tout de suite, sans attente ni nouvel essai du même modèle. Politique par échec :

    429 quota du jour (quotaId ...PerDay...)   modèle écarté jusqu'à la remise à zéro des quotas
    404 (modèle retiré)                        idem
    autre 429, 5xx, délai, erreur de connexion, autre 4xx, réponse malformée, refus de sécurité
                                               modèle suivant, tout de suite

Les quotas par minute et par jour (RPM, RPD) sont suivis localement : un modèle à sa limite est sauté sans être
appelé, parce qu'un appel refusé compte quand même dans le quota.

Un modèle qui ne répond pas dans le délai de lecture (SUPPORT_LLM_TIMEOUT, 8 s par défaut) cède la place au
suivant. Le délai de connexion est fixe, à 1 s.
"""

import json
import logging
import os
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

log = logging.getLogger("support")

DEFAULT_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
# Ordre du meilleur au moins bon (hypothèse, à confirmer en L42). Identifiants vérifiés le 2026-10-22.
DEFAULT_MODELS = (
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3-flash-preview",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
)
CONNECT_TIMEOUT = 1.0
READ_TIMEOUT = 8.0
# Google nomme le quota journalier « ...PerDay... » (quotaId dans le corps du 429). Cherché aussi dans le
# texte brut, pour qu'un corps illisible compte quand même.
DAILY_QUOTA_MARKER = "PerDay"
# AI Studio remet ses quotas à zéro à minuit, heure du Pacifique
QUOTA_TZ = ZoneInfo("America/Los_Angeles")
JSON_MODE = {"type": "json_object"}
MINUTE = timedelta(seconds=60)
# (RPM, RPD) du quota gratuit AI Studio, relevé le 2026-10-22. Un modèle absent de ce dictionnaire n'a pas de
# limite locale : seul le 429 le retient. Le TPM n'est pas suivi (un texte de ticket reste très en dessous).
DEFAULT_LIMITS: dict[str, tuple[int, int]] = {
    "gemini-3.8-flash": (5, 20),
    "gemini-3.6-flash": (5, 20),
    "gemini-3-flash-preview": (5, 20),
    "gemini-3.5-flash-lite": (15, 500),
    "gemini-3.1-flash-lite": (15, 500),
}


class LLMError(Exception):
    """Ce modèle n'a pas donné de réponse utilisable. ModelChain passe au suivant."""


class ModelUnavailable(LLMError):
    """Le modèle ne répondra plus avant la remise à zéro des quotas (quota du jour, 404)."""


@dataclass
class QuotaError:
    """Ce que dit un corps de 429 : les quotas atteints, et s'il s'agit du quota du jour."""

    quota_ids: list[str] = field(default_factory=list)
    daily: bool = False


def parse_quota_error(text: str | None) -> QuotaError:
    """Lit un corps de 429 Gemini, en tolérant l'enveloppe en liste et les champs absents."""
    info = QuotaError()
    try:
        body = json.loads(text or "")
    except ValueError:
        body = None
    if isinstance(body, list):
        body = body[0] if body else None
    error = body.get("error") if isinstance(body, dict) else None
    details = error.get("details") if isinstance(error, dict) else None
    for detail in details if isinstance(details, list) else []:
        if not isinstance(detail, dict):
            continue
        violations = detail.get("violations")
        for item in [detail, *(violations if isinstance(violations, list) else [])]:
            if isinstance(item, dict) and isinstance(item.get("quotaId"), str):
                info.quota_ids.append(item["quotaId"])
    info.daily = (any(DAILY_QUOTA_MARKER in q for q in info.quota_ids)
                  or DAILY_QUOTA_MARKER in (text or ""))
    return info


def _excerpt(text: str | None, limit: int = 200) -> str:
    """L'explication de l'API (modèle retiré, quota...), espaces réduits."""
    return " ".join((text or "").split())[:limit]


def post_chat(session, url: str, payload: dict, api_key: str | None, *, model: str,
              timeout: tuple[float, float]) -> requests.Response:
    """Un seul essai : renvoie la réponse HTTP 200, sinon lève ModelUnavailable ou LLMError."""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        res = session.post(url, json=payload, headers=headers, timeout=timeout)
    except requests.ReadTimeout as e:
        raise LLMError(f"pas de réponse dans le délai de {timeout[1]:g} s") from e
    except requests.RequestException as e:
        raise LLMError(f"erreur de connexion ({type(e).__name__})") from e
    if res.status_code == 200:
        return res
    if res.status_code == 429:
        quota = parse_quota_error(res.text)
        if quota.daily:
            raise ModelUnavailable("quota du jour atteint")
        raise LLMError(f"HTTP 429 ({', '.join(quota.quota_ids) or 'quota par minute'})")
    if res.status_code == 404:
        # Modèle retiré ou mal orthographié : inutile de le rappeler avant la remise à zéro
        raise ModelUnavailable("HTTP 404, modèle introuvable")
    raise LLMError(f"HTTP {res.status_code} {_excerpt(res.text)}".rstrip())


def next_quota_reset(now: datetime) -> datetime:
    """Minuit, heure du Pacifique, qui suit `now` (en UTC) : remise à zéro des quotas."""
    local = now.astimezone(QUOTA_TZ)
    midnight = datetime.combine(local.date() + timedelta(days=1), time(0), tzinfo=QUOTA_TZ)
    return midnight.astimezone(timezone.utc)


def quota_day_start(now: datetime) -> datetime:
    """Minuit, heure du Pacifique, qui précède `now` (en UTC) : début du jour de quota en cours."""
    local = now.astimezone(QUOTA_TZ)
    return datetime.combine(local.date(), time(0), tzinfo=QUOTA_TZ).astimezone(timezone.utc)


def parse_models(value: str) -> list[str]:
    return [m.strip() for m in value.split(",") if m.strip()]


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


class ModelChain:
    """Essai ordonné des modèles. Une instance par processus : elle retient les modèles écartés.

    Partagée entre threads (asyncio.to_thread) : l'état des quotas est protégé par un verrou, et le modèle qui a
    répondu est renvoyé avec la réponse plutôt que retenu sur l'instance.
    """

    def __init__(self, models: list[str], *, url: str | None = None, api_key: str | None = None,
                 timeout: float = READ_TIMEOUT, session: requests.Session | None = None, clock=None,
                 limits: dict[str, tuple[int, int]] | None = None):
        if not models:
            raise ValueError("empty model chain")
        self.url = f"{(url or DEFAULT_URL).rstrip('/')}/chat/completions"
        self.models = list(models)
        self.api_key = api_key or None
        self.timeout = (CONNECT_TIMEOUT, timeout)
        self.session = session or requests.Session()
        # Injectable : les tests fixent l'heure, la production lit l'horloge système
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.limits = dict(DEFAULT_LIMITS if limits is None else limits)  # modèle -> (RPM, RPD)
        self.exhausted: dict[str, datetime] = {}  # modèle -> remise à zéro de son quota
        self.sent: dict[str, deque[datetime]] = {}  # modèle -> instants des envois, refusés compris
        self._lock = threading.Lock()
        # Par modèle : réponses obtenues et jetons, pour le journal
        self.usage: dict[str, dict[str, int]] = {}

    @classmethod
    def from_env(cls, **kwargs) -> "ModelChain":
        """SUPPORT_LLM_MODELS (liste ordonnée), SUPPORT_LLM_URL, SUPPORT_LLM_API_KEY, SUPPORT_LLM_TIMEOUT."""
        models = parse_models(os.environ.get("SUPPORT_LLM_MODELS", "")) or list(DEFAULT_MODELS)
        kwargs.setdefault("url", os.environ.get("SUPPORT_LLM_URL"))
        kwargs.setdefault("api_key", os.environ.get("SUPPORT_LLM_API_KEY"))
        kwargs.setdefault("timeout", _env_float("SUPPORT_LLM_TIMEOUT", READ_TIMEOUT))
        return cls(models, **kwargs)

    def complete(self, messages: list[dict[str, str]], response_format: dict | None = None,
                 accept=None) -> str:
        """Contenu de la réponse du premier modèle qui répond utilement (voir complete_with_model)."""
        return self.complete_with_model(messages, response_format, accept)[0]

    def complete_with_model(self, messages: list[dict[str, str]], response_format: dict | None = None,
                            accept=None) -> tuple[str, str]:
        """(contenu, modèle) du premier modèle qui répond utilement. Lève LLMError si aucun ne le fait.

        `response_format` est envoyé tel quel ; None équivaut au mode JSON. `accept(contenu) -> bool`
        permet de refuser une réponse lisible mais inexploitable : le modèle suivant est alors essayé.
        """
        errors = []
        for model in self.models:
            # Vérifier la limite et réserver l'envoi d'un seul tenant : deux threads ne dépassent pas le quota
            with self._lock:
                now = self.clock()
                reason = self._skip_reason(model, now)
                if not reason:
                    self._record(model, now)
            if reason:
                errors.append(f"{model}: {reason}")
                continue
            try:
                content = self._complete_one(model, messages, response_format or JSON_MODE, accept)
            except LLMError as e:
                errors.append(f"{model}: {e}")
                continue
            if errors:
                log.warning("LLM: %s, répondu par %s", "; ".join(errors), model)
            return content, model
        raise LLMError("aucun modèle n'a répondu — " + "; ".join(errors))

    def _skip_reason(self, model: str, now: datetime) -> str | None:
        reset = self.exhausted.get(model)
        if reset is not None and now < reset:
            return f"écarté jusqu'à {reset.astimezone(QUOTA_TZ):%d/%m %H:%M} (heure du Pacifique)"
        rpm, rpd = self.limits.get(model, (None, None))
        sent = self.sent.get(model, ())
        if rpm is not None and sum(1 for t in sent if t > now - MINUTE) >= rpm:
            return f"limite locale de {rpm} requêtes par minute atteinte"
        if rpd is not None and sum(1 for t in sent if t >= quota_day_start(now)) >= rpd:
            return f"limite locale de {rpd} requêtes par jour atteinte"
        return None

    def _record(self, model: str, now: datetime) -> None:
        sent = self.sent.setdefault(model, deque())
        sent.append(now)
        horizon = min(quota_day_start(now), now - MINUTE)
        while sent and sent[0] < horizon:
            sent.popleft()

    def _complete_one(self, model: str, messages: list[dict[str, str]], response_format: dict,
                      accept) -> str:
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0,
            "response_format": response_format,
        }
        try:
            res = post_chat(self.session, self.url, payload, self.api_key, model=model,
                            timeout=self.timeout)
        except ModelUnavailable:
            with self._lock:
                self.exhausted[model] = next_quota_reset(self.clock())
            raise
        try:
            data = res.json()
            choice = data["choices"][0]
            content = choice["message"]["content"]
            finish = choice.get("finish_reason")
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as e:
            raise LLMError("réponse malformée") from e
        with self._lock:
            self._count(model, data.get("usage"))
        if finish != "stop":
            # content_filter (refus de sécurité) ou coupure : un autre modèle peut répondre
            raise LLMError(f"réponse refusée ou coupée (finish_reason={finish})")
        if not isinstance(content, str) or not content.strip():
            raise LLMError("contenu vide")
        if accept is not None and not accept(content):
            raise LLMError("réponse illisible")
        return content

    def _count(self, model: str, usage) -> None:
        counters = self.usage.setdefault(model, {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0})
        counters["requests"] += 1
        if not isinstance(usage, dict):
            return
        for key in ("prompt_tokens", "completion_tokens"):
            if isinstance(usage.get(key), int):
                counters[key] += usage[key]
