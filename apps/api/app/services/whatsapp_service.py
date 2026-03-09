"""
WhatsApp channel service using neonize (whatsmeow Go backend).
Manages per-tenant WhatsApp Web sessions directly in the FastAPI process.
"""
import asyncio
import base64
import io
import inspect
import logging
import uuid
from datetime import datetime
from typing import Dict, Optional

import segno
from neonize.aioze.client import NewAClient
from neonize.aioze.events import (
    ConnectedEv,
    DisconnectedEv,
    LoggedOutEv,
    MessageEv,
    PairStatusEv,
)
from neonize.utils import build_jid
from neonize.utils.enum import ChatPresence, ChatPresenceMedia
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.channel_account import ChannelAccount
from app.models.channel_event import ChannelEvent
from app.models.chat import ChatSession

logger = logging.getLogger(__name__)


class WhatsAppService:
    """Manages neonize WhatsApp clients per tenant:account."""

    MAX_RECONNECT_ATTEMPTS = 5
    RECONNECT_BASE_DELAY = 2  # seconds, doubles each attempt

    def __init__(self, db_url: str):
        self._clients: Dict[str, NewAClient] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        self._watchdog_tasks: Dict[str, asyncio.Task] = {}
        self._qr_codes: Dict[str, str] = {}
        self._statuses: Dict[str, str] = {}
        self._reconnect_counts: Dict[str, int] = {}
        self._sent_message_ids: Dict[str, set] = {}  # Track bot-sent msg IDs to avoid echo loops
        self._db_url = db_url

    def _key(self, tenant_id: str, account_id: str = "default") -> str:
        return f"{tenant_id}:{account_id}"

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
            self._reconnect_counts[key] = 0  # Reset on successful connection
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
            self._save_session_to_db(tenant_id, account_id)

        # Disconnected — save session (keys may have rotated), then auto-reconnect
        @client.event(DisconnectedEv)
        async def on_disconnected(c: NewAClient, event: DisconnectedEv):
            logger.warning(f"DisconnectedEv for {key}")
            self._save_session_to_db(tenant_id, account_id)
            self._statuses[key] = "disconnected"
            self._update_account_status(tenant_id, account_id, "disconnected")
            self._log_event(tenant_id, account_id, "connection_closed")
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
            await self.reconnect(tenant_id, account_id)
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

        if not text:
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
        except Exception as e:
            logger.debug(f"LID→phone resolution failed for {sender_jid}: {e}")

        # DM policy enforcement
        db = self._get_db()
        try:
            acct = self._get_or_create_account(db, tenant_id, account_id)
            if acct.dm_policy == "allowlist":
                allowed = acct.allow_from or []
                if "*" not in allowed:
                    # Check both the raw JID and resolved phone against allowlist
                    matches = (
                        sender_jid in allowed
                        or f"+{sender_jid}" in allowed
                        or sender_phone in allowed
                        or f"+{sender_phone}" in allowed
                    )
                    if not matches:
                        logger.info(f"Blocked message from {sender_jid} (phone={sender_phone}, not in allowlist {allowed})")
                        return
        finally:
            db.close()

        logger.info(f"Inbound DM from {sender_phone} (jid={sender_jid}) in {key}: {text[:100]}")
        self._log_event(
            tenant_id, account_id, "message_inbound",
            direction="inbound", remote_id=sender_phone,
            message_content=text,
            extra_data={"chat_jid": chat_jid, "is_group": is_group},
        )

        # Show "typing..." indicator while processing
        try:
            reply_jid = build_jid(sender_phone)
            await client.send_chat_presence(
                reply_jid,
                ChatPresence.CHAT_PRESENCE_COMPOSING,
                ChatPresenceMedia.CHAT_PRESENCE_MEDIA_TEXT,
            )
        except Exception:
            logger.debug(f"Failed to send composing presence for {sender_phone}")

        # Process through agent — use phone number (not LID) as session key
        response_text = await self._process_through_agent(tenant_id, sender_phone, text)
        if not response_text:
            logger.warning(f"Empty response from agent for {sender_phone}, not sending reply")
        if response_text:
            try:
                # Build clean JID without device part — neonize requires user-only JID for sending
                reply_jid = build_jid(sender_phone)

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

    async def _process_through_agent(
        self, tenant_id: str, sender_id: str, message: str,
    ) -> Optional[str]:
        """Route inbound message through the same ADK agent pipeline as the chat UI.

        This ensures WhatsApp conversations share the same supervisor, agent kits,
        LLM provider, conversation history, and Temporal workflow audit trail.
        """
        db = self._get_db()
        try:
            from app.services import chat as chat_service
            from app.models.agent_kit import AgentKit
            from app.models.user import User

            tid = uuid.UUID(tenant_id)

            # Find the tenant's admin user (needed for ADK session context)
            user = db.query(User).filter(User.tenant_id == tid).first()
            if not user:
                logger.error(f"No user found for tenant {tenant_id}")
                return None

            # Find the tenant's first agent kit (or a WhatsApp-specific one)
            agent_kit = (
                db.query(AgentKit)
                .filter(AgentKit.tenant_id == tid)
                .first()
            )
            if not agent_kit:
                logger.warning(f"No agent kit found for tenant {tenant_id}")
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
                    agent_kit_id=agent_kit.id,
                    source="whatsapp",
                    external_id=session_key,
                )
                db.add(session)
                db.commit()
                db.refresh(session)
            elif not session.agent_kit_id:
                # Backfill agent_kit on existing sessions
                session.agent_kit_id = agent_kit.id
                db.commit()
                db.refresh(session)

            # Route through the same chat service as the web UI
            # This calls ADK supervisor → agent selection → LLM → tools → audit
            # Wrapper captures content string eagerly in the thread (avoids
            # SQLAlchemy lazy-loading issues when crossing the thread boundary)
            def _run_chat():
                _user_msg, assistant_msg = chat_service.post_user_message(
                    db,
                    session=session,
                    user_id=user.id,
                    content=message,
                    sender_phone=sender_id,
                )
                # Eagerly capture content before leaving the thread
                return assistant_msg.content if assistant_msg else None

            response = await asyncio.to_thread(_run_chat)
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
        # Cancel watchdog
        watchdog = self._watchdog_tasks.pop(key, None)
        if watchdog and not watchdog.done():
            watchdog.cancel()
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
                        # Call is_logged_in and await if it returns an awaitable
                        res = client.is_logged_in()
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
                    # Call is_logged_in and await if it returns an awaitable
                    res = client.is_logged_in()
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
        # Cancel watchdog
        watchdog = self._watchdog_tasks.pop(key, None)
        if watchdog and not watchdog.done():
            watchdog.cancel()
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


# Singleton instance — initialized with DATABASE_URL
whatsapp_service = WhatsAppService(db_url=settings.DATABASE_URL)
