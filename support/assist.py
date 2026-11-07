"""Contexte du rédacteur : ce que voit le modèle pour une question, et rien d'autre.

Couche 1 : fonctions pures. N'importe que `support.models`. Le choix du contenu est ici, pas dans le rédacteur :
c'est la seule liste de ce qui sort vers Gemini.
"""

from support.models import DraftContext, FaqEntry, Guide


def build_context(question: str, challenge_key: str | None, guides: dict[str, Guide],
                  faq: list[FaqEntry], history: tuple[str, ...]) -> DraftContext:
    """Challenge connu : son guide seul. Sinon : « clé : titre » de tous les guides, pour que le modèle le situe.

    La clé contient le nom du dossier, que les joueurs citent souvent à la place du titre public ; c'est aussi
    ce que le modèle doit renvoyer pour qu'un second appel lui donne le guide.
    """
    guide = guides.get(challenge_key) if challenge_key else None
    titles = () if guide is not None else tuple(f"{g.key} : {g.title}" for g in guides.values())
    return DraftContext(
        question=question,
        guide=guide,
        titles=titles,
        # Une entrée sans réponse n'apporte rien au modèle et ne doit pas lui laisser croire qu'elle existe
        faq=tuple(entry for entry in faq if entry.answer.strip()),
        history=history,
    )
