# Options LLM pour le rédacteur

Ce document garde la trace du choix du rédacteur, des options écartées et de la raison de chacune.

## Décision (2026-10-22)

| Point | Décision |
|---|---|
| Rédacteur | Gemini, par l'API compatible OpenAI, en chaîne de modèles du meilleur au moins bon |
| Ordre de la chaîne | `DEFAULT_MODELS` dans `support/drafter/chain.py` : `gemini-3.8-flash`, `gemini-3.6-flash`, `gemini-3-flash-preview`, `gemini-3.5-flash-lite`, `gemini-3.1-flash-lite`. **Ordre à confirmer en L42** |
| Délai | 8 s de lecture par modèle (`SUPPORT_LLM_TIMEOUT`), 1 s de connexion (fixe) |
| Repli | Erreur, délai, refus de sécurité, réponse illisible : modèle suivant, tout de suite. Si aucun modèle ne répond : le bot envoie « Aucun brouillon utilisable (aucun modèle n'a répondu, ou réponse inexploitable). À toi de rédiger », et l'orga rédige lui-même |
| Quotas | Suivis localement par modèle (par minute et par jour). Un 429 « PerDay » ou un 404 écarte le modèle jusqu'à minuit, heure du Pacifique |
| Données envoyées | Question, questions précédentes du même joueur, guide, FAQ. Jamais d'identifiant Discord ni de pseudo |

### Ce que ce choix coûte

| Coût | Conséquence |
|---|---|
| Données chez Google | Le texte des tickets et les guides sortent du PC. Les participants doivent en être informés. Les conditions de l'offre utilisée sont à relire avant l'événement |
| Quotas gratuits | Un joueur qui inonde le salon peut vider le quota du jour. Le bot continue sans brouillon |
| Dépendance à un service distant | Une panne ou une clé révoquée supprime les brouillons. Le repli à la main reste possible |
| Modèles qui changent | Un identifiant retiré répond 404 : il est écarté, le suivant prend le relais. La liste doit être vérifiée avant l'événement |

### Mesures

> **À compléter par L42.** Aucune mesure n'est encore consignée. Ne pas s'appuyer sur les chiffres de ce document
> avant cette section.

| Modèle | Délai observé | Réponses utilisables | Refus de sécurité | Qualité relue |
|---|---|---|---|---|
| `gemini-3.8-flash` | à mesurer | à mesurer | à mesurer | à mesurer |
| `gemini-3.6-flash` | à mesurer | à mesurer | à mesurer | à mesurer |
| `gemini-3-flash-preview` | à mesurer | à mesurer | à mesurer | à mesurer |
| `gemini-3.5-flash-lite` | à mesurer | à mesurer | à mesurer | à mesurer |
| `gemini-3.1-flash-lite` | à mesurer | à mesurer | à mesurer | à mesurer |

## Options écartées

| Option | Coût | Verdict et raison |
|---|---|---|
| API Anthropic (Haiku) | À l'usage | **Écartée** : dépense refusée |
| `claude -p` persistant (stream-json) | Quota de l'abonnement | **Écartée** : le contexte est partagé entre tickets. Une influence ou une injection persiste d'un ticket à l'autre |
| `claude -p`, un processus par ticket | Quota de l'abonnement | **Écartée** : environ 6 s par requête. Les instructions globales de l'utilisateur restent dans le contexte. Dépend de la session connectée sur ce PC |
| `claude -p --bare` | — | **Inutilisable** : exige une clé API |
| Copie de `.credentials.json` dans un dossier isolé | Gratuit | **Écartée** : duplique un secret OAuth. Le refresh token peut se désynchroniser |
| `CLAUDE_CONFIG_DIR` avec une nouvelle connexion | Gratuit si même abonnement | **Non testée** : soumise au quota et aux conditions d'usage d'un abonnement pour un service automatisé |
| Ollama en local (modèle quantifié sur CPU) | Gratuit | **Non retenue** pour l'événement. Jamais testée : vitesse et qualité du français restent à mesurer sur cette machine. Les raisons du choix de Gemini ne sont pas consignées dans le plan : **à compléter** |
| Mots-clés seuls (version précédente) | Gratuit | **Remplacé** : le tri par mots-clés ne décide plus de la réponse. Il ne sert qu'à choisir le guide du rédacteur |

### Mesures antérieures avec Claude Code (CLI 2.1.306)

Gardées parce qu'elles ont motivé plusieurs écartements.

| Test | Résultat |
|---|---|
| `claude -p` persistant, deux messages | Une réponse par message, mais contexte partagé : le modèle a retenu un mot du premier message |
| `--tools ""` | Aucun outil disponible |
| Dossier de travail vide | Le CLAUDE.md global reste chargé |
| `CLAUDE_CONFIG_DIR` vers un dossier vide | Échec : « Not logged in » |

## Risques et limites

- Le rédacteur peut inventer une réponse. Le DM de validation et la relecture de l'orga restent la protection.
- Le rédacteur ne lit que ce que `assist.py` lui donne. Il n'a aucun outil ni accès réseau hors de l'appel.
- Les guides sont la seule connaissance des challenges : un guide faux donne un brouillon faux.

## Questions ouvertes

1. Ordre définitif de la chaîne et identifiants réels des modèles (L42).
2. Mesures de latence et de qualité (L42).
3. Conditions de l'offre Gemini utilisée : usage des données pour l'amélioration des produits, relecture humaine
   (à relire avant de rédiger la page d'information des participants).
4. Raisons du choix de Gemini plutôt qu'Ollama : à consigner ici.
