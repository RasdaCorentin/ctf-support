"""Bot Discord du salon tickets.

Variables d'environnement (lues depuis .env si présent, sinon depuis l'environnement) :
  DISCORD_TOKEN       token du bot
  TICKETS_CHANNEL_IDS identifiants des salons tickets, séparés par des virgules
  SUPPORT_DRY_RUN     "0" pour publier réellement ; sinon le bot journalise seulement (défaut)
  TRIGGER_USER_IDS    identifiants Discord autorisés à ouvrir un ticket par 👀, séparés par des virgules
  LLM_ENABLED         "1" pour demander un brouillon à Gemini (défaut "0"). Exige SUPPORT_LLM_API_KEY
  LLM_CONCURRENCY     appels simultanés au rédacteur (défaut 2)
  LLM_MAX_QUEUE       brouillons en cours au-delà desquels il n'y en a plus (défaut 10)

Un message d'un salon tickets devient un ticket seulement quand une personne autorisée (TRIGGER_USER_IDS, et toujours le
propriétaire) y pose 👀. Le propriétaire reçoit alors le ticket en DM, avec trois boutons : Envoyer, Modifier, Clore.
Rien ne part dans le salon sans un clic ou une réponse du propriétaire. Seule `publish()` écrit dans le salon.
"""

import asyncio
import os
import re
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import discord

from support import assist
from support import storage as store
from support import tickets as tk
from support import triage
from support.drafter import GeminiDrafter
from support.drafter.chain import ModelChain
from support.guard import check_reply
from support.models import MAX_REPLY, SEEN, Draft, TicketStatus

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

REFUSAL_LABELS = {
    "empty": "message vide",
    "flag": "contient un format de flag",
    "mass_mention": "mention de masse",
    "invite": "invitation Discord",
    "http": "échec de l'envoi sur Discord",
}

TICKET_ID_TEMPLATE = r"ticket:(?P<action>send|edit|close):(?P<dm_id>\d+)"
BUTTON_LABELS = {
    "send": ("Envoyer", discord.ButtonStyle.success),
    "edit": ("Modifier", discord.ButtonStyle.secondary),
    "close": ("Clore", discord.ButtonStyle.danger),
}


def load_env(path: Path = ENV_FILE) -> None:
    """Charge les paires KEY=VALUE de .env. L'environnement existant reste prioritaire."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


# Renseignés par main() : importer ce module ne lit ni .env ni l'environnement
CHANNEL_IDS: frozenset[int] = frozenset()
DRY_RUN = True
TRIGGER_USER_IDS: frozenset[int] = frozenset()  # en plus du propriétaire, toujours autorisé

intents = discord.Intents.default()
intents.message_content = True

OWNER = None  # utilisateur propriétaire de l'application, renseigné par setup_hook

# Rédacteur : renseigné par main() (chaîne, si LLM_ENABLED=1) puis setup_hook (guides, FAQ, rédacteur)
LLM_CHAIN: ModelChain | None = None
LLM_CONCURRENCY = 2
LLM_MAX_QUEUE = 10
DRAFTER: GeminiDrafter | None = None
GUIDES: dict = {}
FAQ: list = []
_DRAFT_SLOTS = asyncio.Semaphore(LLM_CONCURRENCY)
# Une référence par tâche de brouillon : sans elle, le ramasse-miettes peut interrompre une tâche en cours
_DRAFT_TASKS: set[asyncio.Task] = set()

DRAFT_PENDING_NOTE = "brouillon en cours…"
NO_DRAFT_NOTE = "⚠️ pas de brouillon"
FULL_QUEUE_NOTE = "⚠️ file pleine, pas de brouillon : rédige toi-même"

# Lecture, transition et écriture de tickets.json sous ce verrou : deux clics ou deux messages simultanés
# ne doivent ni publier deux fois ni s'écraser. Tenu aussi pendant la publication, pour qu'un seul gagne.
_TICKETS_LOCK = asyncio.Lock()


# Messages dont le ticket est en cours d'ouverture : deux 👀 posés en même temps n'en ouvrent qu'un
_OPENING: set[tuple[int, int]] = set()


def is_trigger(emoji: str, user_id: int, channel_id: int, *, channels: frozenset[int], allowed: frozenset[int],
               owner_id: int | None) -> bool:
    """👀 posé dans un salon tickets par une personne autorisée.

    Le tri est humain : un joueur ne peut ni ouvrir de ticket, ni déclencher le rédacteur, ni vider le quota.
    Le 👀 que le bot pose lui-même n'est jamais un déclencheur (le bot n'est pas dans la liste).
    """
    return emoji == SEEN and channel_id in channels and (user_id in allowed or user_id == owner_id)


class AlreadyHandled(Exception):
    """Le ticket n'est plus en attente : une réponse ou une clôture est déjà passée."""


