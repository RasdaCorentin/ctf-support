# Architecture

## Objectif

Répondre aux tickets du salon support pendant le CTF, sans jamais publier un message que l'orga n'a pas relu, et
sans qu'un flag puisse sortir du dépôt CTF.

## Vue d'ensemble

```
Joueur ──message──▶ Salon tickets
                        │   (bots et rôle orga ignorés)
                        ▼
              👀 🕐 posés, journal « ticket »
                        │
                        ▼
        DM au propriétaire : question, lien, « brouillon en cours… »
                        │
                        ├──▶ brouillon en tâche de fond
                        │      contexte : guide ou titres, FAQ, questions précédentes
                        │      rédacteur Gemini : essai de la chaîne de modèles
                        │      garde : refus = rien n'est affiché ni stocké
                        │      aucun brouillon : « Aucun brouillon utilisable »
                        ▼
        Boutons du DM :   Envoyer  │  Modifier  │  Clore
                        │
                        ▼  (Envoyer, ou Modifier puis validation)
              publish() : garde, mention de l'auteur seule
                        │
                        ▼
        Réponse sur le ticket d'origine, 🕐 retiré
```

Rien ne part dans le salon sans une action du propriétaire (bouton ou réponse en DM). `publish()` est le seul point d'écriture dans le salon.

## Couches

```
couche 2   bot.py             tools/make_guides.py    tools/check_leaks.py
              │                        │                      │
couche 1   assist   drafter/   guard   tickets   triage       │
              │        │          │        │         │        │
couche 0   models ◀───┴──────────┴────────┴─────────┘   storage ◀┘
```

- Un module de couche 1 n'importe jamais un autre module de couche 1. Ce qui sert à plusieurs vit dans `models.py`.
- Le processus du bot ne lit jamais le dépôt CTF. Seuls les outils de `tools/` y accèdent, hors ligne.

## Modules

| Module | Rôle |
|---|---|
| `support/models.py` | Contrats partagés : guide, brouillon, ticket, verdict de la garde |
| `support/storage.py` | Lecture des guides et de la FAQ, état (`tickets.json`), journal |
| `support/triage.py` | Repère le challenge cité, pour choisir le guide du rédacteur |
| `support/assist.py` | Construit ce que voit le rédacteur, et rien d'autre |
| `support/guard.py` | Contrôle de tout texte public : longueur, flag, mentions de masse, invitations |
| `support/tickets.py` | Cycle d'un ticket : attente, répondu, clos |
| `support/drafter/` | Rédacteur : chaîne de modèles Gemini, prompt, lecture de la réponse |
| `support/bot.py` | Adaptateur Discord : réactions, DM à boutons, publication |
| `guides/` | Un YAML par challenge, sans flag ni solution finale |
| `tools/make_guides.py` | Squelettes de guides, à partir des seuls champs publics du dépôt CTF |
| `tools/check_leaks.py` | Compare les flags réels du dépôt CTF aux guides et à la FAQ |

## Cycle d'un ticket

| Étape | Ce qui se passe | Où le voit l'orga |
|---|---|---|
| Arrivée | Réactions 👀 et 🕐, journal `ticket` | Salon : deux réactions, aucun message |
| Ouverture | DM avec la question et le lien, ticket `waiting` | DM : « brouillon en cours… » |
| Brouillon | Un ou deux appels au rédacteur, en tâche de fond | DM mis à jour : brouillon, mode, étape, modèle |
| Relecture | Trois boutons : Envoyer, Modifier (180 caractères), Clore | DM |
| Publication | Garde, puis réponse sur le ticket d'origine | Salon : une réponse, 🕐 retiré |
| Clôture | Rien n'est publié, 🕐 retiré | DM : « clos, rien n'est publié » |

Un ticket change d'état une seule fois : `waiting` vers `answered` ou `closed`. Un second clic reçoit « Ce ticket est
déjà traité ».

## Le rédacteur

| Point | Règle |
|---|---|
| Entrée | Guide du challenge seul si le challenge est identifié. Sinon, les titres de tous les guides |
| Autres données | FAQ (entrées avec une réponse), questions précédentes du même joueur sur ce challenge |
| Absent du contexte | Identifiant Discord, pseudo, secret, flag, dépôt CTF |
| Sortie | JSON : mode, challenge, étape, réponse |
| Second appel | Si le premier brouillon nomme un challenge connu sans guide, le rédacteur est rappelé avec ce guide. Si le second échoue, le premier est gardé |
| Modèles | Ordre de `DEFAULT_MODELS` (`support/drafter/chain.py`), 8 s de lecture par modèle, quotas locaux par modèle |
| Repli | Erreur, délai, refus de sécurité, réponse illisible : modèle suivant. Si tous échouent : message à l'orga |

Le rédacteur n'a ni outil ni accès réseau hors de l'appel à Gemini.

