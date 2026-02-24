"""
WhatsApp channel service using neonize (whatsmeow Go backend).
Manages per-tenant WhatsApp Web sessions directly in the FastAPI process.
"""
import asyncio
import base64
import io
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
    StreamReplacedEv,
)
from neonize.utils import build_jid
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.channel_account import ChannelAccount
from app.models.channel_event import ChannelEvent
from app.models.chat import ChatSession, ChatMessage

logger = logging.getLogger(__name__)


class WhatsAppService:
    """Manages neonize WhatsApp clients per tenant:account."""

    def __init__(self, db_url: str):
        self._clients: Dict[str, NewAClient] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        self._qr_codes: Dict[str, str] = {}
        self._statuses: Dict[str, str] = {}
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
            db.commit()
            db.refresh(acct)
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

    # ── Client lifecycle ─────────────────────────────────────────────

    def _create_client(self, tenant_id: str, account_id: str) -> NewAClient:
        """Create a neonize async client with event handlers bound."""
        key = self._key(tenant_id, account_id)
        name = self._client_name(tenant_id, account_id)

        # Always use SQLite for neonize session storage.
        # PostgreSQL URLs with special chars in password break Go's URL parser.
        client = NewAClient(name)

        # Fix event loop: neonize creates its own loop at import time,
        # but we need callbacks on the current running loop (uvicorn's)
        try:
            loop = asyncio.get_running_loop()
            client.loop = loop
            # Also patch the module-level event loop so callbacks dispatch correctly
            import neonize.aioze.client as _neonize_mod
            _neonize_mod.event_global_loop = loop
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
                me = c.get_me()
                if me:
                    phone = me.User
            except Exception:
                pass
            self._update_account_status(tenant_id, account_id, "connected", phone=phone)
            self._log_event(tenant_id, account_id, "paired")

        # Connected
        @client.event(ConnectedEv)
        async def on_connected(c: NewAClient, event: ConnectedEv):
            logger.info(f"ConnectedEv fired for {key}")
            self._statuses[key] = "connected"
            self._qr_codes.pop(key, None)
            phone = None
            try:
                me = c.get_me()
                if me:
                    phone = me.User
            except Exception:
                pass
            self._update_account_status(tenant_id, account_id, "connected", phone=phone)
            self._log_event(tenant_id, account_id, "connection_opened")

        # Disconnected
        @client.event(DisconnectedEv)
        async def on_disconnected(c: NewAClient, event: DisconnectedEv):
            logger.warning(f"DisconnectedEv for {key}")
            self._statuses[key] = "disconnected"
            self._update_account_status(tenant_id, account_id, "disconnected")
            self._log_event(tenant_id, account_id, "connection_closed")

        # Stream replaced (another device took over)
        @client.event(StreamReplacedEv)
        async def on_stream_replaced(c: NewAClient, event: StreamReplacedEv):
            logger.warning(f"StreamReplacedEv for {key} — device was unlinked or replaced")
            self._statuses[key] = "disconnected"
            self._clients.pop(key, None)
            self._qr_codes.pop(key, None)
            self._update_account_status(tenant_id, account_id, "disconnected", error="Stream replaced")

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

    async def _handle_inbound(
        self, key: str, tenant_id: str, account_id: str,
        client: NewAClient, event: MessageEv,
    ):
        """Process an inbound WhatsApp message through agent pipeline."""
        info = event.Info
        msg = event.Message

        sender_jid = info.MessageSource.Sender.User if info.MessageSource.Sender else ""
        chat_jid = info.MessageSource.Chat.User if info.MessageSource.Chat else ""
        is_from_me = info.MessageSource.IsFromMe
        is_group = info.MessageSource.IsGroup
        text = msg.conversation or (msg.extendedTextMessage.text if msg.extendedTextMessage else "")

        if is_from_me or not text:
            return

        # DM policy enforcement
        db = self._get_db()
        try:
            acct = self._get_or_create_account(db, tenant_id, account_id)
            if acct.dm_policy == "allowlist":
                allowed = acct.allow_from or []
                if "*" not in allowed and sender_jid not in allowed and f"+{sender_jid}" not in allowed:
                    logger.info(f"Blocked message from {sender_jid} (not in allowlist)")
                    return
        finally:
            db.close()

        logger.info(f"Inbound from {sender_jid} in {key}: {text[:100]}")
        self._log_event(
            tenant_id, account_id, "message_inbound",
            direction="inbound", remote_id=sender_jid,
            message_content=text,
            extra_data={"chat_jid": chat_jid, "is_group": is_group},
        )

        # Process through agent and send response
        response_text = await self._process_through_agent(tenant_id, sender_jid, text)
        if response_text:
            try:
                jid = build_jid(sender_jid)
                await client.send_message(jid, response_text)
                self._log_event(
                    tenant_id, account_id, "message_outbound",
                    direction="outbound", remote_id=sender_jid,
                    message_content=response_text,
                )
            except Exception:
                logger.exception(f"Failed to send reply to {sender_jid}")

    async def _process_through_agent(
        self, tenant_id: str, sender_id: str, message: str,
    ) -> Optional[str]:
        """Route inbound message through the chat/agent pipeline. Returns response text."""
        db = self._get_db()
        try:
            tid = uuid.UUID(tenant_id)

            # Find or create a channel chat session keyed by sender
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
                    source="whatsapp",
                    external_id=session_key,
                )
                db.add(session)
                db.commit()
                db.refresh(session)

            # Append user message
            user_msg = ChatMessage(
                session_id=session.id,
                role="user",
                content=message,
            )
            db.add(user_msg)
            db.commit()

            # Generate agent response via LLM
            from app.services.llm.service import LLMService
            llm_svc = LLMService(db, tid)

            # Build conversation history from recent messages
            recent_msgs = (
                db.query(ChatMessage)
                .filter(ChatMessage.session_id == session.id)
                .order_by(ChatMessage.created_at.desc())
                .limit(20)
                .all()
            )
            recent_msgs.reverse()

            chat_messages = [
                {"role": "system", "content": (
                    "You are a helpful assistant responding via WhatsApp. "
                    "Keep responses concise and conversational. Use short paragraphs."
                )},
            ]
            for m in recent_msgs:
                chat_messages.append({"role": m.role, "content": m.content})

            response = llm_svc.generate_response(
                messages=chat_messages,
                task_type="conversation",
                max_tokens=1024,
                temperature=0.7,
            )

            assistant_text = response.choices[0].message.content if response and response.choices else None
            if assistant_text:
                assistant_msg = ChatMessage(
                    session_id=session.id,
                    role="assistant",
                    content=assistant_text,
                )
                db.add(assistant_msg)
                db.commit()

            return assistant_text
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
        # Disconnect if active
        if key in self._clients:
            try:
                self._clients[key].disconnect()
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

    async def start_pairing(
        self, tenant_id: str, account_id: str = "default", force: bool = False,
    ) -> dict:
        key = self._key(tenant_id, account_id)

        # If force, disconnect existing client and delete session file
        if force:
            if key in self._clients:
                try:
                    self._clients[key].disconnect()
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

        # Create client and start connection (QR will be emitted via callback)
        client = self._create_client(tenant_id, account_id)
        self._clients[key] = client
        # connect() returns a Task — await to get the actual running connection task
        connect_task = await client.connect()
        self._tasks[key] = connect_task

        # Wait briefly for QR to be generated
        for _ in range(20):
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

        return {
            "qr_data_url": None,
            "message": "QR code not yet available, try polling /pair/status",
        }

    async def get_pairing_status(self, tenant_id: str, account_id: str = "default") -> dict:
        key = self._key(tenant_id, account_id)
        status = self._statuses.get(key, "disconnected")
        result = {
            "connected": status == "connected",
            "status": status,
        }
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
        client = self._clients.get(key)
        if client:
            try:
                client.logout()
            except Exception:
                pass
            try:
                client.disconnect()
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
        # Disconnect existing
        if key in self._clients:
            try:
                self._clients[key].disconnect()
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
        self._update_account_status(tenant_id, account_id, "connecting")
        return {"status": "reconnecting"}

    async def shutdown(self):
        """Gracefully disconnect all clients."""
        for key, client in list(self._clients.items()):
            try:
                client.disconnect()
            except Exception:
                pass
        for key, task in list(self._tasks.items()):
            if not task.done():
                task.cancel()
        self._clients.clear()
        self._tasks.clear()
        self._qr_codes.clear()
        self._statuses.clear()
        logger.info("WhatsApp service shut down")

    async def restore_connections(self):
        """On startup, reconnect only previously-connected accounts (have auth state)."""
        db = self._get_db()
        try:
            # Only restore accounts that were actually connected before shutdown.
            # "connecting" or "pairing" means they never completed auth — skip them.
            accounts = (
                db.query(ChannelAccount)
                .filter(
                    ChannelAccount.channel_type == "whatsapp",
                    ChannelAccount.enabled == True,
                    ChannelAccount.status.in_(["connected", "disconnected"]),
                    ChannelAccount.phone_number.isnot(None),  # had a successful pairing
                )
                .all()
            )
            for acct in accounts:
                tenant_id = str(acct.tenant_id)
                account_id = acct.account_id
                logger.info(f"Restoring WhatsApp connection for {tenant_id}:{account_id}")
                try:
                    await self.reconnect(tenant_id, account_id)
                except Exception:
                    logger.exception(f"Failed to restore {tenant_id}:{account_id}")
        finally:
            db.close()


# Singleton instance — initialized with DATABASE_URL
whatsapp_service = WhatsAppService(db_url=settings.DATABASE_URL)