class SupportClient(discord.Client):
    async def setup_hook(self) -> None:
        # Avant la connexion à la passerelle : aucun événement ne peut arriver avec OWNER encore à None
        global OWNER, GUIDES, FAQ, DRAFTER
        OWNER = (await self.application_info()).owner
        # Chargés une fois : un guide modifié exige un redémarrage, comme le reste de la configuration
        GUIDES, guide_errors = store.load_guides()
        for error in guide_errors:
            print(f"Guide ignoré : {error}")
        FAQ = store.load_faq()
        if LLM_CHAIN is not None:
            DRAFTER = GeminiDrafter(LLM_CHAIN, set(GUIDES))
        # Les boutons des DM envoyés avant un redémarrage restent actifs
        self.add_dynamic_items(TicketButton)
        await purge_old_tickets()

    async def on_ready(self) -> None:
        mode = "DRY-RUN, rien n'est publié" if DRY_RUN else "PUBLICATION ACTIVE"
        print(f"Connecté en tant que {self.user} ({mode}), propriétaire : {OWNER.name}, "
              f"{len(CHANNEL_IDS)} salon(x) tickets, {len(TRIGGER_USER_IDS)} autre(s) personne(s) autorisée(s) à ouvrir un ticket par 👀")

    async def on_message(self, message) -> None:
        # Le salon est libre : un message n'y devient un ticket que par 👀 (on_raw_reaction_add)
        if message.author.bot or message.guild is not None:
            return
        try:
            await handle_owner_dm(message)
        except Exception as exc:
            await report_error(message.jump_url, exc)

    async def on_raw_reaction_add(self, payload) -> None:
        # Brut : le message réagi n'est pas forcément dans le cache du client
        if not is_trigger(str(payload.emoji), payload.user_id, payload.channel_id, channels=CHANNEL_IDS,
                          allowed=TRIGGER_USER_IDS, owner_id=OWNER.id):
            return
        try:
            await open_from_reaction(payload.channel_id, payload.message_id)
        except Exception as exc:
            await report_error(f"message {payload.message_id} du salon", exc)


client = SupportClient(intents=intents)


async def _load_tickets() -> dict:
    return await asyncio.to_thread(store.load_tickets)


async def _save_tickets(tickets: dict) -> None:
    await asyncio.to_thread(store.save_tickets, tickets)


async def _log(kind: str, user_id: int, text: str, reply: str) -> None:
    await asyncio.to_thread(store.log_event, kind, user_id, text, reply)


async def purge_old_tickets() -> None:
    """Retire les tickets clos depuis plus de 7 jours. Un ticket en attente est toujours gardé (tk.purge)."""
    before = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    async with _TICKETS_LOCK:
        await _save_tickets(tk.purge(await _load_tickets(), before))


async def report_error(where: str, exc: BaseException) -> None:
    """Erreur non prévue : journal local, et DM à l'orga qui dit quoi regarder. Ne relance jamais."""
    traceback.print_exception(exc)
    try:
        await OWNER.send(f"Erreur sur {where} : {type(exc).__name__}")
    except Exception as report_exc:  # le signalement lui-même ne doit pas faire planter le bot
        print(f"Signalement impossible : {type(report_exc).__name__}")


