#!/usr/bin/env python3
"""Local interview collection tool.

This server runs on the investigator's computer and exposes a minimal web app
for remote participants. Recordings are uploaded directly to the local machine,
stored in session folders, then optionally processed with ffmpeg and
faster-whisper.
"""

from __future__ import annotations

import argparse
import base64
from difflib import SequenceMatcher
from email.parser import BytesParser
from email.policy import default as email_policy_default
import hashlib
import hmac
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen
from urllib.error import URLError


ROOT_DIR = Path(__file__).resolve().parent.parent
APP_DIR = ROOT_DIR / "app"
STATIC_DIR = APP_DIR / "static"
DATA_DIR = ROOT_DIR / "data"
SESSIONS_DIR = DATA_DIR / "sessions"
CORPORA_DIR = DATA_DIR / "corpora"
TMP_DIR = ROOT_DIR / "tmp"
CONFIG_PATH = ROOT_DIR / "config.json"
NGROK_PID_PATH = TMP_DIR / "ngrok.pid"
NGROK_LOG_PATH = TMP_DIR / "ngrok.log"

DEFAULT_CONFIG = {
    "title": "Entretien à distance",
    "max_upload_size_mb": 2048,
    "ffmpeg_binary": "ffmpeg",
    "enable_mp4_export": True,
    "enable_mp3_export": True,
    "enable_audio_extraction": True,
    "enable_transcription": True,
    "whisper_model_size": "small",
    "whisper_language": "fr",
    "whisper_device": "cpu",
    "whisper_compute_type": "int8",
    "session_prefix": "entretien",
    "livekit_url": "",
    "livekit_api_key": "",
    "livekit_api_secret": "",
}
WHISPER_ALLOWED_MODEL_SIZES = {"small", "medium"}
LIVEKIT_ALLOWED_ROLES = {"participant", "investigator"}

MODEL_LOCK = threading.Lock()
WHISPER_MODEL: Any | None = None
WHISPER_MODEL_SIGNATURE: tuple[str, str, str] | None = None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def ensure_runtime_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    CORPORA_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)


