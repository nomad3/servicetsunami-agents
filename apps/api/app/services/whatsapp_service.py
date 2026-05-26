"""
WhatsApp channel service using neonize (whatsmeow Go backend).
Manages per-tenant WhatsApp Web sessions directly in the FastAPI process.
"""
import asyncio
import base64
import io
import inspect
import logging
import socket
import time
import uuid
from datetime import datetime
from typing import Dict, Optional

import segno
from sqlalchemy.orm import Session

# Lazy neonize imports — neonize requires protobuf>=7 which conflicts with
# temporalio (protobuf<7). Import at use-time only.
NewAClient = None
ConnectedEv = DisconnectedEv = LoggedOutEv = MessageEv = PairStatusEv = None
build_jid = None
ChatPresence = ChatPresenceMedia = None

def _ensure_neonize():
    """Lazy-load neonize on first use. Raises ImportError if unavailable."""
    global NewAClient, ConnectedEv, DisconnectedEv, LoggedOutEv, MessageEv, PairStatusEv
    global build_jid, ChatPresence, ChatPresenceMedia
    if NewAClient is not None:
        return
    from neonize.aioze.client import NewAClient as _NewAClient
    from neonize.aioze.events import (
        ConnectedEv as _ConnectedEv,
        DisconnectedEv as _DisconnectedEv,
        LoggedOutEv as _LoggedOutEv,
        MessageEv as _MessageEv,
        PairStatusEv as _PairStatusEv,
    )
    from neonize.utils import build_jid as _build_jid
    from neonize.utils.enum import ChatPresence as _ChatPresence, ChatPresenceMedia as _ChatPresenceMedia
    NewAClient = _NewAClient
    ConnectedEv = _ConnectedEv
    DisconnectedEv = _DisconnectedEv
    LoggedOutEv = _LoggedOutEv
    MessageEv = _MessageEv
    PairStatusEv = _PairStatusEv
    build_jid = _build_jid
    ChatPresence = _ChatPresence
    ChatPresenceMedia = _ChatPresenceMedia

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.channel_account import ChannelAccount
from app.models.channel_event import ChannelEvent
from app.models.chat import ChatSession
from app.services.url_intent_router import extract_learning_url

logger = logging.getLogger(__name__)

WHATSAPP_AUDIO_TRANSCRIBE_TIMEOUT_SECONDS = 60.0
DEFAULT_WHATSAPP_AUDIO_MIME = "audio/ogg"
WHATSAPP_AUDIO_TRANSCRIPTION_FALLBACK = (
    "[User sent a WhatsApp voice message, but the audio could not be "
    "transcribed. Apologize briefly and ask them to resend the voice note "
    "or type the message.]"
)