def parse_custom_id(custom_id: str) -> tuple[str, int] | None:
    match = re.fullmatch(TICKET_ID_TEMPLATE, custom_id)
    if match is None:
        return None
    return match["action"], int(match["dm_id"])


def format_dm(question: str, author_id: int, link: str, draft: str | None = None, note: str | None = None) -> str:
    quoted = "\n".join(f"> {line}" for line in question[:tk.QUESTION_MAX].splitlines()) or "> (message vide)"
    lines = [f"**TICKET** de <@{author_id}> :", quoted, link, f"**Brouillon** : {draft or '—'}"]
    if note:
        lines.append(note)
    return "\n".join(lines)


def ticket_view(dm_id: int, *, active: bool, has_draft: bool) -> discord.ui.View:
    view = discord.ui.View(timeout=None)  # pas de délai : le DM reste utilisable
    view.add_item(TicketButton("send", dm_id, disabled=not (active and has_draft)))
    view.add_item(TicketButton("edit", dm_id, disabled=not active))
    view.add_item(TicketButton("close", dm_id, disabled=not active))
    return view


class TicketButton(discord.ui.DynamicItem[discord.ui.Button], template=TICKET_ID_TEMPLATE):
    def __init__(self, action: str, dm_id: int, *, disabled: bool = False):
        label, style = BUTTON_LABELS[action]
        super().__init__(
            discord.ui.Button(label=label, style=style, custom_id=f"ticket:{action}:{dm_id}", disabled=disabled)
        )
        self.action = action
        self.dm_id = dm_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["action"], int(match["dm_id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            await self._act(interaction)
        except Exception as exc:
            await report_error(interaction.message.jump_url if interaction.message else f"ticket {self.dm_id}", exc)

    async def _act(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != OWNER.id:
            await interaction.response.send_message("Réservé au propriétaire du bot.", ephemeral=True)
            return
        ticket = tk.find(await _load_tickets(), self.dm_id)
        if ticket is None or ticket.status != TicketStatus.WAITING:
            await interaction.response.send_message("Ce ticket est déjà traité.", ephemeral=True)
            return
        if self.action == "edit":
            await interaction.response.send_modal(EditModal(self.dm_id, ticket.draft))
            return

        await interaction.response.defer(ephemeral=True)
        try:
            if self.action == "send":
                if ticket.draft is None:
                    await interaction.followup.send("Pas de brouillon : utilise Modifier.", ephemeral=True)
                    return
                published, reasons = await answer_ticket(self.dm_id, ticket.draft)
                await interaction.followup.send(outcome_text(published, reasons, ticket.draft), ephemeral=True)
            else:
                await close_ticket(self.dm_id)
                await interaction.followup.send("Clos, rien n'est publié.", ephemeral=True)
        except AlreadyHandled:
            await interaction.followup.send("Ce ticket est déjà traité.", ephemeral=True)


class EditModal(discord.ui.Modal, title="Réponse au ticket"):
    def __init__(self, dm_id: int, draft: str | None):
        super().__init__()
        self.dm_id = dm_id
        self.text = discord.ui.TextInput(
            label="Texte publié sur le ticket",
            style=discord.TextStyle.paragraph,
            default=draft or None,
            min_length=1,
            max_length=MAX_REPLY,
        )
        self.add_item(self.text)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            await self._submit(interaction)
        except Exception as exc:
            await report_error(f"fenêtre de modification du ticket {self.dm_id}", exc)

    async def _submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != OWNER.id:
            await interaction.response.send_message("Réservé au propriétaire du bot.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        text = self.text.value
        try:
            published, reasons = await answer_ticket(self.dm_id, text)
        except AlreadyHandled:
            await interaction.followup.send("Ce ticket est déjà traité.", ephemeral=True)
            return
        await interaction.followup.send(outcome_text(published, reasons, text), ephemeral=True)


async def apply_reactions(message, status: TicketStatus) -> None:
    """Réactions du ticket : 👀🕐 à l'ouverture, 🕐 retiré à la réponse ou à la clôture. Rien en dry-run."""
    to_add, to_remove = tk.reactions_for(status)
    if DRY_RUN:
        return
    for emoji in to_remove:
        await message.remove_reaction(emoji, client.user)
    for emoji in to_add:
        await message.add_reaction(emoji)


async def fetch_ticket_message(ticket):
    channel = client.get_channel(ticket.channel_id) or await client.fetch_channel(ticket.channel_id)
    return await channel.fetch_message(ticket.message_id)


async def open_from_reaction(channel_id: int, message_id: int) -> None:
    """Ouvre le ticket d'un message du salon, sauf s'il en a déjà un ou si son auteur est un bot."""
    key = (channel_id, message_id)
    if key in _OPENING:
        return
    _OPENING.add(key)  # avant tout await : un second 👀 simultané s'arrête au test ci-dessus
    try:
        if tk.find_by_message(await _load_tickets(), channel_id, message_id) is not None:
            return
        channel = client.get_channel(channel_id) or await client.fetch_channel(channel_id)
        message = await channel.fetch_message(message_id)
        if message.author.bot:
            return
        # Rien n'est publié sans relecture : le ticket part en DM à l'orga, sans réponse dans le salon
        await _log("ticket", message.author.id, message.content, "")
        await forward_to_owner(message)
    finally:
        _OPENING.discard(key)


async def forward_to_owner(message) -> None:
    await apply_reactions(message, TicketStatus.WAITING)
    challenge_key = triage.identify(message.content, GUIDES)
    dm = await OWNER.send(format_dm(message.content, message.author.id, message.jump_url,
                                    note=DRAFT_PENDING_NOTE if DRAFTER is not None else None))
    async with _TICKETS_LOCK:
        # Instantané avant ouverture : l'historique du joueur ne doit pas contenir la question en cours
        previous = await _load_tickets()
        tickets = tk.open_ticket(
            previous,
            dm_id=dm.id,
            channel_id=message.channel.id,
            message_id=message.id,
            author_id=message.author.id,
            question=message.content,
            challenge_key=challenge_key,
            now=datetime.now(timezone.utc).isoformat(),
        )
        await _save_tickets(tickets)
    # Les boutons portent l'identifiant du DM : ils ne peuvent être posés qu'après l'envoi
    await dm.edit(view=ticket_view(dm.id, active=True, has_draft=False))
    if DRAFTER is None:
        return
    if len(_DRAFT_TASKS) >= LLM_MAX_QUEUE:
        await refresh_dm(dm.id, message.jump_url, note=FULL_QUEUE_NOTE)
        return
    task = asyncio.create_task(
        draft_for(dm.id, message.jump_url, message.content, message.author.id, challenge_key, previous)
    )
    _DRAFT_TASKS.add(task)
    task.add_done_callback(_DRAFT_TASKS.discard)


async def draft_for(dm_id: int, link: str, question: str, author_id: int, challenge_key: str | None,
                    previous: tk.Tickets) -> None:
    """Brouillon d'un ticket, en tâche de fond. Une exception est signalée à l'orga, jamais avalée."""
    try:
        async with _DRAFT_SLOTS:
            context = assist.build_context(question, challenge_key, GUIDES, FAQ,
                                           tk.history(previous, author_id, challenge_key))
            draft = await DRAFTER.draft(context)
            # Sans guide au premier appel, le rédacteur a pu nommer le challenge : il est redemandé avec son guide,
            # et ce second brouillon remplace le premier. Si le second échoue, le premier reste meilleur que rien
            if context.guide is None and draft is not None and draft.challenge_key in GUIDES:
                context = assist.build_context(question, draft.challenge_key, GUIDES, FAQ,
                                               tk.history(previous, author_id, draft.challenge_key))
                draft = await DRAFTER.draft(context) or draft
        await _settle_draft(dm_id, link, question, author_id, draft)
    except Exception as exc:
        await report_error(f"brouillon du ticket {dm_id}", exc)


async def _settle_draft(dm_id: int, link: str, question: str, author_id: int, draft: Draft | None) -> None:
    async with _TICKETS_LOCK:
        tickets = await _load_tickets()
        ticket = tk.find(tickets, dm_id)
        if ticket is None or ticket.status != TicketStatus.WAITING:
            # Répondu ou clos à la main pendant la rédaction : ni état ni DM ne bougent
            return
        if draft is None:
            await _log("draft", author_id, question, "aucun brouillon")
            await refresh_dm(dm_id, link, note=NO_DRAFT_NOTE)
            # Nouveau message et non modification : seul un message notifie l'orga
            dm = await _owner_dm(dm_id)
            await dm.reply(f"Aucun brouillon utilisable (aucun modèle n'a répondu, ou réponse inexploitable). À toi de rédiger : {link}")
            return
        verdict = check_reply(draft.reply)
        if not verdict.ok:
            # Ni affiché ni stocké, et pas journalisé : un refus de garde peut contenir un flag
            await _log("draft", author_id, question, f"[{draft.mode.value}/{draft.step_id}] refusé : "
                                                     f"{', '.join(verdict.reasons)}")
            await refresh_dm(dm_id, link, note=f"⚠️ brouillon refusé par la garde "
                                               f"({describe_refusal(verdict.reasons, len(draft.reply))})")
            return
        updated = tk.set_draft(tickets, dm_id, draft)
        await _save_tickets(updated)
        await _log("draft", author_id, question, f"[{draft.mode.value}/{draft.step_id}] {draft.reply}")
        await refresh_dm(dm_id, link, note=draft_note(draft, updated[dm_id].challenge_key))


def draft_note(draft: Draft, challenge_key: str | None) -> str:
    """Mode, étape et rédacteur : l'orga voit où en est le joueur avant de relire."""
    guide = GUIDES.get(challenge_key) if challenge_key else None
    step = next((s for s in guide.steps if s.id == draft.step_id), None) if guide and draft.step_id else None
    where = step.goal if step else "non située"
    return f"Mode : {draft.mode.value} · étape : {where} · rédigé par {draft.model or 'modèle inconnu'}"


async def _owner_dm(dm_id: int):
    channel = OWNER.dm_channel or await OWNER.create_dm()
    return await channel.fetch_message(dm_id)


async def refresh_dm(dm_id: int, link: str, note: str | None = None) -> None:
    ticket = tk.find(await _load_tickets(), dm_id)
    dm = await _owner_dm(dm_id)
    active = ticket.status == TicketStatus.WAITING
    await dm.edit(
        content=format_dm(ticket.question, ticket.author_id, link, ticket.draft, note),
        view=ticket_view(dm_id, active=active, has_draft=ticket.draft is not None),
    )


async def answer_ticket(dm_id: int, text: str) -> tuple[bool, tuple[str, ...]]:
    """Publie `text` sur le ticket puis passe le ticket à « répondu ». Seul chemin vers `answered`."""
    async with _TICKETS_LOCK:
        tickets = await _load_tickets()
        ticket = tk.find(tickets, dm_id)
        if ticket is None or ticket.status != TicketStatus.WAITING:
            raise AlreadyHandled
        original = await fetch_ticket_message(ticket)
        published, reasons = await publish(original, text)
        if not published:
            return False, reasons
        await _save_tickets(tk.answer(tickets, dm_id, text))
        await _log("reply", OWNER.id, text, f"ticket {ticket.message_id}")
        await apply_reactions(original, TicketStatus.ANSWERED)
        await refresh_dm(dm_id, original.jump_url, note=f"✅ envoyé : {text}")
        return True, ()


async def close_ticket(dm_id: int) -> None:
    async with _TICKETS_LOCK:
        tickets = await _load_tickets()
        ticket = tk.find(tickets, dm_id)
        if ticket is None or ticket.status != TicketStatus.WAITING:
            raise AlreadyHandled
        original = await fetch_ticket_message(ticket)
        await _save_tickets(tk.close(tickets, dm_id))
        await apply_reactions(original, TicketStatus.CLOSED)
        await refresh_dm(dm_id, original.jump_url, note="clos, rien n'est publié")


def describe_refusal(reasons: tuple[str, ...], length: int) -> str:
    parts = []
    for reason in reasons:
        if reason == "too_long":
            parts.append(f"{length} caractères, maximum {MAX_REPLY}")
        else:
            parts.append(REFUSAL_LABELS.get(reason, reason))
    return ", ".join(parts)


def outcome_text(published: bool, reasons: tuple[str, ...], text: str) -> str:
    if published:
        return "✅ Publié."
    return f"Non publié : {describe_refusal(reasons, len(text))}."


async def publish(target, text: str) -> tuple[bool, tuple[str, ...]]:
    """Seul point d'écriture dans le salon : toute publication passe par la garde.

    Renvoie (publié, raisons de refus). En dry-run, la publication est seulement journalisée.
    """
    verdict = check_reply(text)
    if not verdict.ok:
        return False, verdict.reasons
    if DRY_RUN:
        print(f"[DRY-RUN] publication sur {target.id} : {text}")
        return True, ()
    try:
        await target.reply(
            text,
            mention_author=True,
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=[target.author]),
        )
    except discord.HTTPException as exc:
        print(f"Échec de publication sur {target.id} (HTTP {exc.status})")
        return False, ("http",)
    return True, ()


async def handle_owner_dm(message) -> None:
    if message.author.id != OWNER.id:
        return  # DM d'un autre utilisateur : ignoré

    if message.reference is None:
        await message.channel.send(
            "Pour répondre à un ticket, utilise « Répondre » sur le message de ticket que je t'ai envoyé."
        )
        return

    if tk.find(await _load_tickets(), message.reference.message_id) is None:
        await message.channel.send("Je ne retrouve pas ce ticket dans la file d'attente.")
        return
    try:
        published, reasons = await answer_ticket(message.reference.message_id, message.content)
    except AlreadyHandled:
        await message.channel.send("Ce ticket est déjà traité.")
        return
    if not published:
        await message.channel.send(outcome_text(False, reasons, message.content))
        return
    await message.add_reaction("✅")


def parse_ids(value: str) -> frozenset[int]:
    return frozenset(int(part) for part in re.split(r"[,\s]+", value) if part)


def main() -> None:
    global CHANNEL_IDS, DRY_RUN, TRIGGER_USER_IDS, LLM_CHAIN, LLM_CONCURRENCY, LLM_MAX_QUEUE, _DRAFT_SLOTS
    load_env()
    CHANNEL_IDS = parse_ids(os.environ.get("TICKETS_CHANNEL_IDS", ""))
    if not CHANNEL_IDS:
        raise SystemExit("TICKETS_CHANNEL_IDS est vide : renseigne au moins un identifiant de salon dans .env.")
    DRY_RUN = os.environ.get("SUPPORT_DRY_RUN", "1") != "0"
    TRIGGER_USER_IDS = parse_ids(os.environ.get("TRIGGER_USER_IDS", ""))
    if os.environ.get("LLM_ENABLED", "0") == "1":
        # Échec ici plutôt qu'un bot qui ne rédige jamais sans rien dire. La valeur de la clé n'est jamais affichée
        if not os.environ.get("SUPPORT_LLM_API_KEY", "").strip():
            raise SystemExit("LLM_ENABLED=1 mais SUPPORT_LLM_API_KEY est vide : renseigne-la dans .env, "
                             "ou mets LLM_ENABLED=0.")
        # Une seule chaîne pour tout le processus : elle retient les quotas déjà épuisés
        LLM_CHAIN = ModelChain.from_env()
    LLM_CONCURRENCY = int(os.environ.get("LLM_CONCURRENCY", "2"))
    LLM_MAX_QUEUE = int(os.environ.get("LLM_MAX_QUEUE", "10"))
    _DRAFT_SLOTS = asyncio.Semaphore(LLM_CONCURRENCY)
    client.run(os.environ["DISCORD_TOKEN"])


if __name__ == "__main__":
    main()
