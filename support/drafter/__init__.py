"""Rédacteur des brouillons : chaîne de modèles Gemini (couche 1).

Implémente le protocole `Drafter` de support/models.py. Ne reçoit que `DraftContext` : ne lit aucun fichier et
n'a aucun outil.
"""

import asyncio
from dataclasses import replace

from support.drafter.chain import LLMError, ModelChain
from support.drafter.prompt import RESPONSE_FORMAT, build_messages, parse_draft
from support.models import MAX_REPLY, Draft, DraftContext


class GeminiDrafter:
    def __init__(self, chain: ModelChain, known_keys: set[str]):
        self.chain = chain
        self.known_keys = set(known_keys)

    async def draft(self, context: DraftContext) -> Draft | None:
        step_ids = {step.id for step in context.guide.steps} if context.guide else set()
        messages = build_messages(context)
        try:
            raw, model = await asyncio.to_thread(self._ask, messages, step_ids)
            draft = parse_draft(raw, self.known_keys, step_ids)
            if draft is not None and len(draft.reply) > MAX_REPLY:
                # Une seule relance : le modèle voit sa réponse trop longue et doit la réécrire
                retry = messages + [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": (f"Trop long ({len(draft.reply)} caractères). "
                                                 f"Réécris en {MAX_REPLY} caractères maximum.")},
                ]
                raw, model = await asyncio.to_thread(self._ask, retry, step_ids)
                draft = parse_draft(raw, self.known_keys, step_ids)
        except LLMError:
            return None
        if draft is None or len(draft.reply) > MAX_REPLY:
            return None
        return replace(draft, model=model)

    def _ask(self, messages: list[dict], step_ids: set[str]) -> tuple[str, str]:
        # Une réponse illisible fait passer au modèle suivant, et non une nouvelle tentative du même
        return self.chain.complete_with_model(
            messages, RESPONSE_FORMAT,
            accept=lambda raw: parse_draft(raw, self.known_keys, step_ids) is not None,
        )


class FakeDrafter:
    """Brouillons préparés, rendus dans l'ordre (None une fois épuisés). Attend `delay` s avant chaque réponse."""

    def __init__(self, drafts: list[Draft | None] = (), delay: float = 0.0):
        self.drafts = list(drafts)
        self.delay = delay
        self.contexts: list[DraftContext] = []

    async def draft(self, context: DraftContext) -> Draft | None:
        self.contexts.append(context)
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.drafts.pop(0) if self.drafts else None