def normalize_whisper_model_size(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in WHISPER_ALLOWED_MODEL_SIZES:
        return normalized
    return "small"


def load_config() -> dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        config.update(loaded)
    config["whisper_model_size"] = normalize_whisper_model_size(config.get("whisper_model_size"))
    return config


def save_config(config: dict[str, Any]) -> None:
    normalized = dict(config)
    normalized["whisper_model_size"] = normalize_whisper_model_size(normalized.get("whisper_model_size"))
    save_json_file(CONFIG_PATH, normalized)


def normalize_livekit_role(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in LIVEKIT_ALLOWED_ROLES:
        return normalized
    return "participant"


def get_livekit_settings(config: dict[str, Any]) -> dict[str, str]:
    return {
        "livekit_url": str(config.get("livekit_url", "")).strip(),
        "livekit_api_key": str(config.get("livekit_api_key", "")).strip(),
        "livekit_api_secret": str(config.get("livekit_api_secret", "")).strip(),
    }


def has_livekit_config(config: dict[str, Any]) -> bool:
    settings = get_livekit_settings(config)
    return bool(settings["livekit_url"] and settings["livekit_api_key"] and settings["livekit_api_secret"])


def is_valid_public_base_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except Exception:
        return False

    if parsed.scheme != "https":
        return False

    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        return False
    if hostname in {"localhost", "::1"}:
        return False
    if hostname.startswith("127."):
        return False
    return True


def read_pid_file(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def is_process_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def tail_text_file(path: Path, max_chars: int = 1600) -> str:
    if not path.exists():
        return ""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    return content[-max_chars:]


def get_ngrok_tunnels_payload() -> dict[str, Any] | None:
    try:
        with urlopen("http://127.0.0.1:4040/api/tunnels", timeout=1.2) as response:
            return json.loads(response.read().decode("utf-8"))
    except (URLError, json.JSONDecodeError, TimeoutError, OSError):
        return None


def find_ngrok_public_url(port: int = 8000) -> str:
    payload = get_ngrok_tunnels_payload() or {}
    tunnels = payload.get("tunnels", [])
    expected_port = str(port)
    for tunnel in tunnels:
        public_url = str(tunnel.get("public_url", "")).strip()
        config = tunnel.get("config", {}) if isinstance(tunnel, dict) else {}
        addr = str(config.get("addr", "")).strip()
        if public_url.startswith("https://") and expected_port in addr:
            return public_url
    return ""


def get_ngrok_status(port: int = 8000) -> dict[str, Any]:
    ngrok_binary = shutil.which("ngrok")
    pid = read_pid_file(NGROK_PID_PATH)
    running = is_process_alive(pid)
    public_url = find_ngrok_public_url(port) if ngrok_binary else ""
    if public_url:
        running = True
    return {
        "available": bool(ngrok_binary),
        "running": running,
        "pid": pid,
        "public_url": public_url,
        "log_tail": tail_text_file(NGROK_LOG_PATH),
    }


def format_ngrok_error_message(log_tail: str) -> str:
    normalized = (log_tail or "").lower()
    if "err_ngrok_4018" in normalized or "authentication failed" in normalized:
        return (
            "ngrok n'est pas encore activé sur ce poste. "
            "Vérifiez d'abord votre compte ngrok dans le navigateur, puis collez ici l'authtoken une seule fois."
        )
    return log_tail or "ngrok n'a pas pu démarrer correctement."


def save_ngrok_authtoken(token: str) -> tuple[bool, str]:
    ngrok_binary = shutil.which("ngrok")
    if not ngrok_binary:
        return False, "ngrok n'est pas installé sur cette machine."
    cleaned = str(token or "").strip()
    if not cleaned:
        return False, "Authtoken ngrok manquant."

    completed = subprocess.run(
        [ngrok_binary, "config", "add-authtoken", cleaned],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(ROOT_DIR),
    )
    if completed.returncode != 0:
        error = completed.stderr.strip() or completed.stdout.strip() or "Impossible d'enregistrer l'authtoken ngrok."
        return False, error
    return True, "Authtoken ngrok enregistré."


def start_ngrok_tunnel(port: int = 8000) -> tuple[bool, str, str]:
    ensure_runtime_dirs()
    ngrok_binary = shutil.which("ngrok")
    if not ngrok_binary:
        return False, "", "ngrok n'est pas installé sur cette machine."

    current_url = find_ngrok_public_url(port)
    if current_url:
        return True, current_url, "ngrok est déjà actif."

    existing_pid = read_pid_file(NGROK_PID_PATH)
    if existing_pid and not is_process_alive(existing_pid):
        try:
            NGROK_PID_PATH.unlink()
        except OSError:
            pass

    NGROK_LOG_PATH.write_text("", encoding="utf-8")
    with NGROK_LOG_PATH.open("a", encoding="utf-8") as log_handle:
        process = subprocess.Popen(  # noqa: S603
            [ngrok_binary, "http", str(port)],
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            cwd=str(ROOT_DIR),
        )
    NGROK_PID_PATH.write_text(str(process.pid), encoding="utf-8")

    for _ in range(20):
        if process.poll() is not None:
            break
        public_url = find_ngrok_public_url(port)
        if public_url:
            return True, public_url, "Accès distant ngrok démarré."
        time.sleep(0.5)

    log_tail = tail_text_file(NGROK_LOG_PATH)
    return False, "", format_ngrok_error_message(log_tail)


def stop_ngrok_tunnel() -> tuple[bool, str]:
    pid = read_pid_file(NGROK_PID_PATH)
    current_url = find_ngrok_public_url(8000)
    if current_url and not pid:
        return False, "ngrok semble actif mais n'a pas été démarré par l'application. Arrêtez-le manuellement dans votre terminal."
    if pid and is_process_alive(pid):
        try:
            os.kill(pid, 15)
        except OSError:
            return False, "Impossible d'arrêter le processus ngrok."
        time.sleep(0.4)
        if is_process_alive(pid):
            try:
                os.kill(pid, 9)
            except OSError:
                return False, "Impossible d'arrêter le processus ngrok."
    if NGROK_PID_PATH.exists():
        try:
            NGROK_PID_PATH.unlink()
        except OSError:
            pass
    return True, "Accès distant ngrok arrêté."


def build_livekit_identity(session_id: str, role: str) -> str:
    normalized_role = normalize_livekit_role(role)
    return f"{normalized_role}_{session_id}"


def livekit_role_dir(session_dir: Path, role: str) -> Path:
    normalized_role = normalize_livekit_role(role)
    role_dir = session_dir / normalized_role
    role_dir.mkdir(parents=True, exist_ok=True)
    return role_dir


def livekit_consent_path(session_id: str) -> Path:
    return livekit_role_dir(SESSIONS_DIR / session_id, "participant") / "consent.json"


def load_livekit_consent(session_id: str) -> dict[str, Any]:
    return load_json_file(
        livekit_consent_path(session_id),
        {
            "consent_checked": False,
            "consent_timestamp": None,
        },
    )


def normalize_role_label(value: Any, fallback: str) -> str:
    normalized = str(value or "").strip()
    return normalized or fallback


def get_session_speaker_labels(session_root: Path) -> dict[str, str]:
    metadata = load_json_file(session_root / "metadata.json", {})
    labels = metadata.get("speaker_labels", {}) if isinstance(metadata, dict) else {}
    participant_label = normalize_role_label(labels.get("participant"), "Participant") if isinstance(labels, dict) else "Participant"
    investigator_label = normalize_role_label(labels.get("investigator"), "Enquêteur") if isinstance(labels, dict) else "Enquêteur"
    return {
        "participant": participant_label,
        "investigator": investigator_label,
    }


def create_livekit_session(
    participant_code: str,
    notes: str,
    participant_role_label: str,
    investigator_role_label: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    session_id = generate_session_id(participant_code, config)
    session_dir = SESSIONS_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=False)

    speaker_labels = {
        "participant": normalize_role_label(participant_role_label, "Participant"),
        "investigator": normalize_role_label(investigator_role_label, "Enquêteur"),
    }

    metadata = {
        "session_id": session_id,
        "participant_code": participant_code,
        "created_at": iso_now(),
        "mode": "livekit",
        "room_name": session_id,
        "notes": notes,
        "speaker_labels": speaker_labels,
    }
    livekit_session = {
        "session_id": session_id,
        "room_name": session_id,
        "participant_code": participant_code,
        "created_at": iso_now(),
        "speaker_labels": speaker_labels,
        "roles": {
            "participant": {"identity": build_livekit_identity(session_id, "participant")},
            "investigator": {"identity": build_livekit_identity(session_id, "investigator")},
        },
        "notes": notes,
    }

    save_json_file(session_dir / "metadata.json", metadata)
    save_json_file(session_dir / "livekit_session.json", livekit_session)
    return livekit_session


def load_livekit_session(session_id: str) -> dict[str, Any] | None:
    session_dir = SESSIONS_DIR / session_id
    data_path = session_dir / "livekit_session.json"
    if not data_path.exists():
        return None
    return load_json_file(data_path, None)


def base64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def encode_hs256_jwt(payload: dict[str, Any], secret: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = ".".join(
        [
            base64url_encode(json.dumps(header, separators=(",", ":"), ensure_ascii=False).encode("utf-8")),
            base64url_encode(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")),
        ]
    )
    signature = hmac.new(secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{base64url_encode(signature)}"


def create_livekit_token(config: dict[str, Any], session_data: dict[str, Any], role: str) -> str:
    settings = get_livekit_settings(config)
    normalized_role = normalize_livekit_role(role)
    session_id = str(session_data["session_id"])
    room_name = str(session_data["room_name"])
    identity = build_livekit_identity(session_id, normalized_role)
    now = int(time.time())
    payload = {
        "iss": settings["livekit_api_key"],
        "sub": identity,
        "nbf": now - 10,
        "exp": now + 60 * 60 * 6,
        "name": identity,
        "metadata": json.dumps({"role": normalized_role, "session_id": session_id}, ensure_ascii=False),
        "video": {
            "roomJoin": True,
            "room": room_name,
            "canPublish": True,
            "canSubscribe": True,
            "canPublishData": True,
            "canPublishSources": ["camera", "microphone"],
        },
    }
    return encode_hs256_jwt(payload, settings["livekit_api_secret"])


def load_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
    tmp_path.replace(path)


def get_ffmpeg_binary(config: dict[str, Any]) -> str | None:
    binary = config.get("ffmpeg_binary", "ffmpeg")
    if shutil.which(binary):
        return binary
    return None


def infer_extension(content_type: str) -> str:
    normalized = (content_type or "").lower()
    if "mp4" in normalized:
        return ".mp4"
    if "mpeg" in normalized or "mp3" in normalized:
        return ".mp3"
    if "wav" in normalized:
        return ".wav"
    if "m4a" in normalized or "aac" in normalized:
        return ".m4a"
    if "ogg" in normalized:
        return ".ogg"
    if "quicktime" in normalized:
        return ".mov"
    return ".webm"


def slugify(text: str) -> str:
    safe = []
    for char in text.strip():
        if char.isalnum():
            safe.append(char.lower())
        elif char in {"-", "_"}:
            safe.append(char)
        else:
            safe.append("-")
    collapsed = "".join(safe).strip("-")
    return collapsed or "participant"


def generate_session_id(participant_code: str, config: dict[str, Any]) -> str:
    date_part = datetime.now().strftime("%Y-%m-%d")
    prefix = config.get("session_prefix", "entretien")
    base_name = f"{prefix}_{date_part}_{slugify(participant_code)}"

    candidate = base_name
    suffix = 2
    while (SESSIONS_DIR / candidate).exists():
        candidate = f"{base_name}_{suffix}"
        suffix += 1
    return candidate


def generate_corpus_id(corpus_title: str) -> str:
    date_part = datetime.now().strftime("%Y-%m-%d")
    base_name = f"corpus_{date_part}_{slugify(corpus_title)}"

    candidate = base_name
    suffix = 2
    while (CORPORA_DIR / candidate).exists():
        candidate = f"{base_name}_{suffix}"
        suffix += 1
    return candidate


def append_log(session_dir: Path, message: str) -> None:
    log_path = session_dir / "logs.txt"
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{iso_now()}] {message}\n")


def update_processing_status(session_dir: Path, status: str, details: dict[str, Any]) -> None:
    payload = {"status": status, "updated_at": iso_now(), **details}
    save_json_file(session_dir / "processing.json", payload)
    append_log(session_dir, f"processing={status} details={details}")


def extract_wav_from_media(media_path: Path, output_path: Path, config: dict[str, Any]) -> tuple[bool, str, Path | None]:
    ffmpeg_binary = get_ffmpeg_binary(config)
    if not ffmpeg_binary:
        return False, "ffmpeg introuvable sur cette machine.", None

    command = [
        ffmpeg_binary,
        "-y",
        "-i",
        str(media_path),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        str(output_path),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        error = completed.stderr.strip() or completed.stdout.strip() or "Erreur inconnue ffmpeg."
        return False, error, None
    return True, "audio extrait", output_path


def extract_audio(video_path: Path, session_dir: Path, config: dict[str, Any]) -> tuple[bool, str, Path | None]:
    audio_path = session_dir / "audio.wav"
    return extract_wav_from_media(video_path, audio_path, config)


def export_audio_mp3(audio_path: Path, session_dir: Path, config: dict[str, Any]) -> tuple[bool, str, Path | None]:
    mp3_path = session_dir / "audio.mp3"
    ffmpeg_binary = get_ffmpeg_binary(config)
    if not ffmpeg_binary:
        return False, "ffmpeg introuvable sur cette machine.", None

    command = [
        ffmpeg_binary,
        "-y",
        "-i",
        str(audio_path),
        "-codec:a",
        "libmp3lame",
        "-q:a",
        "2",
        str(mp3_path),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        error = completed.stderr.strip() or completed.stdout.strip() or "Erreur inconnue ffmpeg."
        return False, error, None
    return True, "audio mp3 exporté", mp3_path


def export_video_mp4(video_path: Path, session_dir: Path, config: dict[str, Any]) -> tuple[bool, str, Path | None]:
    mp4_path = session_dir / "video.mp4"
    if video_path.resolve() == mp4_path.resolve():
        return True, "vidéo mp4 déjà disponible", mp4_path

    ffmpeg_binary = get_ffmpeg_binary(config)
    if not ffmpeg_binary:
        return False, "ffmpeg introuvable sur cette machine.", None

    if video_path.suffix.lower() == ".mp4":
        shutil.copy2(video_path, mp4_path)
        return True, "vidéo mp4 copiée", mp4_path

    command = [
        ffmpeg_binary,
        "-y",
        "-i",
        str(video_path),
        "-vf",
        "fps=30,scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(mp4_path),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        if mp4_path.exists():
            mp4_path.unlink()
        error = completed.stderr.strip() or completed.stdout.strip() or "Erreur inconnue ffmpeg."
        return False, error, None
    return True, "vidéo mp4 exportée", mp4_path


def get_whisper_model(config: dict[str, Any]) -> Any:
    global WHISPER_MODEL, WHISPER_MODEL_SIGNATURE
    model_size = normalize_whisper_model_size(config.get("whisper_model_size"))
    signature = (
        model_size,
        str(config.get("whisper_device", "cpu")),
        str(config.get("whisper_compute_type", "int8")),
    )
    if WHISPER_MODEL is not None and WHISPER_MODEL_SIGNATURE == signature:
        return WHISPER_MODEL

    with MODEL_LOCK:
        if WHISPER_MODEL is not None and WHISPER_MODEL_SIGNATURE == signature:
            return WHISPER_MODEL
        from faster_whisper import WhisperModel  # type: ignore

        WHISPER_MODEL = WhisperModel(
            model_size,
            device=signature[1],
            compute_type=signature[2],
        )
        WHISPER_MODEL_SIGNATURE = signature
        return WHISPER_MODEL


def format_segments_for_json(segments: list[Any]) -> list[dict[str, Any]]:
    formatted = []
    for segment in segments:
        formatted.append(
            {
                "id": getattr(segment, "id", None),
                "start": getattr(segment, "start", None),
                "end": getattr(segment, "end", None),
                "text": getattr(segment, "text", "").strip(),
            }
        )
    return formatted


def format_segments_as_text(segments: list[dict[str, Any]]) -> str:
    lines = []
    for segment in segments:
        start = segment.get("start")
        end = segment.get("end")
        text = segment.get("text", "").strip()
        speaker = str(segment.get("speaker", "")).strip()
        if speaker:
            lines.append(f"[{start:.2f}s - {end:.2f}s] {speaker} :\n{text}")
        else:
            lines.append(f"[{start:.2f}s - {end:.2f}s] {text}")
    return "\n\n".join(lines).strip() + "\n"


def transcribe_audio(audio_path: Path, session_dir: Path, config: dict[str, Any], transcript_stem: str = "transcript") -> tuple[bool, str]:
    try:
        model = get_whisper_model(config)
    except ImportError:
        return False, "Le paquet faster-whisper n'est pas installé."
    except Exception as exc:  # pragma: no cover - safety net for local env issues
        return False, f"Impossible d'initialiser Whisper: {exc}"

    try:
        requested_language = str(config.get("whisper_language", "fr")).strip().lower() or "fr"
        segments_iterable, info = model.transcribe(
            str(audio_path),
            vad_filter=True,
            language=requested_language,
            task="transcribe",
        )
        segments = list(segments_iterable)
        formatted_segments = format_segments_for_json(segments)
        transcript_text = format_segments_as_text(formatted_segments)
        full_text = " ".join(segment.get("text", "").strip() for segment in formatted_segments if segment.get("text")).strip()
        save_json_file(
            session_dir / f"{transcript_stem}.json",
            {
                "requested_language": requested_language,
                "language": getattr(info, "language", None),
                "duration": getattr(info, "duration", None),
                "duration_after_vad": getattr(info, "duration_after_vad", None),
                "segments_count": len(formatted_segments),
                "full_text": full_text,
                "segments": formatted_segments,
            },
        )
        (session_dir / f"{transcript_stem}.txt").write_text(transcript_text, encoding="utf-8")
        return True, "Transcription terminée."
    except Exception as exc:  # pragma: no cover - depends on local model/runtime
        return False, f"Erreur de transcription: {exc}"


def normalize_similarity_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii")
    cleaned = []
    previous_space = False
    for char in normalized.lower():
        if char.isalnum():
            cleaned.append(char)
            previous_space = False
        elif not previous_space:
            cleaned.append(" ")
            previous_space = True
    return "".join(cleaned).strip()


def build_role_segments(payload: dict[str, Any], role: str, speaker_labels: dict[str, str]) -> list[dict[str, Any]]:
    role_segments: list[dict[str, Any]] = []
    for segment in payload.get("segments", []):
        start = segment.get("start")
        end = segment.get("end")
        text = str(segment.get("text", "")).strip()
        if start is None or end is None or not text:
            continue
        role_segments.append(
            {
                "role": role,
                "speaker": speaker_labels.get(role, role),
                "start": float(start),
                "end": float(end),
                "text": text,
            }
        )
    return role_segments


def load_role_transcript_source(role_dir: Path) -> dict[str, Any]:
    raw_path = role_dir / "transcript_raw.json"
    if raw_path.exists():
        return load_json_file(raw_path, {})
    return load_json_file(role_dir / "transcript.json", {})


def segment_overlap_seconds(first: dict[str, Any], second: dict[str, Any]) -> float:
    return max(0.0, min(float(first["end"]), float(second["end"])) - max(float(first["start"]), float(second["start"])))


def is_cross_talk_duplicate(
    investigator_segment: dict[str, Any],
    participant_segment: dict[str, Any],
) -> bool:
    overlap = segment_overlap_seconds(investigator_segment, participant_segment)
    if overlap <= 0:
        return False

    inv_duration = max(0.01, float(investigator_segment["end"]) - float(investigator_segment["start"]))
    part_duration = max(0.01, float(participant_segment["end"]) - float(participant_segment["start"]))
    overlap_ratio = overlap / min(inv_duration, part_duration)
    if overlap < 0.8 and overlap_ratio < 0.5:
        return False

    inv_text = normalize_similarity_text(investigator_segment.get("text", ""))
    part_text = normalize_similarity_text(participant_segment.get("text", ""))
    if not inv_text or not part_text:
        return False

    similarity = SequenceMatcher(None, inv_text, part_text).ratio()
    contains = inv_text in part_text or part_text in inv_text
    return (similarity >= 0.72 and overlap_ratio >= 0.55) or (contains and overlap_ratio >= 0.45)


def filter_investigator_cross_talk(
    participant_segments: list[dict[str, Any]],
    investigator_segments: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    filtered: list[dict[str, Any]] = []
    removed = 0
    for investigator_segment in investigator_segments:
        duplicate = any(
            is_cross_talk_duplicate(investigator_segment, participant_segment)
            for participant_segment in participant_segments
        )
        if duplicate:
            removed += 1
            continue
        filtered.append(investigator_segment)
    return filtered, removed


def export_role_transcript_aliases(session_root: Path) -> None:
    speaker_labels = get_session_speaker_labels(session_root)
    aliases = [
        ("participant", "transcript_participant"),
        ("investigator", "transcript_enqueteur"),
    ]

    role_payloads: dict[str, dict[str, Any]] = {}
    role_segments_map: dict[str, list[dict[str, Any]]] = {}
    for role, _alias_stem in aliases:
        role_dir = session_root / role
        payload = load_role_transcript_source(role_dir)
        if not payload:
            continue
        role_payloads[role] = payload
        raw_segments = build_role_segments(payload, role, speaker_labels)
        role_segments_map[role] = raw_segments
        save_json_file(role_dir / "transcript_raw.json", payload)
        (role_dir / "transcript_raw.txt").write_text(
            format_segments_as_text(raw_segments),
            encoding="utf-8",
        )

    filtered_investigator_segments, removed_segments = filter_investigator_cross_talk(
        role_segments_map.get("participant", []),
        role_segments_map.get("investigator", []),
    )
    if removed_segments:
        append_log(session_root, f"Filtrage anti-repisse enquêteur: {removed_segments} segment(s) supprimé(s).")
    if "investigator" in role_segments_map:
        role_segments_map["investigator"] = filtered_investigator_segments
        filtered_payload = dict(role_payloads.get("investigator", {}))
        filtered_payload["role"] = "investigator"
        filtered_payload["speaker"] = speaker_labels.get("investigator", "investigator")
        filtered_payload["speaker_labels"] = speaker_labels
        filtered_payload["segments"] = filtered_investigator_segments
        filtered_payload["segments_count"] = len(filtered_investigator_segments)
        filtered_payload["cross_talk_removed_segments"] = removed_segments
        filtered_payload["full_text"] = " ".join(segment["text"] for segment in filtered_investigator_segments).strip()
        save_json_file(session_root / "investigator" / "transcript_filtre.json", filtered_payload)
        (session_root / "investigator" / "transcript_filtre.txt").write_text(
            format_segments_as_text(filtered_investigator_segments),
            encoding="utf-8",
        )

    for role, alias_stem in aliases:
        role_dir = session_root / role
        payload = role_payloads.get(role, {})
        if not payload:
            continue

        role_segments = role_segments_map.get(role, [])

        role_payload = dict(payload)
        role_payload["role"] = role
        role_payload["speaker"] = speaker_labels.get(role, role)
        role_payload["segments"] = role_segments
        role_payload["speaker_labels"] = speaker_labels
        role_payload["segments_count"] = len(role_segments)
        role_payload["full_text"] = " ".join(segment["text"] for segment in role_segments).strip()
        if role == "investigator":
            role_payload["cross_talk_removed_segments"] = removed_segments
        save_json_file(role_dir / "transcript.json", role_payload)
        (role_dir / "transcript.txt").write_text(
            format_segments_as_text(role_segments),
            encoding="utf-8",
        )
        save_json_file(session_root / f"{alias_stem}.json", role_payload)
        (session_root / f"{alias_stem}.txt").write_text(
            format_segments_as_text(role_segments),
            encoding="utf-8",
        )


def build_dialogue_transcript(session_root: Path) -> tuple[bool, str]:
    speaker_labels = get_session_speaker_labels(session_root)
    speaker_sources = [
        ("participant", load_json_file(session_root / "participant" / "transcript.json", {})),
        ("investigator", load_json_file(session_root / "investigator" / "transcript.json", {})),
    ]

    dialogue_segments: list[dict[str, Any]] = []
    for role, payload in speaker_sources:
        for segment in payload.get("segments", []):
            start = segment.get("start")
            end = segment.get("end")
            text = str(segment.get("text", "")).strip()
            if start is None or end is None or not text:
                continue
            dialogue_segments.append(
                {
                    "role": role,
                    "speaker": speaker_labels.get(role, role),
                    "start": float(start),
                    "end": float(end),
                    "text": text,
                }
            )

    dialogue_segments.sort(key=lambda segment: (segment["start"], segment["end"], segment["speaker"]))

    if not dialogue_segments:
        return False, "Transcript dialogue indisponible pour le moment."

    full_text = " ".join(segment["text"] for segment in dialogue_segments).strip()
    dialogue_payload = {
        "session_id": session_root.name,
        "speaker_labels": speaker_labels,
        "segments_count": len(dialogue_segments),
        "full_text": full_text,
        "segments": dialogue_segments,
    }
    save_json_file(session_root / "transcript_dialogue.json", dialogue_payload)
    (session_root / "transcript_dialogue.txt").write_text(
        format_segments_as_text(dialogue_segments),
        encoding="utf-8",
    )

    # Backward-compatible alias kept for existing references in the project.
    save_json_file(session_root / "segments_diarises.json", dialogue_payload)
    (session_root / "segments_diarises.txt").write_text(
        format_segments_as_text(dialogue_segments),
        encoding="utf-8",
    )
    return True, "Transcript dialogue généré."


def refresh_session_outputs(session_dir: Path) -> None:
    session_root = session_dir.parent if session_dir.name in LIVEKIT_ALLOWED_ROLES else session_dir
    export_role_transcript_aliases(session_root)
    dialogue_ok, dialogue_message = build_dialogue_transcript(session_root)
    append_log(session_root, dialogue_message)
    if dialogue_ok:
        save_json_file(
            session_root / "processing.json",
            {
                "status": "completed",
                "updated_at": iso_now(),
                "step": "transcript_dialogue",
                "dialogue_transcript": {"ok": True, "message": dialogue_message},
            },
        )


def process_session_async(session_dir: Path, config: dict[str, Any]) -> None:
    video_files = list(session_dir.glob("raw_video.*"))
    if not video_files:
        update_processing_status(
            session_dir,
            "failed",
            {"error": "Fichier vidéo source introuvable."},
        )
        return

    video_path = video_files[0]
    update_processing_status(session_dir, "running", {"step": "initialisation"})

    mp4_ok = False
    mp4_message = "export mp4 désactivé"
    mp4_path = None
    if config.get("enable_mp4_export", True):
        update_processing_status(session_dir, "running", {"step": "video_mp4_export"})
        mp4_ok, mp4_message, mp4_path = export_video_mp4(video_path, session_dir, config)
        append_log(session_dir, mp4_message)
    else:
        append_log(session_dir, "Export mp4 désactivé dans la configuration.")

    audio_ok = False
    audio_message = "extraction audio désactivée"
    audio_path = None

    if config.get("enable_audio_extraction", True):
        update_processing_status(session_dir, "running", {"step": "audio_extraction"})
        audio_ok, audio_message, audio_path = extract_audio(video_path, session_dir, config)
        append_log(session_dir, audio_message)
    else:
        append_log(session_dir, "Extraction audio désactivée dans la configuration.")

    mp3_ok = False
    mp3_message = "export mp3 désactivé"
    mp3_path = None
    if config.get("enable_mp3_export", True) and audio_ok and audio_path is not None:
        update_processing_status(session_dir, "running", {"step": "audio_mp3_export"})
        mp3_ok, mp3_message, mp3_path = export_audio_mp3(audio_path, session_dir, config)
        append_log(session_dir, mp3_message)
    elif not config.get("enable_mp3_export", True):
        append_log(session_dir, "Export mp3 désactivé dans la configuration.")
    elif not audio_ok:
        append_log(session_dir, "Export mp3 ignoré car l'audio n'a pas pu être extrait.")

    transcription_ok = False
    transcription_message = "transcription ignorée"
    if config.get("enable_transcription", True) and audio_ok and audio_path is not None:
        update_processing_status(session_dir, "running", {"step": "transcription"})
        transcription_ok, transcription_message = transcribe_audio(audio_path, session_dir, config)
        append_log(session_dir, transcription_message)
    elif not config.get("enable_transcription", True):
        append_log(session_dir, "Transcription désactivée dans la configuration.")
    elif not audio_ok:
        append_log(session_dir, "Transcription ignorée car l'audio n'a pas pu être extrait.")

    update_processing_status(
        session_dir,
        "completed",
        {
            "video_mp4_export": {"ok": mp4_ok, "message": mp4_message, "path": str(mp4_path) if mp4_path else None},
            "audio_extraction": {"ok": audio_ok, "message": audio_message},
            "audio_mp3_export": {"ok": mp3_ok, "message": mp3_message, "path": str(mp3_path) if mp3_path else None},
            "transcription": {"ok": transcription_ok, "message": transcription_message},
        },
    )
    refresh_session_outputs(session_dir)


def process_audio_only_session_async(session_dir: Path, raw_audio_path: Path, config: dict[str, Any]) -> None:
    update_processing_status(session_dir, "running", {"step": "initialisation"})

    wav_ok, wav_message, wav_path = extract_wav_from_media(raw_audio_path, session_dir / "audio.wav", config)
    append_log(session_dir, wav_message)

    mp3_ok = False
    mp3_message = "export mp3 désactivé"
    mp3_path = None
    if config.get("enable_mp3_export", True) and wav_ok and wav_path is not None:
        update_processing_status(session_dir, "running", {"step": "audio_mp3_export"})
        mp3_ok, mp3_message, mp3_path = export_audio_mp3(wav_path, session_dir, config)
        append_log(session_dir, mp3_message)
    elif not config.get("enable_mp3_export", True):
        append_log(session_dir, "Export mp3 désactivé dans la configuration.")
    elif not wav_ok:
        append_log(session_dir, "Export mp3 ignoré car l'audio n'a pas pu être extrait.")

    transcription_ok = False
    transcription_message = "transcription ignorée"
    if config.get("enable_transcription", True) and wav_ok and wav_path is not None:
        update_processing_status(session_dir, "running", {"step": "transcription"})
        transcription_ok, transcription_message = transcribe_audio(wav_path, session_dir, config)
        append_log(session_dir, transcription_message)
    elif not config.get("enable_transcription", True):
        append_log(session_dir, "Transcription désactivée dans la configuration.")
    elif not wav_ok:
        append_log(session_dir, "Transcription ignorée car l'audio n'a pas pu être extrait.")

    update_processing_status(
        session_dir,
        "completed",
        {
            "audio_extraction": {"ok": wav_ok, "message": wav_message, "path": str(wav_path) if wav_path else None},
            "audio_mp3_export": {"ok": mp3_ok, "message": mp3_message, "path": str(mp3_path) if mp3_path else None},
            "transcription": {"ok": transcription_ok, "message": transcription_message},
        },
    )
    refresh_session_outputs(session_dir)


def list_session_files(session_dir: Path) -> list[str]:
    files: list[str] = []
    for item in session_dir.rglob("*"):
        if item.is_file():
            files.append(item.relative_to(session_dir).as_posix())
    return sorted(files)


def session_has_recording_artifacts(files: list[str]) -> bool:
    prefixes = ("raw_", "video.", "audio.", "transcript")
    for relative_path in files:
        if Path(relative_path).name.startswith(prefixes):
            return True
    return False


def summarize_livekit_processing(session_dir: Path) -> dict[str, Any]:
    participant_processing = load_json_file(session_dir / "participant" / "processing.json", {})
    investigator_processing = load_json_file(session_dir / "investigator" / "processing.json", {})
    processings = [processing for processing in (participant_processing, investigator_processing) if processing]
    if not processings:
        return {}

    statuses = [processing.get("status", "unknown") for processing in processings]
    if "failed" in statuses:
        status = "failed"
    elif statuses and all(item == "completed" for item in statuses):
        status = "completed"
    elif "running" in statuses:
        status = "running"
    elif "queued" in statuses:
        status = "queued"
    else:
        status = statuses[0]

    steps = [processing.get("step") for processing in processings if processing.get("step")]
    step = " | ".join(steps) if steps else ""
    return {"status": status, "step": step}


def list_sessions(limit: int = 50) -> list[dict[str, Any]]:
    ensure_runtime_dirs()
    sessions: list[dict[str, Any]] = []
    for session_dir in sorted(SESSIONS_DIR.iterdir(), reverse=True):
        if not session_dir.is_dir():
            continue
        metadata = load_json_file(session_dir / "metadata.json", {})
        entries = list_session_files(session_dir)
        if not session_has_recording_artifacts(entries):
            continue

        processing = load_json_file(session_dir / "processing.json", {})
        if metadata.get("mode") == "livekit" and not processing:
            processing = summarize_livekit_processing(session_dir)
        sessions.append(
            {
                "session_id": session_dir.name,
                "participant_code": metadata.get("participant_code"),
                "created_at": metadata.get("created_at"),
                "recording_started_at": metadata.get("recording_started_at"),
                "recording_ended_at": metadata.get("recording_ended_at"),
                "upload_size_bytes": metadata.get("upload_size_bytes"),
                "processing": processing,
                "files": sorted(entries),
            }
        )
        if len(sessions) >= limit:
            break
    return sessions


def list_corpus_files(corpus_dir: Path) -> list[str]:
    files: list[str] = []
    for item in corpus_dir.rglob("*"):
        if item.is_file():
            files.append(item.relative_to(corpus_dir).as_posix())
    return sorted(files)


def find_corpus_audio_file(corpus_dir: Path) -> Path | None:
    for candidate in sorted(corpus_dir.iterdir()):
        if candidate.is_file() and candidate.stem == "audio":
            return candidate
    for candidate in sorted(corpus_dir.iterdir()):
        if candidate.is_file() and candidate.suffix.lower() in {".mp3", ".wav", ".m4a", ".ogg", ".webm"}:
            return candidate
    return None


def extract_transcript_text_from_upload(filename: str, raw_bytes: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".json":
        payload = json.loads(raw_bytes.decode("utf-8"))
        if isinstance(payload, dict):
            full_text = str(payload.get("full_text", "")).strip()
            if full_text:
                return full_text + "\n"
            segments = payload.get("segments", [])
            if isinstance(segments, list):
                formatted_segments = []
                for segment in segments:
                    if not isinstance(segment, dict):
                        continue
                    formatted_segments.append(
                        {
                            "start": float(segment.get("start", 0) or 0),
                            "end": float(segment.get("end", 0) or 0),
                            "text": str(segment.get("text", "")).strip(),
                            "speaker": str(segment.get("speaker", "")).strip(),
                        }
                    )
                if formatted_segments:
                    return format_segments_as_text(formatted_segments)
        return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    return raw_bytes.decode("utf-8", errors="replace")


def import_corpus(
    corpus_title: str,
    audio_file: dict[str, Any],
    transcript_file: dict[str, Any] | None,
) -> dict[str, Any]:
    ensure_runtime_dirs()
    corpus_id = generate_corpus_id(corpus_title)
    corpus_dir = CORPORA_DIR / corpus_id
    corpus_dir.mkdir(parents=True, exist_ok=False)

    audio_filename = Path(str(audio_file.get("filename", "audio"))).name
    audio_suffix = Path(audio_filename).suffix.lower() or infer_extension(str(audio_file.get("content_type", "")))
    audio_path = corpus_dir / f"audio{audio_suffix}"
    audio_path.write_bytes(bytes(audio_file.get("data", b"")))

    transcript_source_name = None
    transcript_text = ""
    if transcript_file and transcript_file.get("data") is not None:
        transcript_source_name = Path(str(transcript_file.get("filename", "transcript.txt"))).name
        source_path = corpus_dir / f"source_{transcript_source_name}"
        source_payload = bytes(transcript_file.get("data", b""))
        source_path.write_bytes(source_payload)
        transcript_text = extract_transcript_text_from_upload(transcript_source_name, source_payload)

    transcript_path = corpus_dir / "transcript.txt"
    transcript_path.write_text(transcript_text, encoding="utf-8")

    metadata = {
        "corpus_id": corpus_id,
        "title": corpus_title,
        "created_at": iso_now(),
        "updated_at": iso_now(),
        "audio_filename": audio_path.name,
        "transcript_filename": transcript_path.name,
        "source_transcript_filename": transcript_source_name,
    }
    save_json_file(corpus_dir / "metadata.json", metadata)
    return metadata


def list_corpora(limit: int = 100) -> list[dict[str, Any]]:
    ensure_runtime_dirs()
    corpora: list[dict[str, Any]] = []
    for corpus_dir in sorted(CORPORA_DIR.iterdir(), reverse=True):
        if not corpus_dir.is_dir():
            continue
        metadata_path = corpus_dir / "metadata.json"
        if not metadata_path.exists():
            continue
        metadata = load_json_file(metadata_path, {})
        audio_path = find_corpus_audio_file(corpus_dir)
        transcript_path = corpus_dir / "transcript.txt"
        corpora.append(
            {
                "corpus_id": corpus_dir.name,
                "title": metadata.get("title") or corpus_dir.name,
                "created_at": metadata.get("created_at"),
                "updated_at": metadata.get("updated_at"),
                "audio_filename": audio_path.name if audio_path else None,
                "transcript_available": transcript_path.exists(),
                "files": list_corpus_files(corpus_dir),
            }
        )
        if len(corpora) >= limit:
            break
    return corpora


def load_corpus_detail(corpus_id: str) -> dict[str, Any] | None:
    corpus_dir = CORPORA_DIR / corpus_id
    metadata_path = corpus_dir / "metadata.json"
    if not metadata_path.exists():
        return None

    metadata = load_json_file(metadata_path, {})
    transcript_path = corpus_dir / "transcript.txt"
    audio_path = find_corpus_audio_file(corpus_dir)
    return {
        "corpus_id": corpus_dir.name,
        "title": metadata.get("title") or corpus_dir.name,
        "created_at": metadata.get("created_at"),
        "updated_at": metadata.get("updated_at"),
        "audio_filename": audio_path.name if audio_path else None,
        "audio_url": f"/api/corpora/audio?corpus_id={corpus_dir.name}" if audio_path else "",
        "transcript_text": transcript_path.read_text(encoding="utf-8") if transcript_path.exists() else "",
        "files": list_corpus_files(corpus_dir),
    }


def save_corpus_transcript(corpus_id: str, transcript_text: str) -> dict[str, Any] | None:
    corpus_dir = CORPORA_DIR / corpus_id
    metadata_path = corpus_dir / "metadata.json"
    if not metadata_path.exists():
        return None

    transcript_path = corpus_dir / "transcript.txt"
    transcript_path.write_text(transcript_text, encoding="utf-8")
    metadata = load_json_file(metadata_path, {})
    metadata["updated_at"] = iso_now()
    metadata["transcript_filename"] = transcript_path.name
    save_json_file(metadata_path, metadata)
    return metadata


def open_folder_in_file_manager(target: Path) -> tuple[bool, str]:
    if not target.exists():
        return False, f"Dossier introuvable : {target}"

    if sys.platform == "darwin":
        command = ["open", str(target)]
    elif os.name == "nt":
        command = ["explorer", str(target)]
    else:
        opener = shutil.which("xdg-open")
        if not opener:
            return False, "Aucun ouvreur de dossier compatible n'a été trouvé."
        command = [opener, str(target)]

    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "Erreur lors de l'ouverture du dossier."
        return False, message
    return True, f"Dossier ouvert : {target}"


class InterviewRequestHandler(SimpleHTTPRequestHandler):
    server_version = "InterviewCollector/1.0"
    protocol_version = "HTTP/1.1"
    static_root = STATIC_DIR
    app_config = load_config()

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        sys.stdout.write("%s - - [%s] %s\n" % (self.client_address[0], self.log_date_time_string(), format % args))

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        request_path = urlparse(getattr(self, "path", "")).path
        if request_path.startswith("/api/"):
            default_message = self.responses.get(code, ("Erreur", ""))[0]
            self.send_json(code, {"ok": False, "error": message or default_message, "status": int(code)})
            return
        super().send_error(code, message, explain)

    def translate_path(self, path: str) -> str:
        parsed = urlparse(path)
        request_path = parsed.path.lstrip("/") or "index.html"
        safe_parts = [part for part in Path(request_path).parts if part not in {"..", "."}]
        full_path = self.static_root.joinpath(*safe_parts)
        return str(full_path)

    def is_local_admin_request(self) -> bool:
        host = (self.headers.get("Host") or "").split(":", 1)[0].strip().lower()
        if not host:
            return True
        return host in {"127.0.0.1", "localhost", "::1", "[::1]"}

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def send_text(self, status: int, payload: str) -> None:
        raw = payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def send_binary_file(self, target_path: Path) -> None:
        if not target_path.exists() or not target_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Fichier introuvable.")
            return
        content = target_path.read_bytes()
        content_type, _ = mimetypes.guess_type(target_path.name)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def serve_static_file(self, target_path: Path) -> None:
        if not target_path.exists() or not target_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Fichier introuvable.")
            return
        content = target_path.read_bytes()
        content_type, _ = mimetypes.guess_type(target_path.name)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type or 'application/octet-stream'}")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def read_json_body(self) -> dict[str, Any]:
        raw = self.read_request_body()
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON invalide: {exc}") from exc

    def read_multipart_form(self) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type.lower():
            raise ValueError("Le formulaire doit être envoyé en multipart/form-data.")

        raw = self.read_request_body()
        parser = BytesParser(policy=email_policy_default)
        message = parser.parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + raw
        )

        fields: dict[str, str] = {}
        files: dict[str, dict[str, Any]] = {}
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if not name:
                continue

            payload = part.get_payload(decode=True) or b""
            filename = part.get_filename()
            if filename:
                files[name] = {
                    "filename": filename,
                    "content_type": part.get_content_type(),
                    "data": payload,
                }
                continue

            charset = part.get_content_charset() or "utf-8"
            fields[name] = payload.decode(charset, errors="replace")

        return fields, files

    def read_request_body(self) -> bytes:
        max_bytes = int(self.app_config.get("max_upload_size_mb", 1024)) * 1024 * 1024
        content_length = self.headers.get("Content-Length")
        body = bytearray()

        if content_length is not None:
            expected = int(content_length)
            if expected > max_bytes:
                raise ValueError("Le fichier dépasse la taille maximale autorisée.")
            remaining = expected
            while remaining > 0:
                chunk = self.rfile.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                body.extend(chunk)
                remaining -= len(chunk)
        else:
            while True:
                chunk = self.rfile.read(1024 * 1024)
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise ValueError("Le fichier dépasse la taille maximale autorisée.")
        return bytes(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if parsed.path == "/api/health":
            self.send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "title": self.app_config.get("title"),
                    "ffmpeg_available": bool(get_ffmpeg_binary(self.app_config)),
                    "transcription_enabled": bool(self.app_config.get("enable_transcription", True)),
                },
            )
            return

        if parsed.path == "/api/admin/overview":
            self.app_config = load_config()
            sessions = list_sessions(limit=10)
            livekit_settings = get_livekit_settings(self.app_config)
            ngrok_status = get_ngrok_status(port=self.server.server_address[1])
            self.send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "title": self.app_config.get("title"),
                    "project_dir": str(ROOT_DIR),
                    "sessions_dir": str(SESSIONS_DIR),
                    "ffmpeg_available": bool(get_ffmpeg_binary(self.app_config)),
                    "tailscale_available": bool(shutil.which("tailscale")),
                    "ngrok_available": ngrok_status["available"],
                    "ngrok_running": ngrok_status["running"],
                    "ngrok_public_url": ngrok_status["public_url"],
                    "whisper_model_size": self.app_config.get("whisper_model_size", "small"),
                    "livekit_url": livekit_settings["livekit_url"],
                    "livekit_configured": has_livekit_config(self.app_config),
                    "sessions_count": len(list_sessions(limit=100000)),
                    "recent_sessions": sessions,
                },
            )
            return

        if parsed.path == "/api/admin/sessions":
            self.send_json(HTTPStatus.OK, {"ok": True, "sessions": list_sessions(limit=100)})
            return

        if parsed.path == "/api/corpora":
            self.send_json(HTTPStatus.OK, {"ok": True, "corpora": list_corpora(limit=200)})
            return

        if parsed.path == "/api/corpora/detail":
            corpus_id = query.get("corpus_id", [""])[0]
            if not corpus_id:
                self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "corpus_id absent."})
                return
            detail = load_corpus_detail(corpus_id)
            if detail is None:
                self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Corpus introuvable."})
                return
            self.send_json(HTTPStatus.OK, {"ok": True, "corpus": detail})
            return

        if parsed.path == "/api/corpora/audio":
            corpus_id = query.get("corpus_id", [""])[0]
            if not corpus_id:
                self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "corpus_id absent."})
                return
            corpus_dir = CORPORA_DIR / corpus_id
            audio_path = find_corpus_audio_file(corpus_dir)
            if audio_path is None:
                self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Piste audio introuvable."})
                return
            self.send_binary_file(audio_path)
            return

        if parsed.path == "/api/livekit/token":
            self.app_config = load_config()
            session_id = query.get("session_id", [""])[0]
            role = normalize_livekit_role(query.get("role", ["participant"])[0])
            if not session_id:
                self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "session_id absent."})
                return
            if not has_livekit_config(self.app_config):
                self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "LiveKit n'est pas encore configuré."})
                return
            session_data = load_livekit_session(session_id)
            if session_data is None:
                self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Session LiveKit introuvable."})
                return
            token = create_livekit_token(self.app_config, session_data, role)
            self.send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "livekit_url": self.app_config.get("livekit_url", ""),
                    "room_name": session_data["room_name"],
                    "session_id": session_data["session_id"],
                    "role": role,
                    "identity": build_livekit_identity(session_id, role),
                    "token": token,
                },
            )
            return

        if parsed.path == "/api/livekit/participant-consent-status":
            session_id = query.get("session_id", [""])[0]
            if not session_id:
                self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "session_id absent."})
                return
            if load_livekit_session(session_id) is None:
                self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Session LiveKit introuvable."})
                return
            consent = load_livekit_consent(session_id)
            self.send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "session_id": session_id,
                    "consent_checked": bool(consent.get("consent_checked")),
                    "consent_timestamp": consent.get("consent_timestamp"),
                },
            )
            return

        if parsed.path == "/api/session-status":
            session_id = query.get("session_id", [""])[0]
            if not session_id:
                self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "session_id absent."})
                return
            session_dir = SESSIONS_DIR / session_id
            processing_path = session_dir / "processing.json"
            processing = load_json_file(processing_path, {})
            metadata = load_json_file(session_dir / "metadata.json", {})
            if metadata.get("mode") == "livekit" and not processing:
                processing = summarize_livekit_processing(session_dir)
            if not processing:
                self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Session introuvable."})
                return
            self.send_json(HTTPStatus.OK, {"ok": True, "processing": processing})
            return

        if parsed.path in {"/admin", "/admin.html"}:
            self.serve_static_file(self.static_root / "admin.html")
            return

        if parsed.path in {"/livekit", "/livekit.html"}:
            self.serve_static_file(self.static_root / "livekit.html")
            return

        if parsed.path in {"/", ""}:
            if self.is_local_admin_request():
                self.serve_static_file(self.static_root / "admin.html")
            else:
                self.serve_static_file(self.static_root / "remote-entry.html")
            return

        safe_target = Path(self.translate_path(self.path))
        self.serve_static_file(safe_target)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if parsed.path == "/api/admin/livekit-settings":
            try:
                payload = self.read_json_body()
            except ValueError as exc:
                self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
                return

            current_config = load_config()
            current_config["livekit_url"] = str(payload.get("livekit_url", "")).strip()
            current_config["livekit_api_key"] = str(payload.get("livekit_api_key", "")).strip()
            current_config["livekit_api_secret"] = str(payload.get("livekit_api_secret", "")).strip()
            save_config(current_config)
            self.app_config = load_config()
            self.send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "message": "Configuration LiveKit enregistrée.",
                    "settings": {
                        "livekit_url": self.app_config.get("livekit_url", ""),
                        "livekit_configured": has_livekit_config(self.app_config),
                    },
                },
            )
            return

        if parsed.path == "/api/admin/ngrok/start":
            public_ok, public_url, message = start_ngrok_tunnel(port=self.server.server_address[1])
            status = HTTPStatus.OK if public_ok else HTTPStatus.BAD_REQUEST
            self.send_json(
                status,
                {
                    "ok": public_ok,
                    "message": message,
                    "public_url": public_url,
                },
            )
            return

        if parsed.path == "/api/admin/ngrok/authtoken":
            try:
                payload = self.read_json_body()
            except ValueError as exc:
                self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
                return

            ok, message = save_ngrok_authtoken(str(payload.get("authtoken", "")))
            status = HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST
            self.send_json(status, {"ok": ok, "message": message})
            return

        if parsed.path == "/api/admin/ngrok/stop":
            ok, message = stop_ngrok_tunnel()
            status = HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST
            self.send_json(status, {"ok": ok, "message": message})
            return

        if parsed.path == "/api/admin/create-livekit-session":
            try:
                payload = self.read_json_body()
            except ValueError as exc:
                self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
                return

            self.app_config = load_config()
            if not has_livekit_config(self.app_config):
                self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "LiveKit n'est pas configuré."})
                return

            participant_code = str(payload.get("participant_code", "")).strip()
            if not participant_code:
                self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "participant_code est requis."})
                return

            notes = str(payload.get("notes", "")).strip()
            participant_role_label = str(payload.get("participant_role_label", "")).strip()
            investigator_role_label = str(payload.get("investigator_role_label", "")).strip()
            base_url = str(payload.get("base_url", "")).strip()
            if not is_valid_public_base_url(base_url):
                fallback_public_url = get_ngrok_status(port=self.server.server_address[1]).get("public_url", "")
                if isinstance(fallback_public_url, str) and is_valid_public_base_url(fallback_public_url):
                    base_url = fallback_public_url
            if not is_valid_public_base_url(base_url):
                self.send_json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "ok": False,
                        "error": "Accès distant indisponible. Démarrez d'abord ngrok depuis l'application.",
                    },
                )
                return
            session_data = create_livekit_session(
                participant_code,
                notes,
                participant_role_label,
                investigator_role_label,
                self.app_config,
            )
            base = base_url.rstrip("/")
            participant_link = f"{base}/livekit.html?session_id={session_data['session_id']}&role=participant"
            investigator_link = f"{base}/livekit.html?session_id={session_data['session_id']}&role=investigator"
            self.send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "session": session_data,
                    "participant_link": participant_link,
                    "investigator_link": investigator_link,
                },
            )
            return

        if parsed.path == "/api/admin/open-folder":
            try:
                payload = self.read_json_body()
            except ValueError as exc:
                self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
                return

            target_name = str(payload.get("target", "")).strip()
            if target_name == "project":
                target_path = ROOT_DIR
            elif target_name == "sessions":
                target_path = SESSIONS_DIR
            elif target_name == "corpora":
                target_path = CORPORA_DIR
            elif target_name.startswith("session:"):
                session_id = target_name.split(":", 1)[1]
                target_path = SESSIONS_DIR / session_id
            elif target_name.startswith("corpus:"):
                corpus_id = target_name.split(":", 1)[1]
                target_path = CORPORA_DIR / corpus_id
            else:
                self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Cible de dossier invalide."})
                return

            ok, message = open_folder_in_file_manager(target_path)
            status = HTTPStatus.OK if ok else HTTPStatus.INTERNAL_SERVER_ERROR
            self.send_json(status, {"ok": ok, "message": message})
            return

        if parsed.path == "/api/admin/settings":
            try:
                payload = self.read_json_body()
            except ValueError as exc:
                self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
                return

            whisper_model_size = normalize_whisper_model_size(payload.get("whisper_model_size"))
            current_config = load_config()
            current_config["whisper_model_size"] = whisper_model_size
            save_config(current_config)
            self.app_config = load_config()
            self.send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "message": f"Modèle Whisper réglé sur {whisper_model_size}.",
                    "settings": {
                        "whisper_model_size": whisper_model_size,
                    },
                },
            )
            return

        if parsed.path == "/api/corpora/import":
            try:
                fields, files = self.read_multipart_form()
            except ValueError as exc:
                self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
                return

            corpus_title = str(fields.get("corpus_title", "")).strip()
            if not corpus_title:
                self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Le nom du corpus est requis."})
                return

            audio_file = files.get("audio_file")
            if not audio_file or not audio_file.get("data"):
                self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Un fichier audio est requis."})
                return

            transcript_file = files.get("transcript_file")
            metadata = import_corpus(corpus_title, audio_file, transcript_file)
            self.send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "message": "Corpus importé.",
                    "corpus": metadata,
                },
            )
            return

        if parsed.path == "/api/corpora/save-transcript":
            try:
                payload = self.read_json_body()
            except ValueError as exc:
                self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
                return

            corpus_id = str(payload.get("corpus_id", "")).strip()
            transcript_text = str(payload.get("transcript_text", ""))
            if not corpus_id:
                self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "corpus_id est requis."})
                return

            metadata = save_corpus_transcript(corpus_id, transcript_text)
            if metadata is None:
                self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Corpus introuvable."})
                return

            self.send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "message": "Transcription enregistrée.",
                    "corpus": metadata,
                },
            )
            return

        if parsed.path == "/api/livekit/participant-consent":
            try:
                payload = self.read_json_body()
            except ValueError as exc:
                self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
                return

            session_id = str(payload.get("session_id", "")).strip()
            if not session_id:
                self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "session_id est requis."})
                return
            if load_livekit_session(session_id) is None:
                self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Session LiveKit introuvable."})
                return

            consent_checked = bool(payload.get("consent_checked"))
            consent_payload = {
                "consent_checked": consent_checked,
                "consent_timestamp": iso_now() if consent_checked else None,
            }
            save_json_file(livekit_consent_path(session_id), consent_payload)
            append_log(SESSIONS_DIR / session_id, f"Consentement participant mis à jour: {consent_checked}")
            self.send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "session_id": session_id,
                    "consent_checked": consent_checked,
                    "message": "Consentement enregistré." if consent_checked else "Consentement retiré.",
                },
            )
            return

        if parsed.path == "/api/livekit/upload-participant-recording":
            session_id = query.get("session_id", [""])[0]
            if not session_id:
                self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "session_id absent."})
                return
            if load_livekit_session(session_id) is None:
                self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Session LiveKit introuvable."})
                return
            saved_consent = load_livekit_consent(session_id)
            consent_checked = self.headers.get("X-Consent-Checked", "").lower() == "true" or bool(saved_consent.get("consent_checked"))
            if not consent_checked:
                self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Consentement non confirmé."})
                return
            try:
                body = self.read_request_body()
            except ValueError as exc:
                self.send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False, "error": str(exc)})
                return
            if not body:
                self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Aucune vidéo reçue."})
                return

            role_dir = livekit_role_dir(SESSIONS_DIR / session_id, "participant")
            content_type = self.headers.get("Content-Type", "video/webm")
            extension = infer_extension(content_type)
            video_path = role_dir / f"raw_video{extension}"
            video_path.write_bytes(body)
            metadata = {
                "role": "participant",
                "uploaded_at": iso_now(),
                "upload_size_bytes": len(body),
                "content_type": content_type,
                "client_timezone": self.headers.get("X-Client-Timezone"),
                "recording_started_at": self.headers.get("X-Recording-Started-At"),
                "recording_ended_at": self.headers.get("X-Recording-Ended-At"),
                "original_filename": self.headers.get("X-Original-Filename"),
            }
            save_json_file(role_dir / "metadata.json", metadata)
            save_json_file(
                role_dir / "consent.json",
                {
                    "consent_checked": True,
                    "consent_timestamp": saved_consent.get("consent_timestamp") or iso_now(),
                },
            )
            update_processing_status(role_dir, "queued", {"step": "upload_complete"})
            append_log(role_dir, "Vidéo participant reçue.")

            worker = threading.Thread(
                target=process_session_async,
                args=(role_dir, load_config()),
                daemon=True,
            )
            worker.start()
            self.send_json(HTTPStatus.OK, {"ok": True, "message": "Vidéo participant reçue.", "session_id": session_id})
            return

        if parsed.path == "/api/livekit/upload-investigator-audio":
            session_id = query.get("session_id", [""])[0]
            if not session_id:
                self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "session_id absent."})
                return
            if load_livekit_session(session_id) is None:
                self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Session LiveKit introuvable."})
                return
            try:
                body = self.read_request_body()
            except ValueError as exc:
                self.send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False, "error": str(exc)})
                return
            if not body:
                self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Aucun audio reçu."})
                return

            role_dir = livekit_role_dir(SESSIONS_DIR / session_id, "investigator")
            content_type = self.headers.get("Content-Type", "audio/webm")
            extension = ".webm" if "webm" in content_type.lower() else infer_extension(content_type)
            audio_path = role_dir / f"raw_audio{extension}"
            audio_path.write_bytes(body)
            metadata = {
                "role": "investigator",
                "uploaded_at": iso_now(),
                "upload_size_bytes": len(body),
                "content_type": content_type,
                "client_timezone": self.headers.get("X-Client-Timezone"),
                "recording_started_at": self.headers.get("X-Recording-Started-At"),
                "recording_ended_at": self.headers.get("X-Recording-Ended-At"),
                "original_filename": self.headers.get("X-Original-Filename"),
            }
            save_json_file(role_dir / "metadata.json", metadata)
            update_processing_status(role_dir, "queued", {"step": "upload_complete"})
            append_log(role_dir, "Audio enquêteur reçu.")

            worker = threading.Thread(
                target=process_audio_only_session_async,
                args=(role_dir, audio_path, load_config()),
                daemon=True,
            )
            worker.start()
            self.send_json(HTTPStatus.OK, {"ok": True, "message": "Audio enquêteur reçu.", "session_id": session_id})
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Endpoint introuvable.")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Outil local de collecte d'entretiens cliniques.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="Lancer le serveur local.")
    serve.add_argument("--host", default="127.0.0.1", help="Adresse d'écoute locale.")
    serve.add_argument("--port", type=int, default=8000, help="Port HTTP local.")
    return parser


def command_serve(args: argparse.Namespace) -> int:
    ensure_runtime_dirs()
    handler = InterviewRequestHandler
    handler.app_config = load_config()
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print("")
    print("Serveur local démarré.")
    print(f"URL locale : http://{args.host}:{args.port}")
    print(f"Dossier des sessions : {SESSIONS_DIR}")
    print("Arrêt : Ctrl+C")
    print("")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt du serveur.")
    finally:
        server.server_close()
    return 0


def main() -> int:
    ensure_runtime_dirs()
    parser = build_arg_parser()
    args = parser.parse_args()

    if args.command == "serve":
        return command_serve(args)

    parser.error("Commande inconnue.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