## Sécurité

### Le principe

Une injection de prompt contrôle ce que le modèle écrit. Elle ne lui donne aucune information nouvelle. La fuite
possible est donc limitée à ce qui est dans le contexte du modèle. Le contexte ne contient donc aucun flag et
aucune solution finale.

### Deux temps, deux niveaux de confiance

```
HORS LIGNE (avant l'événement)                 EN LIGNE (pendant l'événement)
entrée : fichiers des auteurs (confiance)      entrée : texte des joueurs (aucune confiance)

dépôt CTF ─▶ agents de rédaction ─▶ guides/   ticket ─▶ triage ─▶ rédacteur Gemini
                                      │                          contexte : guide, FAQ, historique
                       check_leaks ◀──┤                                  │
                    (échec = pas de fusion)                        garde ◀┘
                                      │                                  │
                       relecture de l'orga ◀──────────────────────────────┘
                                                                         │
                                                              Envoyer / Modifier / Clore
                                                                         │
                                                                      salon
```

### Défense en profondeur

| Couche | Ce qu'elle arrête | Limite |
|---|---|---|
| Pas de flag ni de solution finale dans les guides | Fuite de flag par le modèle | Un guide écrit pour supporter une fuite totale |
| Pas d'outil pour le modèle | Lecture de fichiers, appels réseau | Aucune |
| Garde sur toute sortie | Motif de flag, même espacé ou encodé en partie, plus de 180 caractères, `@everyone`, invitations | Les encodages exotiques passent : d'où les couches précédentes |
| Mentions limitées à l'auteur | Ping de masse ou de rôle | Aucune |
| Relecture humaine de chaque message | Brouillon faux, déplacé ou trop explicite | La fatigue. C'est la dernière barrière, pas la première |
| `check_leaks` avant fusion des guides | Flag recopié hors ligne | Compare aux flags connus : un flag non listé n'est pas détecté |

### Ce que contient un guide

- Étapes, signes qui indiquent qu'un joueur en est là, coup de pouce, pièges.
- Au plus 6 étapes et 200 caractères par champ rédigé. Un guide hors de ces bornes, ou contenant un format de flag,
  est ignoré au chargement (fail closed).
- Aucun flag, mot de passe, clé, offset exact ni payload complet.

### Données envoyées à un tiers

Le texte des tickets, les questions précédentes du joueur, le guide et la FAQ partent chez Google (Gemini). Aucun
identifiant Discord ni pseudo n'est envoyé. Les participants doivent en être informés. Les conditions de l'offre
utilisée (usage pour l'amélioration des produits, relecture humaine possible) sont à relire avant l'événement.

### Risques résiduels assumés

| Risque | Parade |
|---|---|
| Brouillon faux ou trompeur | Relecture, bouton Modifier |
| Guide trop explicite | Relecture des guides (L32) |
| Insistance d'un joueur qui obtient une réponse trop généreuse | Aucune mesure technique : jugement de l'orga |
| Refus de sécurité de Gemini sur un sujet offensif | Modèle suivant ; si tous refusent, rédaction à la main |
| Panne, quota du jour épuisé, clé révoquée | Message à l'orga ; le bot reste utilisable sans brouillon |
| Quota vidé volontairement par un joueur | Plus de brouillons jusqu'à minuit, heure du Pacifique. Parade possible : limite par joueur |

## Décisions et compromis

| Sujet | Décision | Compromis |
|---|---|---|
| Publication | Rien sans clic du propriétaire | Une attention humaine constante pendant l'événement |
| Réponses | Liées à la question posée, sans numérotation | Le modèle doit situer le joueur : un guide complet est nécessaire |
| Connaissance | Un guide par challenge, relu ou non | Un guide non relu reste utilisé : le champ `reviewed` n'a aucun effet sur le rédacteur |
| Rédacteur | Gemini, chaîne de modèles | Données envoyées à Google. Quotas gratuits par modèle. Choix détaillé dans [llm-options.md](llm-options.md) |
| Tri | Repérage déterministe du challenge, sans LLM | Un ticket sans challenge reconnu reçoit un brouillon sans guide, donc plus générique |
| Mode d'essai | `SUPPORT_DRY_RUN=1` par défaut | Voir [runbook.md](runbook.md) : en dry-run, « Envoyer » marque quand même le ticket répondu |
| Relais | Le propriétaire peut répondre en DM à un ticket, avec « Répondre » | Même garde que les boutons |

## Limites connues

- Un brouillon peut inventer une réponse. Seule la relecture le protège.
- Quand un guide ne contient aucune étape (squelette non rédigé), le rédacteur ne connaît que le titre et l'énoncé.
- Une réponse en DM à un ticket absent de `tickets.json` est refusée : « Je ne retrouve pas ce ticket ».
