# ctf-support

ctf-support prépare des brouillons de réponse pour l'équipe d'organisation lors des demandes d'assistance sur Discord pendant un CTF.

## Ce que fait le projet

Le projet assiste l'équipe d'organisation d'un CTF pour répondre aux tickets déposés sur Discord.
Lorsqu'un membre autorisé pose une réaction sur un message dans un salon dédié, le bot prend en charge la demande sans rien envoyer dans le salon public.
Le bot transmet la question à l'organisateur en message privé et prépare un brouillon de réponse en tâche de fond.

Le fonctionnement repose sur le principe de la validation humaine avant toute publication.
L'organisateur reçoit le brouillon en message privé avec 3 boutons : Envoyer, Modifier et Clore.
Aucun message ne part dans le salon sans une action explicite de l'organisateur.
La fonction de publication constitue l'unique point d'écriture dans le salon public.
Avant la publication, le texte passe par une garde de contrôle qui refuse les messages vides, les textes dépassant 180 caractères, les mentions de masse, les invitations Discord et les motifs de flag.

L'outil tools/check_leaks.py assure un contrôle anti-fuite hors ligne avant l'événement.
Cet outil compare les secrets du dépôt CTF avec le contenu des guides et du fichier faq.yaml.
Il extrait les secrets des fichiers flag.txt, des champs flags de challenge.yaml et des motifs trouvés dans README.md.
Le contrôle compare le flag entier ainsi que sa partie intérieure dès qu'elle atteint 8 caractères.
En cas de détection, l'outil affiche l'emplacement et la ligne sans jamais afficher la valeur du secret.

Les guides de défis réels ne sont pas publiés dans ce dépôt.
Seul le fichier guides/_template.yaml est présent comme modèle.
L'utilisateur doit fournir ses propres guides pour chaque défi.

## État d'avancement

### Ce qui fonctionne

- Prise en charge d'un message après une réaction posée par une personne autorisée.
- Triage déterministe du défi à partir du titre ou du dossier sans modèle de langage.
- Préparation d'un contexte minimal pour la rédaction, incluant le guide, la foire aux questions et au plus 3 questions antérieures du joueur.
- Rédaction de brouillons avec une chaîne de modèles Gemini et repli automatique en cas d'erreur ou de quota atteint.
- Suivi local des quotas de requêtes par minute et par jour pour chaque modèle.
- Contrôle de sortie par une garde rejetant les formats de flag, les mentions de masse, les invitations et les messages de plus de 180 caractères.
- Interface de gestion en message privé avec boutons Envoyer, Modifier et Clore.
- Possibilité pour l'organisateur de répondre en message privé via la fonction Répondre de Discord.
- Contrôle anti-fuite hors ligne par comparaison des flags avec tools/check_leaks.py.
- Génération hors ligne de squelettes de guides à partir des champs publics des défis avec tools/make_guides.py.
- Enregistrement de l'état des tickets en JSON et journalisation des événements en JSONL.
- Suppression automatique des tickets clos depuis plus de 7 jours.
- Mode simulation sans publication réelle activable par variable d'environnement.

### Ce qui n'est pas fait

- Les mesures de latence, de taux de réponses exploitables, de refus de sécurité et de qualité des modèles ne sont pas consignées.
- L'ordre définitif des modèles de la chaîne et leurs identifiants restent à confirmer.
- La page d'information des participants sur l'envoi de données à un service tiers reste à rédiger.
- Les guides réels pour les défis de l'événement ne sont pas fournis.

## Architecture

- `docs/` : documentation de l'architecture et comparatif des options de modèles de langage.
- `guides/` : répertoire destiné aux guides des défis en YAML, contenant le modèle guides/_template.yaml.
- `support/` : logique du bot Discord, gestion du cycle des tickets, triage, stockage et garde de sortie.
- `support/drafter/` : rédaction des brouillons, chaîne d'appels aux modèles Gemini et schémas des messages.
- `tests/` : tests unitaires et fichiers de test factices.
- `tools/` : scripts hors ligne pour générer les guides et vérifier l'absence de fuite de secret.

## Prérequis

- Python.
- Dépendances Python listées dans requirements.txt : discord.py, pyyaml et requests.
- Jeton d'accès de bot Discord avec les permissions adaptées sur les salons cibles.
- Clé d'API Google Gemini si la variable LLM_ENABLED vaut 1.

## Installation et exécution

Copier le fichier .env.example vers .env et renseigner les paramètres.

Commandes d'exécution des outils documentées dans les fichiers :
- Génération des squelettes de guides : `python -m tools.make_guides --ctf-repo <dossier-du-ctf>`
- Contrôle de fuite : `python -m tools.check_leaks [--ctf-repo CHEMIN] [chemins...]`

Installation et démarrage du bot :

```sh
python -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/python -m support.bot
```

Le mode simulation reste actif tant que `SUPPORT_DRY_RUN` vaut 1.

## Tests

Les tests unitaires s'appuient sur le module standard unittest.
Lancer toute la suite :

```sh
venv/bin/python -m unittest discover -s tests
```

## Choix techniques

- Validation humaine systématique avant publication :
  - Raison : Éviter la publication de messages erronés ou déplacés dans le salon public.
  - Compromis : Un organisateur doit relire chaque réponse.

- Chaîne de repli de modèles Gemini :
  - Raison : Essayer plusieurs modèles à la suite avec bascule immédiate si le délai de lecture dépasse 8 secondes ou en cas d'erreur.
  - Compromis : Données transmises à un tiers externe et quotas gratuits limités.

- Triage déterministe des défis sans modèle :
  - Raison : Associer un ticket à son guide de façon directe et sans coût d'API.
  - Compromis : En l'absence de correspondance sur le titre ou le dossier, le modèle reçoit uniquement les titres et produit un brouillon généraliste.

- Format restreint des réponses et des guides :
  - Raison : Réponses publiques limitées à 180 caractères et guides bornés à 6 étapes de 200 caractères maximum par champ pour ne pas dévoiler la solution finale.
  - Compromis : Le modèle doit cibler précisément l'étape du joueur pour rester pertinent.

- Défense en profondeur contre la divulgation de secrets :
  - Raison : Le modèle ne peut divulguer que les données incluses dans son contexte d'appel.
  - Compromis : La garde textuelle ne bloque pas tous les encodages inhabituels, ce qui impose l'absence de flag dans les guides.

- Mode simulation actif par défaut :
  - Raison : La variable SUPPORT_DRY_RUN est fixée à 1 par défaut pour éviter tout envoi direct lors des essais.
  - Compromis : L'organisateur doit modifier cette variable à 0 pour permettre l'envoi réel des messages.

## Limites

- Le modèle de langage peut produire des affirmations fausses ; seule la relecture humaine protège le salon public.
- Si un guide ne contient pas d'étape rédigée, le modèle ne dispose que du titre et de l'énoncé du défi.
- Une réponse envoyée en message privé pour un ticket absent de tickets.json est rejetée.
- La génération des brouillons dépend du fonctionnement du service Google Gemini.
- L'outil tools/check_leaks.py compare les guides uniquement aux secrets connus du dépôt CTF.
- Les guides réels ne sont pas fournis dans ce dépôt.

## Licence

MIT. Voir [LICENSE](LICENSE).