def _detect_inbound_media(msg, text: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Return media type, MIME type, and caption for an inbound WhatsApp message."""
    image = getattr(msg, "imageMessage", None)
    if image and getattr(image, "mimetype", None):
        return "image", image.mimetype, getattr(image, "caption", None) or text

    audio = getattr(msg, "audioMessage", None)
    # CRITICAL: protobuf3 message fields default to a present-but-empty
    # sub-message, so `if audio:` is True even when no audio was sent.
    # That mis-classified every WhatsApp inbound (including pure text
    # messages like "Ping") as audio → WHATSAPP_AUDIO_TRANSCRIPTION_
    # FALLBACK fired on every chat turn → Luna's session filled up
    # with "Sorry baby, I couldn't transcribe…" apologies even for
    # plain text. Diagnosed 2026-05-24 from chat_messages history:
    # text going back to 17:54 UTC all show the fallback instruction
    # stored as user content instead of the actual text.
    #
    # Fix: require at least ONE actual audio-payload field (mimetype,
    # fileLength, mediaKey, or directPath) to consider this a real
    # audio message. Mirrors the image-detection pattern above which
    # already requires `image.mimetype`.
    if audio and (
        getattr(audio, "mimetype", None)
        or getattr(audio, "fileLength", 0)
        or getattr(audio, "mediaKey", b"")
        or getattr(audio, "directPath", "")
    ):
        return "audio", getattr(audio, "mimetype", None) or DEFAULT_WHATSAPP_AUDIO_MIME, text

    document = getattr(msg, "documentMessage", None)
    if document and getattr(document, "mimetype", None):
        caption = getattr(document, "title", None) or getattr(document, "fileName", None) or text
        return "document", document.mimetype, caption

    # T4.2 — text-only message may contain a learning URL (YouTube / IG).
    # When matched, surface as a new ``learning_url`` tuple variant so the
    # caller can route to LearningService.dispatch instead of the normal
    # chat/agent pipeline. mime slot carries the URL itself; caption keeps
    # the original message text so Luna's ack can quote / contextualize it.
    learning_url = extract_learning_url(text)
    if learning_url:
        return "learning_url", learning_url, text

    return None, None, text


def _phone_variants(value: str | None) -> set[str]:
    """Return normalized phone variants for allowlist matching.

    Handles WhatsApp JIDs/domains, leading plus signs, and Mexico mobile
    numbers which may appear as either 52... or 521....
    """
    if not value:
        return set()

    base = value.split("@", 1)[0].strip()
    digits = "".join(ch for ch in base if ch.isdigit())
    variants = {base, base.lstrip("+"), digits}

    # Mexico mobile: 52... -> 521... and vice-versa
    if digits.startswith("521") and len(digits) > 3:
        variants.add("52" + digits[3:])
    elif digits.startswith("52") and not digits.startswith("521") and len(digits) > 2:
        variants.add("521" + digits[2:])
    
    # General: If digits starts with country code but no plus, add plus variant
    # and vice-versa if it was provided with plus
    if digits and not value.startswith("+"):
        variants.add("+" + digits)

    return {item for item in variants if item}


import random as _random

def _build_ack_message(user_message: str, task_type: str) -> str:
    """Build a natural, conversational acknowledgment."""
    acks = {
        "code": _random.choice([
            "Let me look at the code",
            "Ok checking that out",
            "Let me dig into the code for you",
            "On it, pulling up the repo",
        ]),
        "research": _random.choice([
            "Let me look into that",
            "Good question, let me find out",
            "Give me a sec, checking my sources",
            "Ok let me research that for you",
        ]),
        "email": _random.choice([
            "Let me check your emails",
            "One sec, looking through your inbox",
            "Ok pulling up your emails",
        ]),
        "calendar": _random.choice([
            "Let me check your calendar",
            "One sec, looking at your schedule",
            "Ok checking that",
        ]),
        "sales": _random.choice([
            "Let me pull up the pipeline",
            "Ok checking the deals",
            "One sec, looking at the numbers",
        ]),
        "data": _random.choice([
            "Let me check the data",
            "Ok running that query",
            "Give me a moment, pulling the numbers",
        ]),
    }
    default = _random.choice([
        "Ok let me see",
        "Let me check that for you",
        "One sec",
        "Let's find out",
        "On it",
        "Give me a moment",
        "Let me look into that",
    ])
    return acks.get(task_type, default)


_PROGRESS_MESSAGES = [
    "Still working on it",
    "Taking a bit longer than usual",
    "Almost there",
    "This one's a bit involved, hang tight",
    "Still going",
    "Bear with me",
    "Nearly done",
]

def _get_progress_message(tick: int) -> str:
    """Get a rotating progress message based on elapsed ticks."""
    return _PROGRESS_MESSAGES[tick % len(_PROGRESS_MESSAGES)]


class WhatsAppService:
    """Manages neonize WhatsApp clients per tenant:account."""

    MAX_RECONNECT_ATTEMPTS = 5
    RECONNECT_BASE_DELAY = 2  # seconds, doubles each attempt

    # 2026-05-20 Option-A hardening (whatsapp-api-research.md §"Stay on
    # neonize + harden"). Three thresholds gate the new safety nets:
    STABLE_CONNECTION_SECONDS = 30  # connection must hold this long
    # before the reconnect counter resets — otherwise a 2-second
    # connect-disconnect flap (the failure mode we hit 2026-05-19
    # repeatedly) keeps restarting at base delay instead of escalating.
    HEARTBEAT_INTERVAL_SECONDS = 30  # IsConnected() poll cadence.
    HEARTBEAT_TIMEOUT_SECONDS = 5    # asyncio.wait_for guard — if the
    # Go callback hangs longer than this, treat as silent socket death.

    def __init__(self, db_url: str):
        _ensure_neonize()
        self._clients: Dict[str, NewAClient] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        self._watchdog_tasks: Dict[str, asyncio.Task] = {}
        self._heartbeat_tasks: Dict[str, asyncio.Task] = {}
        self._stable_reset_tasks: Dict[str, asyncio.Task] = {}
        self._qr_codes: Dict[str, str] = {}
        self._statuses: Dict[str, str] = {}
        self._reconnect_counts: Dict[str, int] = {}
        # asyncio.Lock per account-key to serialize SQLite session
        # save/restore. Eliminates the concurrent-writer race that
        # corrupts the on-disk DB and produces the
        # "database disk image is malformed" decryption failures
        # documented in whatsapp_sqlite_corruption_recovery.md.
        self._session_locks: Dict[str, asyncio.Lock] = {}
        self._sent_message_ids: Dict[str, set] = {}  # Track bot-sent msg IDs to avoid echo loops
        self._lid_phone_cache: Dict[str, str] = {}  # LID→phone cache for resolved numbers
        self._db_url = db_url

    def _purge_local_session_file(
        self, tenant_id: str, account_id: str, *, reason: str
    ) -> None:
        """Delete the on-disk neonize SQLite session file for this
        account so the next `start_pairing` has no credentials to
        rehydrate and mints a fresh QR.

        Called from `disable()` and `logout()`. Without this, those
        endpoints clear the in-memory client + DB status but leave the
        device credentials on disk, so when the user next clicks Link
        Phone the api silently reconnects with the existing credentials
        and never shows a QR — the failure mode the operator hit 4+
        times on 2026-05-20.

        Best-effort: never raises into the caller. If the file is
        already absent (already purged, never created, or another
        concurrent op cleared it) the function is a no-op. Backups
        created earlier in the session (e.g. .corrupt-backup,
        .pre-repair) are not touched.
        """
        import os
        try:
            path = self._client_name(tenant_id, account_id)
            if os.path.exists(path):
                os.remove(path)
                logger.info(
                    "Purged neonize session file (%s): %s", reason, path
                )
            else:
                logger.debug(
                    "_purge_local_session_file: no file at %s (already gone)",
                    path,
                )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to purge neonize session file for %s:%s (%s)",
                tenant_id, account_id, reason,
            )

    def _get_session_lock(self, key: str) -> asyncio.Lock:
        """Fetch-or-create the per-account asyncio.Lock used to
        serialize SQLite session save/restore. Safe to call from any
        async context; the dict insert is single-threaded under
        asyncio's cooperative model so no further synchronization is
        needed for the dict itself."""
        lock = self._session_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._session_locks[key] = lock
        return lock

    async def _save_session_locked(self, tenant_id: str, account_id: str) -> None:
        """Async lock wrapper around `_save_session_to_db`. All async
        event-handler call sites (on_connected, on_disconnected,
        on_paired) go through this; the lock serializes them against
        any concurrent _restore_session_from_db on the same key.

        Best-effort: the underlying save can still raise; we let it
        propagate so existing error paths (which currently swallow) keep
        their semantics. The lock only prevents the race, not the
        exception."""
        key = self._key(tenant_id, account_id)
        async with self._get_session_lock(key):
            # `_save_session_to_db` is synchronous SQLite work + sync
            # SQLAlchemy call; running it directly inside the async
            # handler is fine — it's bounded (≤ a few hundred ms for
            # the 7MB-class blob we've seen in production).
            self._save_session_to_db(tenant_id, account_id)

    async def _socket_heartbeat(self, tenant_id: str, account_id: str) -> None:
        """Background coroutine that polls `client.IsConnected()` every
        HEARTBEAT_INTERVAL_SECONDS. If the call returns False or hangs
        beyond HEARTBEAT_TIMEOUT_SECONDS (silent socket death — the
        whatsmeow event loop has wedged without firing DisconnectedEv),
        trip the existing auto-reconnect path.

        This is the Option-A core fix from
        docs/plans/2026-05-18-whatsapp-api-research.md §"Stay on
        neonize + harden": catches the silent-disconnect failure mode
        that the existing DisconnectedEv-driven reconnect cannot see.

        The task is started in connect_account and torn down on
        explicit disconnect / logout. It MUST not raise into the event
        loop — catch everything and log."""
        key = self._key(tenant_id, account_id)
        logger.info(f"_socket_heartbeat started for {key}")
        try:
            while True:
                await asyncio.sleep(self.HEARTBEAT_INTERVAL_SECONDS)
                status = self._statuses.get(key)
                if status in ("logged_out", "disconnected", None):
                    # Status went terminal — heartbeat owner stops.
                    logger.info(f"_socket_heartbeat exiting for {key} (status={status})")
                    return
                client = self._clients.get(key)
                if client is None:
                    logger.info(f"_socket_heartbeat exiting for {key} (no client)")
                    return
                try:
                    # neonize exposes `is_connected` as a PROPERTY
                    # (verified 2026-05-20 via
                    # `type(getattr(NewAClient, 'is_connected'))` →
                    # `<class 'property'>`). Accessing the attribute
                    # returns the property's getter result — which is
                    # itself a coroutine in the async client build, NOT
                    # a callable. Do not add parens. Then check
                    # awaitable / coerce to bool as before.
                    res = client.is_connected
                    if inspect.isawaitable(res):
                        is_connected = await asyncio.wait_for(
                            res, timeout=self.HEARTBEAT_TIMEOUT_SECONDS,
                        )
                    else:
                        is_connected = bool(res)
                except asyncio.TimeoutError:
                    logger.warning(
                        f"_socket_heartbeat: IsConnected() hung > "
                        f"{self.HEARTBEAT_TIMEOUT_SECONDS}s for {key} — "
                        "treating as silent socket death; tripping reconnect"
                    )
                    self._statuses[key] = "disconnected"
                    self._update_account_status(tenant_id, account_id, "disconnected")
                    asyncio.ensure_future(self._auto_reconnect(tenant_id, account_id))
                    return
                except Exception:  # noqa: BLE001
                    logger.exception(f"_socket_heartbeat: IsConnected() raised for {key}")
                    # Don't trip reconnect on a single weird exception
                    # — could be a transient FFI hiccup. Next tick
                    # decides.
                    continue
                if not is_connected:
                    logger.warning(
                        f"_socket_heartbeat: IsConnected()=False for {key} — "
                        "tripping reconnect"
                    )
                    self._statuses[key] = "disconnected"
                    self._update_account_status(tenant_id, account_id, "disconnected")
                    asyncio.ensure_future(self._auto_reconnect(tenant_id, account_id))
                    return
        except asyncio.CancelledError:
            return

    def _start_heartbeat(self, tenant_id: str, account_id: str) -> None:
        """Cancel any existing heartbeat for this key + start a fresh
        one. Called from on_connected. Idempotent."""
        key = self._key(tenant_id, account_id)
        prev = self._heartbeat_tasks.pop(key, None)
        if prev and not prev.done():
            prev.cancel()
        self._heartbeat_tasks[key] = asyncio.ensure_future(
            self._socket_heartbeat(tenant_id, account_id)
        )

    async def _delayed_counter_reset(self, key: str) -> None:
        """Reset _reconnect_counts[key] to 0 ONLY after the connection
        has been continuously up for STABLE_CONNECTION_SECONDS. If a
        disconnect fires before this delay elapses, the task is
        cancelled and the counter keeps its escalated value — so the
        next reconnect uses the proper backoff delay instead of
        restarting at base.

        Without this, the 2026-05-19 incident pattern (every reconnect
        succeeds for 2-3s then dies) hammered WhatsApp's server every
        ~2s and got nowhere."""
        try:
            await asyncio.sleep(self.STABLE_CONNECTION_SECONDS)
            self._reconnect_counts[key] = 0
            logger.debug(
                f"Stable-connection threshold reached for {key} "
                f"({self.STABLE_CONNECTION_SECONDS}s) — reconnect "
                "counter reset"
            )
        except asyncio.CancelledError:
            return

    WHATSAPP_CONNECT_TIMEOUT = 5  # seconds for pre-flight check

    def _key(self, tenant_id: str, account_id: str = "default") -> str:
        return f"{tenant_id}:{account_id}"

    @staticmethod
    def _is_whatsapp_reachable(timeout: int = 5) -> bool:
        """Pre-flight TCP check to web.whatsapp.com:443.

        Neonize's Go code panics (kills process) on TLS handshake timeout,
        so we verify network connectivity before calling client.connect().
        """
        try:
            sock = socket.create_connection(("web.whatsapp.com", 443), timeout=timeout)
            sock.close()
            return True
        except (OSError, socket.timeout):
            return False

    def _client_name(self, tenant_id: str, account_id: str = "default") -> str:
        import os
        # Use persistent storage so sessions survive pod restarts
        base = settings.DATA_STORAGE_PATH or "/app/storage"
        session_dir = os.environ.get("NEONIZE_SESSION_DIR", f"{base}/neonize_sessions")
        os.makedirs(session_dir, exist_ok=True)
        short = tenant_id[:8]
        return f"{session_dir}/wa_{short}_{account_id}.db"

    # ── DB helpers ────────────────────────────────────────────────────

    def _get_db(self) -> Session:
        return SessionLocal()

    def _get_or_create_account(
        self, db: Session, tenant_id: str, account_id: str = "default",
    ) -> ChannelAccount:
        tid = uuid.UUID(tenant_id)
        acct = (
            db.query(ChannelAccount)
            .filter(
                ChannelAccount.tenant_id == tid,
                ChannelAccount.channel_type == "whatsapp",
                ChannelAccount.account_id == account_id,
            )
            .first()
        )
        if not acct:
            acct = ChannelAccount(
                tenant_id=tid,
                channel_type="whatsapp",
                account_id=account_id,
            )
            db.add(acct)
            try:
                db.flush()
                db.refresh(acct)
            except Exception:
                db.rollback()
                raise
        return acct

    def _update_account_status(
        self, tenant_id: str, account_id: str, status: str,
        error: Optional[str] = None, phone: Optional[str] = None,
    ):
        db = self._get_db()
        try:
            acct = self._get_or_create_account(db, tenant_id, account_id)
            acct.status = status
            acct.updated_at = datetime.utcnow()
            if error is not None:
                acct.last_error = error
            if phone is not None:
                acct.phone_number = phone
            if status == "connected":
                acct.connected_at = datetime.utcnow()
                acct.reconnect_attempts = 0
                acct.last_error = None
            elif status == "disconnected":
                acct.disconnected_at = datetime.utcnow()
            db.commit()
        except Exception:
            logger.exception("Failed to update account status")
            db.rollback()
        finally:
            db.close()

    def _log_event(
        self, tenant_id: str, account_id: str, event_type: str,
        direction: Optional[str] = None, remote_id: Optional[str] = None,
        message_content: Optional[str] = None, extra_data: Optional[dict] = None,
    ):
        db = self._get_db()
        try:
            acct = self._get_or_create_account(db, tenant_id, account_id)
            evt = ChannelEvent(
                tenant_id=uuid.UUID(tenant_id),
                channel_account_id=acct.id,
                event_type=event_type,
                direction=direction,
                remote_id=remote_id,
                message_content=message_content,
                extra_data=extra_data or {},
            )
            db.add(evt)
            db.commit()
        except Exception:
            logger.exception("Failed to log channel event")
            db.rollback()
        finally:
            db.close()

    # ── Session blob persistence ────────────────────────────────────

    def _save_session_to_db(self, tenant_id: str, account_id: str):
        """Compress the neonize SQLite file and store in channel_accounts.session_blob."""
        import gzip
        import os
        import sqlite3
        path = self._client_name(tenant_id, account_id)
        if not os.path.exists(path):
            return
            
        # Try to checkpoint the DB before saving to merge WAL changes into the main .db file.
        # This ensures we don't lose the latest auth keys that might be stuck in the WAL.
        try:
            # Connect with a short timeout to avoid blocking if neonize has it locked
            conn = sqlite3.connect(path, timeout=2)
            conn.execute("PRAGMA wal_checkpoint(FULL)")
            conn.close()
            logger.info(f"Checkpointed neonize DB for {tenant_id[:8]}")
        except Exception as e:
            logger.debug(f"Failed to checkpoint neonize DB (likely locked): {e}")

        try:
            with open(path, "rb") as f:
                raw = f.read()
            compressed = gzip.compress(raw)
            db = self._get_db()
            try:
                acct = self._get_or_create_account(db, tenant_id, account_id)
                acct.session_blob = compressed
                db.commit()
                logger.info(f"Saved neonize session to DB for {tenant_id[:8]}:{account_id} ({len(raw)}→{len(compressed)} bytes)")
            except Exception:
                logger.exception("Failed to save session blob")
                db.rollback()
            finally:
                db.close()
        except Exception:
            logger.exception(f"Failed to read neonize session file {path}")

    def _restore_session_from_db(self, tenant_id: str, account_id: str) -> bool:
        """Decompress session_blob and write neonize SQLite file to disk. Returns True if restored."""
        import gzip
        import os
        db = self._get_db()
        try:
            acct = self._get_or_create_account(db, tenant_id, account_id)
            if not acct.session_blob:
                logger.info(f"No session blob for {tenant_id[:8]}:{account_id}")
                return False
            raw = gzip.decompress(acct.session_blob)
            path = self._client_name(tenant_id, account_id)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            
            # CRITICAL: Delete any stale WAL/SHM files before writing the restored .db file.
            # SQLite will fail to open the database if it finds a WAL file that is
            # inconsistent with the main .db file (which happens if we only restore the .db).
            for suffix in ("-wal", "-shm"):
                try:
                    os.remove(path + suffix)
                except FileNotFoundError:
                    pass
                except Exception:
                    logger.warning(f"Failed to delete stale {suffix} file {path + suffix}")
            
            with open(path, "wb") as f:
                f.write(raw)
            logger.info(f"Restored neonize session from DB for {tenant_id[:8]}:{account_id} ({len(raw)} bytes)")
            return True
        except Exception:
            logger.exception(f"Failed to restore session blob for {tenant_id[:8]}:{account_id}")
            return False
        finally:
            db.close()

    # ── Client lifecycle ─────────────────────────────────────────────

    def _create_client(self, tenant_id: str, account_id: str) -> NewAClient:
        """Create a neonize async client with event handlers bound."""
        key = self._key(tenant_id, account_id)
        name = self._client_name(tenant_id, account_id)

        # Always use SQLite for neonize session storage.
        # PostgreSQL URLs with special chars in password break Go's URL parser.
        client = NewAClient(name)

        # Fix event loop: neonize creates its own loop at import time
        # (asyncio.new_event_loop() that is never started), but we need
        # callbacks on the current running loop (uvicorn's). The execute()
        # method in neonize.aioze.events uses its own module-level
        # event_global_loop, so we must patch BOTH modules.
        try:
            loop = asyncio.get_running_loop()
            client.loop = loop
            import neonize.aioze.client as _neonize_client_mod
            import neonize.aioze.events as _neonize_events_mod
            _neonize_client_mod.event_global_loop = loop
            _neonize_events_mod.event_global_loop = loop
            logger.info(f"Patched neonize event loops for {key}")
        except RuntimeError:
            pass

        # QR callback
        @client.qr
        async def on_qr(c: NewAClient, data_qr: bytes):
            try:
                qr = segno.make_qr(data_qr.decode())
                buf = io.BytesIO()
                qr.save(buf, kind="png", scale=8)
                data_url = f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"
                self._qr_codes[key] = data_url
                self._statuses[key] = "pairing"
                logger.info(f"QR code generated for {key}")
            except Exception:
                logger.exception(f"QR generation failed for {key}")

        # Pair status (fires on successful QR scan / phone linking)
        @client.event(PairStatusEv)
        async def on_pair_status(c: NewAClient, event: PairStatusEv):
            logger.info(f"Pair status event for {key}: {event}")
            self._statuses[key] = "connected"
            self._qr_codes.pop(key, None)
            phone = None
            try:
                me = await c.get_me()
                if me:
                    phone = me.User
            except Exception:
                pass
            self._update_account_status(tenant_id, account_id, "connected", phone=phone)
            self._log_event(tenant_id, account_id, "paired")
            self._save_session_to_db(tenant_id, account_id)

        # Connected
        @client.event(ConnectedEv)
        async def on_connected(c: NewAClient, event: ConnectedEv):
            logger.info(f"ConnectedEv fired for {key}")
            self._statuses[key] = "connected"
            # 2026-05-20 fix: do NOT reset reconnect_counts here. The
            # previous version reset on every ConnectedEv, which let a
            # flapping connection restart at base delay every 2s and
            # hammer WhatsApp's server (verified in the 2026-05-19
            # incident logs). Counter resets via _delayed_counter_reset
            # only after STABLE_CONNECTION_SECONDS of continuous up.
            prev_reset = self._stable_reset_tasks.pop(key, None)
            if prev_reset and not prev_reset.done():
                prev_reset.cancel()
            self._stable_reset_tasks[key] = asyncio.ensure_future(
                self._delayed_counter_reset(key)
            )
            self._qr_codes.pop(key, None)
            phone = None
            try:
                me = await c.get_me()
                if me:
                    phone = me.User
            except Exception:
                pass
            self._update_account_status(tenant_id, account_id, "connected", phone=phone)
            self._log_event(tenant_id, account_id, "connection_opened")
            await self._save_session_locked(tenant_id, account_id)
            self._start_heartbeat(tenant_id, account_id)
            # Register whatsapp shell in presence
            try:
                from app.services import luna_presence_service
                luna_presence_service.register_shell(tenant_id, "whatsapp")
            except Exception:
                pass

        # Disconnected — save session (keys may have rotated), then auto-reconnect
        @client.event(DisconnectedEv)
        async def on_disconnected(c: NewAClient, event: DisconnectedEv):
            logger.warning(f"DisconnectedEv for {key}")
            # Cancel the pending stable-counter-reset (if any) so the
            # next reconnect sees the escalated count, not zero.
            pending_reset = self._stable_reset_tasks.pop(key, None)
            if pending_reset and not pending_reset.done():
                pending_reset.cancel()
            # Cancel the heartbeat — a new one starts on next ConnectedEv.
            hb = self._heartbeat_tasks.pop(key, None)
            if hb and not hb.done():
                hb.cancel()
            await self._save_session_locked(tenant_id, account_id)
            self._statuses[key] = "disconnected"
            self._update_account_status(tenant_id, account_id, "disconnected")
            self._log_event(tenant_id, account_id, "connection_closed")
            # Mute presence while WhatsApp is disconnected
            try:
                from app.services import luna_presence_service
                luna_presence_service.update_state(tenant_id, privacy="muted")
            except Exception:
                pass
            # Schedule auto-reconnect
            asyncio.ensure_future(self._auto_reconnect(tenant_id, account_id))

        # NOTE: StreamReplacedEv is NOT registered — it crashes the neonize Go binary
        # with "panic: index out of range [0] with length 0" in CallbackFunction.
        # Disconnection is handled via DisconnectedEv instead.

        # Logged out
        @client.event(LoggedOutEv)
        async def on_logged_out(c: NewAClient, event: LoggedOutEv):
            logger.info(f"LoggedOutEv for {key}")
            self._statuses[key] = "logged_out"
            self._qr_codes.pop(key, None)
            self._update_account_status(tenant_id, account_id, "logged_out")
            self._log_event(tenant_id, account_id, "logged_out")
            # Mute presence when WhatsApp session ends
            try:
                from app.services import luna_presence_service
                luna_presence_service.update_state(tenant_id, privacy="muted")
            except Exception:
                pass

        # Inbound messages
        @client.event(MessageEv)
        async def on_message(c: NewAClient, event: MessageEv):
            try:
                await self._handle_inbound(key, tenant_id, account_id, c, event)
            except Exception:
                logger.exception(f"Error handling inbound message for {key}")

        self._clients[key] = client
        self._statuses[key] = "connecting"
        return client

    async def _auto_reconnect(self, tenant_id: str, account_id: str):
        """Auto-reconnect after disconnect with exponential backoff."""
        key = self._key(tenant_id, account_id)
        attempt = self._reconnect_counts.get(key, 0) + 1
        self._reconnect_counts[key] = attempt

        if attempt > self.MAX_RECONNECT_ATTEMPTS:
            logger.error(f"Max reconnect attempts ({self.MAX_RECONNECT_ATTEMPTS}) reached for {key}")
            self._update_account_status(tenant_id, account_id, "disconnected",
                                        error=f"Max reconnect attempts reached after {attempt - 1} tries")
            return

        delay = self.RECONNECT_BASE_DELAY * (2 ** (attempt - 1))
        logger.info(f"Auto-reconnect attempt {attempt}/{self.MAX_RECONNECT_ATTEMPTS} for {key} in {delay}s")
        await asyncio.sleep(delay)

        # Check if status was manually changed (e.g., disabled, logged out)
        current_status = self._statuses.get(key)
        if current_status in ("logged_out", None):
            logger.info(f"Skipping auto-reconnect for {key} — status is {current_status}")
            return

        try:
            result = await self.reconnect(tenant_id, account_id)
            if result.get("status") == "unreachable":
                logger.warning(f"Auto-reconnect skipped for {key} — network unreachable, will retry")
                # Schedule another attempt (count already incremented)
                asyncio.ensure_future(self._auto_reconnect(tenant_id, account_id))
            else:
                logger.info(f"Auto-reconnect initiated for {key}")
        except Exception:
            logger.exception(f"Auto-reconnect failed for {key}")

    async def _connection_watchdog(self, key: str, tenant_id: str, account_id: str):
        """Monitor the connection task; reconnect if it dies unexpectedly."""
        try:
            task = self._tasks.get(key)
            if not task:
                return
            # Wait for the connection task to finish (it shouldn't under normal operation)
            await task
        except asyncio.CancelledError:
            return  # Normal shutdown
        except Exception as e:
            logger.warning(f"Connection task for {key} ended with error: {e}")

        # Connection task ended — check if we should reconnect
        status = self._statuses.get(key)
        if status in ("logged_out", None):
            return
        # If DisconnectedEv already triggered reconnect, skip
        if status == "connecting":
            return

        logger.warning(f"Connection task died for {key} (status={status}), triggering auto-reconnect")
        self._statuses[key] = "disconnected"
        self._update_account_status(tenant_id, account_id, "disconnected")
        await self._auto_reconnect(tenant_id, account_id)

    async def _handle_inbound(
        self, key: str, tenant_id: str, account_id: str,
        client: NewAClient, event: MessageEv,
    ):
        """Process an inbound WhatsApp message through agent pipeline."""
        info = event.Info
        msg = event.Message

        sender_jid_obj = info.MessageSource.Sender  # Full JID object (preserves LID vs phone)
        sender_jid = sender_jid_obj.User if sender_jid_obj else ""
        chat_jid = info.MessageSource.Chat.User if info.MessageSource.Chat else ""
        is_from_me = info.MessageSource.IsFromMe
        is_group = info.MessageSource.IsGroup
        text = msg.conversation or (msg.extendedTextMessage.text if msg.extendedTextMessage else "")

        # Extract message ID for echo detection
        msg_id = info.ID if hasattr(info, 'ID') else ""

        # Detect media messages (images, audio, documents)
        media_bytes = None
        media_mime = None
        media_type = None
        media_caption = text

        media_type, media_mime, media_caption = _detect_inbound_media(msg, text)

        # Download media if present
        # Audio can be large — use a longer timeout than images/docs
        if media_type:
            _download_timeout = 90 if media_type == "audio" else 30
            try:
                media_bytes = await asyncio.wait_for(
                    client.download_any(event.Message), timeout=_download_timeout,
                )
                logger.info(f"Downloaded {media_type} ({len(media_bytes)} bytes) from {sender_jid}")
            except Exception as e:
                # Audio-specific neonize regression 2026-05-24: download_any
                # raises "didn't find any attachments in message" for inbound
                # AudioMessage despite the audioMessage being present in the
                # envelope. Text and image downloads via download_any continue
                # to work. Fall back to download_media_with_path called
                # directly with the audio fields — bypasses the broken
                # attachment-detection branch on the Go side while keeping
                # everything else unchanged.
                _err_str = str(e)
                _audio_attachment_miss = (
                    media_type == "audio"
                    and "didn't find any attachments" in _err_str
                )
                if _audio_attachment_miss:
                    try:
                        from neonize.utils.enum import (
                            MediaType as _MediaType,
                            MediaTypeToMMS as _MediaTypeToMMS,
                        )
                        audio_msg = getattr(msg, "audioMessage", None)
                        if audio_msg is None:
                            raise RuntimeError(
                                "audioMessage missing on fallback path "
                                "(should have been present per _detect_inbound_media)"
                            )
                        media_bytes = await asyncio.wait_for(
                            client.download_media_with_path(
                                audio_msg.directPath,
                                audio_msg.fileEncSHA256,
                                audio_msg.fileSHA256,
                                audio_msg.mediaKey,
                                audio_msg.fileLength,
                                _MediaType.MediaAudio,
                                _MediaTypeToMMS.MediaAudio,
                            ),
                            timeout=_download_timeout,
                        )
                        logger.info(
                            "Downloaded audio (%d bytes) via "
                            "download_media_with_path fallback for %s — "
                            "neonize download_any attachment-detection "
                            "workaround",
                            len(media_bytes), sender_jid,
                        )
                    except Exception as fallback_exc:
                        logger.warning(
                            "Audio fallback download_media_with_path also "
                            "failed for %s: %s (original error: %s)",
                            sender_jid, fallback_exc, e,
                        )
                        media_bytes = None
                else:
                    logger.warning(
                        f"Failed to download {media_type} from {sender_jid}: {e}"
                    )
                    media_bytes = None

        # Skip if no text AND no media
        if not text and not media_bytes and not media_type:
            return

        # Skip group messages — only handle DMs for now
        if is_group:
            return

        # Skip messages the user sends to other contacts — only process self-chat or inbound DMs
        if is_from_me and chat_jid != sender_jid:
            return

        # Skip bot echo replies in self-chat
        if is_from_me:
            sent_ids = self._sent_message_ids.get(key, set())
            if msg_id and msg_id in sent_ids:
                sent_ids.discard(msg_id)
                return

        # Resolve LID → phone number if needed (WhatsApp now uses LIDs for DMs)
        sender_phone = sender_jid  # default: assume JID is the phone
        try:
            # neonize client method to resolve LID to phone number
            pn_result = await asyncio.wait_for(client.get_pn_from_lid(sender_jid_obj), timeout=5)
            if pn_result:
                resolved = pn_result.User if hasattr(pn_result, 'User') else str(pn_result)
                logger.info(f"Resolved LID {sender_jid} → phone {resolved}")
                sender_phone = resolved
                self._lid_phone_cache[sender_jid] = resolved
        except Exception as e:
            logger.debug(f"LID→phone resolution failed for {sender_jid}: {e}")
            # Fallback 1: check LID→phone cache from previous successful resolutions
            if sender_jid in self._lid_phone_cache:
                sender_phone = self._lid_phone_cache[sender_jid]
                logger.info(f"Using cached LID→phone: {sender_jid} → {sender_phone}")
            # Fallback 2: in DMs, chat_jid is often the phone number even when sender is a LID
            elif not is_group and chat_jid and chat_jid != sender_jid:
                sender_phone = chat_jid
                logger.info(f"Using chat JID as phone fallback: {sender_jid} → {chat_jid}")

        # DM policy enforcement
        db = self._get_db()
        try:
            acct = self._get_or_create_account(db, tenant_id, account_id)
            if acct.dm_policy == "allowlist":
                allowed = acct.allow_from or []
                if "*" not in allowed:
                    allowed_variants = set()
                    for allowed_value in allowed:
                        allowed_variants.update(_phone_variants(allowed_value))

                    candidates = set()
                    candidates.update(_phone_variants(sender_jid))
                    candidates.update(_phone_variants(sender_phone))
                    candidates.update(_phone_variants(chat_jid))

                    matches = bool(candidates & allowed_variants)
                    if not matches:
                        logger.info(f"Blocked message from {sender_jid} (phone={sender_phone}, not in allowlist {allowed})")
                        return
        finally:
            db.close()

        # Update presence: listening on whatsapp
        try:
            from app.services import luna_presence_service
            luna_presence_service.update_state(tenant_id, state="listening", active_shell="whatsapp")
        except Exception:
            pass

        logger.info(f"Inbound DM from {sender_phone} (jid={sender_jid}) in {key}: {text[:100]}")
        self._log_event(
            tenant_id, account_id, "message_inbound",
            direction="inbound", remote_id=sender_phone,
            message_content=text,
            extra_data={"chat_jid": chat_jid, "is_group": is_group},
        )

        # Show "typing..." indicator while processing.
        # WhatsApp auto-dismisses composing presence after ~5s, so we refresh
        # it every 4s in a background task until the response is ready.
        reply_jid = build_jid(sender_phone)

        # Helper to send a message and track its ID for echo suppression
        async def _send_and_track(msg: str):
            try:
                resp = await client.send_message(reply_jid, msg)
                if resp and hasattr(resp, 'ID') and resp.ID:
                    _ids = self._sent_message_ids.setdefault(key, set())
                    _ids.add(resp.ID)
                    if len(_ids) > 100:
                        _ids.pop()
            except Exception:
                pass

        typing_done = asyncio.Event()

        async def _keep_typing():
            while not typing_done.is_set():
                try:
                    await client.send_chat_presence(
                        reply_jid,
                        ChatPresence.CHAT_PRESENCE_COMPOSING,
                        ChatPresenceMedia.CHAT_PRESENCE_MEDIA_TEXT,
                    )
                except Exception:
                    pass
                try:
                    await asyncio.wait_for(typing_done.wait(), timeout=4.0)
                    break
                except asyncio.TimeoutError:
                    continue

        typing_task = asyncio.create_task(_keep_typing())

        # Process through agent — use phone number (not LID) as session key
        try:
            media_parts = None
            doc_text = None

            if media_bytes and media_type == "document":
                # Documents (PDF, Excel, CSV, text): extract text locally, embed it.
                # Don't send raw bytes to the LLM — process in Python.
                media_filename = ""
                if msg.documentMessage:
                    media_filename = msg.documentMessage.fileName or msg.documentMessage.title or ""
                doc_text = await self._extract_and_embed_document(
                    tenant_id, media_bytes, media_mime or "", media_filename,
                )
            elif media_bytes and media_type == "image":
                # Images: save locally, embed, and describe for CLI agents
                # CLI mode can't process inline images — extract description
                import base64
                img_b64 = base64.b64encode(media_bytes).decode()
                img_size = len(media_bytes)
                doc_text = (
                    f"[User sent an image ({media_mime or 'image'}, {img_size:,} bytes). "
                    f"Caption: {media_caption or 'no caption'}. "
                    f"Please acknowledge the image was received and respond based on the caption/context.]"
                )
                # Also build media_parts for multimodal processing
                try:
                    from app.services.media_utils import build_media_parts
                    media_parts, _ = build_media_parts(
                        media_bytes=media_bytes,
                        mime_type=media_mime,
                        caption=media_caption or "",
                        filename="",
                    )
                except ValueError:
                    pass
                # Embed image description for future recall
                try:
                    from app.services.embedding_service import embed_and_store
                    from app.db.session import SessionLocal
                    import uuid as _uuid
                    edb = SessionLocal()
                    try:
                        embed_and_store(
                            edb,
                            tenant_id=_uuid.UUID(tenant_id),
                            content_type="whatsapp_image",
                            content_id=f"wa_img_{hash(media_bytes) % 10000}",
                            text_content=f"Image from {sender_phone}: {media_caption or 'no caption'}",
                        )
                        edb.commit()
                    finally:
                        edb.close()
                except Exception:
                    pass
            elif media_bytes and media_type == "audio":
                # Audio (voice notes): transcribe via the code-worker
                # workflow. _handle_inbound is async, so we MUST go through
                # transcribe_async — transcribe_bytes_sync's ThreadPoolExecutor
                # bridge blocks the event loop. See transcription_client.py.
                try:
                    from app.services.transcription_client import (
                        TranscriptionUnavailable,
                        transcribe_async,
                    )
                    transcript = None
                    try:
                        tr = await transcribe_async(
                            media_bytes,
                            sync_timeout=WHATSAPP_AUDIO_TRANSCRIBE_TIMEOUT_SECONDS,
                        )
                        if tr.status == "completed":
                            transcript = tr.transcript
                        elif tr.status == "pending":
                            logger.warning(
                                "WhatsApp audio transcription still pending after %.0fs "
                                "for %s (job=%s)",
                                WHATSAPP_AUDIO_TRANSCRIBE_TIMEOUT_SECONDS,
                                sender_phone,
                                tr.job_id,
                            )
                    except TranscriptionUnavailable as exc:
                        logger.warning(
                            f"Transcription unavailable for {sender_phone}: {exc}"
                        )
                    if transcript:
                        logger.info(f"Whisper transcript ({len(transcript)} chars) for {sender_phone}")
                        doc_text = transcript
                    else:
                        logger.warning(
                            "WhatsApp audio transcription empty for %s; "
                            "CLI channel cannot consume inline audio",
                            sender_phone,
                        )
                except Exception as e:
                    logger.warning(f"Audio processing failed for {sender_phone}: {e}")
            elif media_bytes:
                # Other media types: send to LLM as media_parts
                try:
                    from app.services.media_utils import build_media_parts
                    media_parts, _ = build_media_parts(
                        media_bytes=media_bytes,
                        mime_type=media_mime,
                        caption=media_caption or "",
                        filename="",
                    )
                except ValueError as e:
                    logger.warning(f"Media processing failed for {sender_phone}: {e}")

            # If we extracted document/audio text, prepend it to the agent message
            if doc_text:
                if media_type == "audio":
                    # Voice note — send transcript directly, no document wrapper
                    agent_text = doc_text
                else:
                    media_filename = ""
                    if msg.documentMessage:
                        media_filename = msg.documentMessage.fileName or msg.documentMessage.title or ""
                    agent_text = f"[User sent document: {media_filename}]\n\nExtracted content:\n{doc_text[:3000]}"
                    if len(doc_text) > 3000:
                        agent_text += f"\n\n(Document truncated — {len(doc_text)} chars total, embedded for search)"
            elif media_type == "audio":
                agent_text = WHATSAPP_AUDIO_TRANSCRIPTION_FALLBACK
            else:
                agent_text = media_caption or text or f"[Sent {media_type}]"

            response_text = await self._process_through_agent(
                tenant_id, sender_phone, agent_text, media_parts=media_parts,
            )
        finally:
            # Stop the typing indicator loop
            typing_done.set()
            await typing_task

        if not response_text:
            logger.warning(f"Empty response from agent for {sender_phone}, not sending reply")
        if response_text:
            try:
                # Split long messages — WhatsApp limits to ~4096 chars
                chunks = [response_text] if len(response_text) <= 4000 else [
                    response_text[i:i + 4000] for i in range(0, len(response_text), 4000)
                ]

                for chunk in chunks:
                    resp = await client.send_message(reply_jid, chunk)
                    # Track sent message ID to avoid echo loop on self-messages
                    if resp and hasattr(resp, 'ID') and resp.ID:
                        sent_ids = self._sent_message_ids.setdefault(key, set())
                        sent_ids.add(resp.ID)
                        # Cap the set size to prevent memory leak
                        if len(sent_ids) > 100:
                            sent_ids.pop()

                self._log_event(
                    tenant_id, account_id, "message_outbound",
                    direction="outbound", remote_id=sender_phone,
                    message_content=response_text,
                )
                # Stop typing indicator
                try:
                    await client.send_chat_presence(
                        reply_jid,
                        ChatPresence.CHAT_PRESENCE_PAUSED,
                        ChatPresenceMedia.CHAT_PRESENCE_MEDIA_TEXT,
                    )
                except Exception:
                    pass
            except Exception:
                logger.exception(f"Failed to send reply to {sender_phone} (jid={sender_jid})")

    async def _extract_and_embed_document(
        self, tenant_id: str, media_bytes: bytes, mime_type: str, filename: str,
    ) -> Optional[str]:
        """Extract text from a document and embed it locally. No LLM needed.

        Supports PDF, Excel, CSV, and plain text files.
        Returns extracted text or None on failure.
        """
        import io
        text_content = None

        try:
            # PDF
            if mime_type == "application/pdf" or filename.lower().endswith(".pdf"):
                import pdfplumber
                with pdfplumber.open(io.BytesIO(media_bytes)) as pdf:
                    pages = [p.extract_text() for p in pdf.pages if p.extract_text()]
                    text_content = "\n\n".join(pages) if pages else None

            # Excel
            elif "spreadsheet" in mime_type or "excel" in mime_type or filename.lower().endswith((".xlsx", ".xls")):
                import pandas as pd
                df = pd.read_excel(io.BytesIO(media_bytes))
                text_content = df.to_string(max_rows=200)

            # CSV
            elif "csv" in mime_type or filename.lower().endswith(".csv"):
                text_content = media_bytes.decode("utf-8", errors="replace")

            # Plain text / code
            elif mime_type.startswith("text/") or filename.lower().endswith((".txt", ".md", ".json", ".py", ".js")):
                text_content = media_bytes.decode("utf-8", errors="replace")

            if not text_content:
                return None

            logger.info(f"Extracted {len(text_content)} chars from {filename} ({mime_type})")

            # Embed the document content for semantic search
            try:
                from app.services.embedding_service import embed_and_store
                from app.db.session import SessionLocal
                import uuid as _uuid
                db = SessionLocal()
                try:
                    embed_and_store(
                        db,
                        tenant_id=_uuid.UUID(tenant_id),
                        content_type="whatsapp_document",
                        content_id=f"wa_{filename}_{hash(media_bytes) % 10000}",
                        text_content=text_content[:8000],
                    )
                    db.commit()
                    logger.info(f"Embedded document {filename} for tenant {tenant_id[:8]}")
                finally:
                    db.close()
            except Exception:
                logger.debug("Document embedding failed (non-fatal)", exc_info=True)

            return text_content

        except Exception:
            logger.warning(f"Document extraction failed for {filename}", exc_info=True)
            return None

    async def _process_through_agent(
        self, tenant_id: str, sender_id: str, message: str,
        media_parts: list | None = None,
    ) -> Optional[str]:
        """Route inbound message through the same agent pipeline as the chat UI.

        This ensures WhatsApp conversations share the same agent kits,
        LLM provider, conversation history, and Temporal workflow audit trail.
        """
        db = self._get_db()
        try:
            from app.services import chat as chat_service
            from app.models.agent import Agent
            from app.services._agent_ordering import agent_status_rank
            from app.models.user import User

            tid = uuid.UUID(tenant_id)

            # Find the tenant's admin user (needed for session context)
            user = db.query(User).filter(User.tenant_id == tid).first()
            if not user:
                logger.error(f"No user found for tenant {tenant_id}")
                return None

            # Find the tenant's primary agent — prefer Luna, then production > staging > draft
            agent = (
                db.query(Agent)
                .filter(Agent.tenant_id == tid)
                .order_by(
                    (Agent.name == "Luna").desc(),
                    agent_status_rank.asc(),
                    Agent.id.asc(),
                )
                .first()
            )
            if not agent:
                logger.warning(f"No agent found for tenant {tenant_id}")
                return None

            # Find or create a WhatsApp chat session keyed by sender
            session_key = f"whatsapp:{sender_id}"
            session = (
                db.query(ChatSession)
                .filter(
                    ChatSession.tenant_id == tid,
                    ChatSession.source == "whatsapp",
                    ChatSession.external_id == session_key,
                )
                .first()
            )
            if not session:
                session = ChatSession(
                    title=f"WhatsApp: {sender_id}",
                    tenant_id=tid,
                    agent_id=agent.id,
                    source="whatsapp",
                    external_id=session_key,
                )
                db.add(session)
                db.commit()
                db.refresh(session)
            elif not session.agent_id:
                # Backfill agent on existing sessions
                session.agent_id = agent.id
                db.commit()
                db.refresh(session)

            # Route through the same chat service as the web UI
            # This calls agent selection → LLM → tools → audit
            # Wrapper captures content string eagerly in the thread (avoids
            # SQLAlchemy lazy-loading issues when crossing the thread boundary)
            # [chat-trace] anchor the WhatsApp → API thread-pool boundary. The
            # 21:12→23:09 silent hang from 2026-04-28 lost ~2h between the
            # `Inbound DM` log and the next visible log line; without these
            # bookends a hang inside `to_thread` (or pre-handler DB session
            # setup) is opaque from the api logs alone.
            _trace_t0 = time.perf_counter()
            logger.info(
                "[chat-trace] handoff: to_thread session=%s sender=%s",
                str(session.id)[:8], sender_id,
            )

            def _run_chat():
                logger.info(
                    "[chat-trace] enter _run_chat: session=%s elapsed=%.0fms",
                    str(session.id)[:8], (time.perf_counter() - _trace_t0) * 1000,
                )
                _user_msg, assistant_msg = chat_service.post_user_message(
                    db,
                    session=session,
                    user_id=user.id,
                    content=message,
                    sender_phone=sender_id,
                    media_parts=media_parts,
                )
                # Eagerly capture content before leaving the thread
                return assistant_msg.content if assistant_msg else None

            response = await asyncio.to_thread(_run_chat)
            logger.info(
                "[chat-trace] handoff: returned session=%s elapsed=%.0fms response=%s",
                str(session.id)[:8], (time.perf_counter() - _trace_t0) * 1000,
                "ok" if response else "none",
            )
            logger.info(f"Agent response for {sender_id}: len={len(response) if response else 0}")
            return response
        except Exception:
            logger.exception("Failed to process through agent pipeline")
            db.rollback()
            return None
        finally:
            db.close()

    # ── Public API ───────────────────────────────────────────────────

    async def enable(
        self, tenant_id: str, account_id: str = "default",
        dm_policy: str = "allowlist", allow_from: list = None,
    ) -> dict:
        db = self._get_db()
        try:
            acct = self._get_or_create_account(db, tenant_id, account_id)
            acct.enabled = True
            acct.dm_policy = dm_policy
            acct.allow_from = allow_from or []
            acct.updated_at = datetime.utcnow()
            db.commit()
            return {"account_id": account_id, "enabled": True, "dm_policy": dm_policy}
        finally:
            db.close()

    async def disable(self, tenant_id: str, account_id: str = "default") -> dict:
        key = self._key(tenant_id, account_id)
        # Prevent auto-reconnect
        self._statuses[key] = "logged_out"
        # Cancel watchdog + heartbeat
        watchdog = self._watchdog_tasks.pop(key, None)
        if watchdog and not watchdog.done():
            watchdog.cancel()
        hb = self._heartbeat_tasks.pop(key, None)
        if hb and not hb.done():
            hb.cancel()
        # Disconnect if active
        if key in self._clients:
            try:
                await self._clients[key].disconnect()
            except Exception:
                pass
            self._clients.pop(key, None)
            self._qr_codes.pop(key, None)
        # Cancel background task
        task = self._tasks.pop(key, None)
        if task and not task.done():
            task.cancel()

        # Purge the on-disk neonize SQLite session file. Without this,
        # the next `start_pairing` rehydrates the existing device
        # credentials and never shows a QR — diagnosed 2026-05-20 with
        # Simon (4 incidents in one session). See
        # whatsapp_sqlite_corruption_recovery.md + Luna observation
        # 3d01949b.
        self._purge_local_session_file(tenant_id, account_id, reason="disable")

        db = self._get_db()
        try:
            acct = self._get_or_create_account(db, tenant_id, account_id)
            acct.enabled = False
            acct.status = "disconnected"
            acct.updated_at = datetime.utcnow()
            db.commit()
            return {"account_id": account_id, "enabled": False}
        finally:
            db.close()

    async def update_settings(
        self, tenant_id: str, account_id: str = "default",
        dm_policy: str = "allowlist", allow_from: list = None,
    ) -> dict:
        """Update allowlist / DM policy without changing enabled state."""
        db = self._get_db()
        try:
            acct = self._get_or_create_account(db, tenant_id, account_id)
            acct.dm_policy = dm_policy
            acct.allow_from = allow_from or []
            acct.updated_at = datetime.utcnow()
            db.commit()
            return {"account_id": account_id, "dm_policy": dm_policy, "allow_from": acct.allow_from}
        finally:
            db.close()

    async def start_pairing(
        self, tenant_id: str, account_id: str = "default", force: bool = False,
    ) -> dict:
        key = self._key(tenant_id, account_id)

        # If force, disconnect existing client and delete session file
        if force:
            if key in self._clients:
                try:
                    await self._clients[key].disconnect()
                except Exception:
                    pass
                self._clients.pop(key, None)
            task = self._tasks.pop(key, None)
            if task and not task.done():
                task.cancel()
            self._qr_codes.pop(key, None)
            # Delete session DB so neonize requests a fresh QR instead of reusing stale auth
            import os
            session_path = self._client_name(tenant_id, account_id)
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(session_path + suffix)
                except FileNotFoundError:
                    pass
            # Also clear the session blob in the database
            db = self._get_db()
            try:
                acct = self._get_or_create_account(db, tenant_id, account_id)
                acct.session_blob = None
                acct.status = "pairing"
                db.commit()
            except Exception:
                db.rollback()
            finally:
                db.close()

        # Pre-flight: verify WhatsApp is reachable before connect() —
        # neonize Go code panics (kills process) on TLS handshake timeout.
        reachable = await asyncio.get_event_loop().run_in_executor(
            None, self._is_whatsapp_reachable, self.WHATSAPP_CONNECT_TIMEOUT
        )
        if not reachable:
            logger.warning(f"WhatsApp unreachable, cannot start pairing for {key}")
            self._statuses[key] = "disconnected"
            self._update_account_status(tenant_id, account_id, "disconnected",
                                        error="WhatsApp servers unreachable")
            return {"error": "WhatsApp servers are currently unreachable. Check network connectivity."}

        # Create client and start connection (QR will be emitted via callback)
        client = self._create_client(tenant_id, account_id)
        self._clients[key] = client
        # Reset reconnect counter on fresh pairing
        self._reconnect_counts[key] = 0
        # connect() returns a Task — await to get the actual running connection task
        connect_task = await client.connect()
        self._tasks[key] = connect_task
        # Start watchdog to detect unexpected disconnects (StreamReplaced, EOF, etc.)
        old_watchdog = self._watchdog_tasks.pop(key, None)
        if old_watchdog and not old_watchdog.done():
            old_watchdog.cancel()
        self._watchdog_tasks[key] = asyncio.ensure_future(
            self._connection_watchdog(key, tenant_id, account_id)
        )

        # Wait briefly for QR to be generated or existing session to restore
        for i in range(30):
            await asyncio.sleep(0.5)
            if key in self._qr_codes:
                return {
                    "qr_data_url": self._qr_codes[key],
                    "message": "Scan QR code with WhatsApp > Linked Devices > Link a Device",
                }
            if self._statuses.get(key) == "connected":
                return {
                    "qr_data_url": None,
                    "message": "Already connected (existing session restored)",
                    "connected": True,
                }
            # After 3 seconds, also check active connection (session may auto-restore
            # without events firing after whatsmeow's internal 515 reconnect).
            if i >= 6:
                try:
                    # Sync .connected attr is most reliable indicator of active connection
                    connected = getattr(client, "connected", False)
                    # Fallback: check if we have credentials on disk
                    logged_in = False
                    try:
                        # is_logged_in is a PROPERTY on neonize NewAClient
                        # (verified 2026-05-20). Drop the parens; the getter
                        # may return either a bool or an awaitable
                        # depending on the build. Same fix pattern as
                        # _socket_heartbeat in PR #599.
                        res = client.is_logged_in
                        if inspect.isawaitable(res):
                            logged_in = await asyncio.wait_for(res, timeout=2)
                        else:
                            logged_in = bool(res)
                    except Exception as e:
                        logger.debug(f"is_logged_in check failed: {e}")
                        pass
                        
                    logger.info(f"start_pairing: active probe i={i} connected={connected} logged_in={logged_in} for {key}")
                    
                    if connected:
                        phone = None
                        try:
                            me = await asyncio.wait_for(client.get_me(), timeout=3)
                            phone = me.User if me else None
                        except Exception:
                            pass
                        logger.info(f"start_pairing: active detection found {key} connected as {phone}")
                        self._statuses[key] = "connected"
                        self._qr_codes.pop(key, None)
                        self._update_account_status(tenant_id, account_id, "connected", phone=phone)
                        self._save_session_to_db(tenant_id, account_id)
                        return {
                            "qr_data_url": None,
                            "message": "Already connected (existing session restored)",
                            "connected": True,
                        }
                    elif logged_in:
                        # Authenticated but not yet connected to servers (maybe 515 reconnecting)
                        # We stay in the loop to wait for real connection or QR
                        self._statuses[key] = "connecting"
                        self._qr_codes.pop(key, None)
                        
                except Exception as e:
                    logger.info(f"start_pairing: active probe error: {e}")

        return {
            "qr_data_url": None,
            "message": "QR code not yet available, try polling /pair/status",
        }

    async def get_pairing_status(self, tenant_id: str, account_id: str = "default") -> dict:
        key = self._key(tenant_id, account_id)
        status = self._statuses.get(key, "disconnected")

        # Active detection: if status isn't "connected" yet, check if the
        # client is actually authenticated (event callbacks may not fire after
        # whatsmeow's internal 515 reconnect during pairing).
        if status != "connected" and key in self._clients:
            client = self._clients[key]
            try:
                # Check sync .connected attr first
                connected = getattr(client, "connected", False)
                
                # Check logged_in as fallback
                logged_in = False
                try:
                    # is_logged_in is a PROPERTY on neonize NewAClient
                    # (verified 2026-05-20). Drop the parens; getter may
                    # return bool or awaitable. Mirrors PR #599's
                    # heartbeat fix.
                    res = client.is_logged_in
                    if inspect.isawaitable(res):
                        logged_in = await asyncio.wait_for(res, timeout=2)
                    else:
                        logged_in = bool(res)
                except Exception as e:
                    logger.debug(f"is_logged_in check failed: {e}")
                    pass
                    
                logger.info(f"Active detection probe for {key}: status={status}, connected={connected}, logged_in={logged_in}")
                
                if connected:
                    phone = None
                    try:
                        me = await asyncio.wait_for(client.get_me(), timeout=2)
                        phone = me.User if me else None
                    except Exception:
                        pass
                    logger.info(f"Active detection: {key} is connected as {phone}")
                    status = "connected"
                    self._statuses[key] = "connected"
                    self._qr_codes.pop(key, None)
                    self._update_account_status(tenant_id, account_id, "connected", phone=phone)
                    self._save_session_to_db(tenant_id, account_id)
                elif logged_in:
                    # Authenticated but not fully connected yet
                    if status != "pairing":
                        status = "connecting"
                        self._statuses[key] = "connecting"
            except Exception as e:
                logger.warning(f"Active detection check failed for {key}: {type(e).__name__}: {e}")

        result = {
            "connected": status == "connected",
            "status": status,
        }
        if status == "connecting":
            result["message"] = "Waiting for QR scan"
        # Include fresh QR if still pairing
        if key in self._qr_codes:
            result["qr_data_url"] = self._qr_codes[key]
        return result

    async def get_status(self, tenant_id: str, account_id: str = "default") -> dict:
        key = self._key(tenant_id, account_id)
        in_memory_status = self._statuses.get(key)

        db = self._get_db()
        try:
            acct = self._get_or_create_account(db, tenant_id, account_id)
            return {
                "channel_type": "whatsapp",
                "account_id": account_id,
                "enabled": acct.enabled,
                "status": in_memory_status or acct.status,
                "connected": (in_memory_status or acct.status) == "connected",
                "phone_number": acct.phone_number,
                "dm_policy": acct.dm_policy,
                "allow_from": acct.allow_from,
                "connected_at": acct.connected_at.isoformat() if acct.connected_at else None,
                "last_error": acct.last_error,
            }
        finally:
            db.close()

    async def send_message(
        self, tenant_id: str, account_id: str = "default",
        to: str = "", message: str = "",
    ) -> dict:
        key = self._key(tenant_id, account_id)
        client = self._clients.get(key)
        if not client:
            return {"status": "error", "error": "WhatsApp not connected"}
        if self._statuses.get(key) != "connected":
            return {"status": "error", "error": f"WhatsApp status: {self._statuses.get(key)}"}

        # Normalize phone number (strip + prefix)
        phone = to.lstrip("+")
        jid = build_jid(phone)

        try:
            resp = await client.send_message(jid, message)
            # Track sent message ID to avoid echo loop
            if resp and hasattr(resp, 'ID') and resp.ID:
                sent_ids = self._sent_message_ids.setdefault(key, set())
                sent_ids.add(resp.ID)
                if len(sent_ids) > 100:
                    sent_ids.pop()
            self._log_event(
                tenant_id, account_id, "message_outbound",
                direction="outbound", remote_id=phone,
                message_content=message,
            )
            return {"status": "sent", "message_id": resp.ID if resp else None}
        except Exception as e:
            logger.exception(f"Failed to send message for {key}")
            return {"status": "error", "error": str(e)}

    async def logout(self, tenant_id: str, account_id: str = "default") -> dict:
        key = self._key(tenant_id, account_id)
        # Prevent auto-reconnect
        self._statuses[key] = "logged_out"
        # Cancel watchdog + heartbeat
        watchdog = self._watchdog_tasks.pop(key, None)
        if watchdog and not watchdog.done():
            watchdog.cancel()
        hb = self._heartbeat_tasks.pop(key, None)
        if hb and not hb.done():
            hb.cancel()
        client = self._clients.get(key)
        if client:
            try:
                await client.logout()
            except Exception:
                pass
            try:
                await client.disconnect()
            except Exception:
                pass
            self._clients.pop(key, None)
            self._qr_codes.pop(key, None)

        task = self._tasks.pop(key, None)
        if task and not task.done():
            task.cancel()

        # Purge the on-disk neonize SQLite session file so the next
        # start_pairing has no credentials to rehydrate and mints a
        # fresh QR. See disable() for the rationale comment + memory
        # references.
        self._purge_local_session_file(tenant_id, account_id, reason="logout")

        self._statuses[key] = "logged_out"
        self._update_account_status(tenant_id, account_id, "logged_out")
        return {"status": "logged_out"}

    async def reconnect(self, tenant_id: str, account_id: str = "default") -> dict:
        key = self._key(tenant_id, account_id)
        # Cancel existing watchdog
        old_watchdog = self._watchdog_tasks.pop(key, None)
        if old_watchdog and not old_watchdog.done():
            old_watchdog.cancel()
        # Disconnect existing
        if key in self._clients:
            try:
                await self._clients[key].disconnect()
            except Exception:
                pass
            self._clients.pop(key, None)
        task = self._tasks.pop(key, None)
        if task and not task.done():
            task.cancel()

        # Pre-flight: verify WhatsApp is reachable before connect() —
        # neonize Go code panics (kills process) on TLS handshake timeout.
        reachable = await asyncio.get_event_loop().run_in_executor(
            None, self._is_whatsapp_reachable, self.WHATSAPP_CONNECT_TIMEOUT
        )
        if not reachable:
            logger.warning(f"WhatsApp unreachable, skipping reconnect for {key}")
            self._statuses[key] = "disconnected"
            self._update_account_status(tenant_id, account_id, "disconnected",
                                        error="WhatsApp servers unreachable — will retry")
            return {"status": "unreachable"}

        # Reconnect (will restore session from DB if auth state exists)
        client = self._create_client(tenant_id, account_id)
        self._clients[key] = client
        connect_task = await client.connect()
        self._tasks[key] = connect_task
        # Start watchdog for this new connection
        self._watchdog_tasks[key] = asyncio.ensure_future(
            self._connection_watchdog(key, tenant_id, account_id)
        )
        self._update_account_status(tenant_id, account_id, "connecting")
        return {"status": "reconnecting"}

    async def shutdown(self):
        """Gracefully disconnect all clients."""
        for key, task in list(self._watchdog_tasks.items()):
            if not task.done():
                task.cancel()
        self._watchdog_tasks.clear()
        for key, client in list(self._clients.items()):
            try:
                self._statuses[key] = "logged_out"  # Prevent auto-reconnect
                await client.disconnect()
            except Exception:
                pass
        for key, task in list(self._tasks.items()):
            if not task.done():
                task.cancel()
        self._clients.clear()
        self._tasks.clear()
        self._qr_codes.clear()
        self._statuses.clear()
        self._reconnect_counts.clear()
        logger.info("WhatsApp service shut down")

    async def restore_connections(self):
        """On startup, reconnect all enabled accounts that had a connection.

        Neonize keeps auth state in its SQLite DB, so we don't need phone_number
        to be set — the session will auto-restore if the auth state exists on disk.
        """
        db = self._get_db()
        try:
            accounts = (
                db.query(ChannelAccount)
                .filter(
                    ChannelAccount.channel_type == "whatsapp",
                    ChannelAccount.enabled.is_(True),
                    ChannelAccount.status.in_(["connected", "disconnected", "connecting", "pairing"]),
                )
                .all()
            )
            logger.info(f"WhatsApp restore_connections: found {len(accounts)} accounts to restore")
            tasks = []
            for acct in accounts:
                tenant_id = str(acct.tenant_id)
                account_id = acct.account_id
                
                async def restore(tid, aid, status, phone):
                    logger.info(f"Restoring WhatsApp connection for {tid}:{aid} (status={status}, phone={phone})")
                    try:
                        # Restore neonize SQLite session from PostgreSQL before reconnecting
                        self._restore_session_from_db(tid, aid)
                        await self.reconnect(tid, aid)
                    except Exception:
                        logger.exception(f"Failed to restore {tid}:{aid}")

                tasks.append(restore(tenant_id, account_id, acct.status, acct.phone_number))
            
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            db.close()


# Lazy singleton — only initialized when first accessed to avoid
# neonize/protobuf import at module load time (protobuf 7.x vs 6.x conflict).
_whatsapp_service_instance = None

def _get_whatsapp_service():
    global _whatsapp_service_instance
    if _whatsapp_service_instance is None:
        _whatsapp_service_instance = WhatsAppService(db_url=settings.DATABASE_URL)
    return _whatsapp_service_instance

class _LazyWhatsAppService:
    """Proxy that defers WhatsAppService instantiation until first attribute access."""
    def __getattr__(self, name):
        return getattr(_get_whatsapp_service(), name)

whatsapp_service = _LazyWhatsAppService()
