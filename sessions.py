"""Session storage — menyimpan token per user Discord dalam JSON lokal."""
import json
import os
import time
import logging
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR = Path(os.getenv("XL_DATA_DIR", str(Path(__file__).parent / "data")))
SESSION_FILE = DATA_DIR / "sessions.json"


class SessionStore:
    def __init__(self, path: Path = SESSION_FILE):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._sessions = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                log.error("Failed to load sessions: %s", e)
        return {}

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._sessions, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    # --- per-user accessors ---

    def save_tokens(self, user_id: int, number: str, tokens: dict):
        entry = self._sessions.get(str(user_id), {})
        entry.update(
            {
                "number": number,
                "access_token": tokens.get("access_token"),
                "refresh_token": tokens.get("refresh_token"),
                "id_token": tokens.get("id_token"),
                "updated_at": int(time.time()),
            }
        )
        self._sessions[str(user_id)] = entry
        self._save()

    def update_tokens(self, user_id: int, tokens: dict):
        entry = self._sessions.get(str(user_id))
        if not entry:
            return False
        entry.update(
            {
                "access_token": tokens.get("access_token"),
                "id_token": tokens.get("id_token"),
                "refresh_token": tokens.get("refresh_token") or entry.get("refresh_token"),
                "updated_at": int(time.time()),
            }
        )
        self._save()
        return True

    def get(self, user_id: int) -> dict | None:
        return self._sessions.get(str(user_id))

    def get_tokens(self, user_id: int) -> dict | None:
        e = self.get(user_id)
        if not e:
            return None
        return {
            "number": e.get("number"),
            "access_token": e.get("access_token"),
            "refresh_token": e.get("refresh_token"),
            "id_token": e.get("id_token"),
        }

    def is_logged_in(self, user_id: int) -> bool:
        e = self.get(user_id)
        return bool(e and e.get("id_token") and e.get("refresh_token"))

    def logout(self, user_id: int):
        self._sessions.pop(str(user_id), None)
        self._save()

    def set_pending_otp(self, user_id: int, number: str, subscriber_id: str | None):
        entry = self._sessions.get(str(user_id), {})
        entry["pending_otp"] = {"number": number, "subscriber_id": subscriber_id, "ts": int(time.time())}
        self._sessions[str(user_id)] = entry
        self._save()

    def get_pending_otp(self, user_id: int) -> dict | None:
        e = self.get(user_id)
        if not e:
            return None
        p = e.get("pending_otp")
        if not p:
            return None
        # OTP kedaluwarsa setelah 5 menit
        if time.time() - p.get("ts", 0) > 300:
            return None
        return p

    def clear_pending_otp(self, user_id: int):
        e = self.get(user_id)
        if e and "pending_otp" in e:
            e.pop("pending_otp", None)
            self._save()
