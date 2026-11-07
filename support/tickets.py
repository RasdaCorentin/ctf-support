"""Cycle de vie d'un ticket : attente, réponse ou clôture, réactions et historique du joueur.

Couche 1 : fonctions pures sur un dict de `Ticket` indexé par `dm_id`. Aucune I/O, aucun Discord.
Chaque fonction renvoie un nouveau dict et ne modifie jamais son argument.
"""

from dataclasses import replace

from support.models import SEEN, Draft, Ticket, TicketStatus, WAITING

Tickets = dict[int, Ticket]  # indexé par dm_id

QUESTION_MAX = 500  # au-delà, la question est tronquée : elle sert de contexte au rédacteur


def open_ticket(tickets: Tickets, *, dm_id: int, channel_id: int, message_id: int, author_id: int,
                question: str, challenge_key: str | None, now: str) -> Tickets:
    ticket = Ticket(
        dm_id=dm_id,
        channel_id=channel_id,
        message_id=message_id,
        author_id=author_id,
        question=question[:QUESTION_MAX],
        challenge_key=challenge_key,
        mode=None,
        step_id=None,
        draft=None,
        reply=None,
        status=TicketStatus.WAITING,
        created_at=now,
    )
    return {**tickets, dm_id: ticket}


def set_draft(tickets: Tickets, dm_id: int, draft: Draft | None) -> Tickets:
    ticket = tickets[dm_id]
    if draft is None:
        # Seul le brouillon change : le mode et l'étape restent ceux du dernier essai
        updated = replace(ticket, draft=None)
    else:
        # Le challenge du brouillon ne remplace pas celui que le ticket connaît déjà
        challenge_key = ticket.challenge_key or draft.challenge_key
        updated = replace(
            ticket,
            draft=draft.reply,
            mode=draft.mode,
            step_id=draft.step_id,
            challenge_key=challenge_key,
        )
    return {**tickets, dm_id: updated}


def answer(tickets: Tickets, dm_id: int, reply: str) -> Tickets:
    ticket = _waiting(tickets, dm_id)
    return {**tickets, dm_id: replace(ticket, reply=reply, status=TicketStatus.ANSWERED)}


def close(tickets: Tickets, dm_id: int) -> Tickets:
    ticket = _waiting(tickets, dm_id)
    return {**tickets, dm_id: replace(ticket, status=TicketStatus.CLOSED)}


def find(tickets: Tickets, dm_id: int) -> Ticket | None:
    return tickets.get(dm_id)


def find_by_message(tickets: Tickets, channel_id: int, message_id: int) -> Ticket | None:
    """Ticket déjà ouvert pour ce message du salon, quel que soit son statut."""
    return next((t for t in tickets.values() if (t.channel_id, t.message_id) == (channel_id, message_id)), None)


def history(tickets: Tickets, author_id: int, challenge_key: str | None, limit: int = 3) -> tuple[str, ...]:
    """Questions du même auteur sur le même challenge, les `limit` plus récentes, la plus récente en dernier."""
    if challenge_key is None:
        return ()
    same = sorted(
        (t for t in tickets.values() if t.author_id == author_id and t.challenge_key == challenge_key),
        key=lambda t: t.created_at,
    )
    return tuple(t.question for t in same[-limit:])


def purge(tickets: Tickets, before: str) -> Tickets:
    """Retire les tickets terminés créés avant `before`. Un ticket en attente est gardé, quel que soit son âge."""
    return {
        dm_id: t
        for dm_id, t in tickets.items()
        if t.status == TicketStatus.WAITING or t.created_at >= before
    }


def reactions_for(status: TicketStatus) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(réactions à poser, réactions à retirer). 👀 reste posé : la trace « vu par l'orga » ne disparaît pas."""
    if status == TicketStatus.WAITING:
        return ((SEEN, WAITING), ())
    return ((), (WAITING,))


def _waiting(tickets: Tickets, dm_id: int) -> Ticket:
    ticket = tickets[dm_id]
    if ticket.status != TicketStatus.WAITING:
        raise ValueError(f"ticket {dm_id} déjà {ticket.status.value}")
    return ticket
