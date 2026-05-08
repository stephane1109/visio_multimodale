# Outil de collecte d'entretiens

Application locale de collecte d'entretiens avec :

- enregistrement vidéo et audio depuis le navigateur de l'enquêté
- sauvegarde directe sur l'ordinateur de l'enquêteur
- export automatique en `video.mp4`
- export automatique en `audio.mp3`
- export automatique en `audio.wav`
- génération de `transcript_participant`
- génération de `transcript_enqueteur`
- génération de `transcript_dialogue`
- extraction audio avec `ffmpeg`
- transcription avec `faster-whisper`
- mode `LiveKit Components` pour un entretien en split-screen
- tableau de bord enquêteur dans le navigateur
- onglet `Ouvrir un corpus` pour importer un audio et modifier la transcription ##### en cours de dev

Le mode d'emploi détaillé est disponible dans [AIDE.md](/Users/stephanemeurisse/Documents/Recherche/visio_multimodale/AIDE.md).

## Librairies à installer

Le fichier `requirements.txt` ne se lance pas directement.
Il sert à installer les dépendances Python avec `pip install -r requirements.txt`.

### Mac

Installer d'abord :

```bash
brew install ffmpeg
brew install ngrok
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Windows

Installer d'abord :

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Puis installer aussi :

- `ffmpeg`
- `ngrok`

## Lancer l'application

Mac :

- double-cliquez sur `Lancer.command`

Windows :

```bat
start_windows.bat
```
