"""OAuthHandshakeWorkflow — Phase D scaffold (#295).

Per docs/plans/2026-05-18-docker-image-shrink-and-latency.md §Phase D
("npm CLIs in api — CORRECTED"). The full Phase D refactor moves
OAuth handshake handling (gemini_cli_auth, claude_auth, codex_auth,
higgsfield_auth) out of the api container and into the code-worker
where the rest of the CLI execution surface lives. This drops the
npm CLIs + Node toolchain from the api Dockerfile.

Phase D ships in multiple PRs:

  D-1 (this scaffold): workflow class + activity stubs + worker
       registration. Production OAuth path stays unchanged —
       handshakes still run via subprocess.run in
       apps/api/app/api/v1/{gemini_cli,claude,codex,higgsfield}_auth.py.

  D-2: code-worker side — implement the activities to actually
       shell out to the bundled CLIs there.

  D-3: api side — flip each OAuth handler to dispatch
       OAuthHandshakeWorkflow instead of subprocess.run. Feature-
       flag per provider (OAUTH_DISPATCH_MODE_GEMINI=subprocess|workflow
       etc.) so each provider can ramp independently.

  D-4: drop the npm CLI install from apps/api/Dockerfile. Frees
       the ~1 GB the original "Phase D" framing wanted to recover.

Until D-2 + D-3 land the workflow returns a noop dict and the
production path is byte-identical to today's. Same scaffold-first
pattern used for O2 (#631) and SkillEvalIterationWorkflow (#294).

Why this pattern: Phase D is genuinely a multi-PR project; the
shape of the workflow + activity boundary needs to ship before
the api-side cutover can reference it. With this scaffold the
worker is ready, the contract is locked, and D-2/D-3/D-4 can land
provider-by-provider without coordinated cross-repo flips.
"""
from datetime import timedelta
from typing import Any, Dict

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from app.workflows.activities.oauth_handshake_activities import (
        run_oauth_handshake,
    )


_HANDSHAKE_TIMEOUT = timedelta(minutes=5)


@workflow.defn
class OAuthHandshakeWorkflow:
    @workflow.run
    async def run(
        self,
        provider: str,
        tenant_id: str,
        code: str,
        code_verifier: str,
        redirect_uri: str,
    ) -> Dict[str, Any]:
        """Run a provider OAuth code exchange on the code-worker.

        Args:
            provider: 'gemini_cli' | 'claude' | 'codex' | 'higgsfield'.
            tenant_id: tenant scope — every token write is filtered by
                this. The code-worker activity MUST refuse to write
                under a different tenant.
            code: OAuth authorization code from the user-facing flow.
            code_verifier: PKCE S256 verifier the api generated.
            redirect_uri: the redirect URI Google/Anthropic/OpenAI/
                Higgsfield was given when the auth URL was minted.

        Returns:
            ``{
                "provider": str,
                "tenant_id": str,
                "access_token_stored": bool,
                "refresh_token_stored": bool,
                "expires_in": Optional[int],
            }``

        Phase D-1 stub: returns success=False so the api-side
        callers keep using their existing subprocess.run path until
        D-3 flips the env flag.
        """
        result = await workflow.execute_activity(
            run_oauth_handshake,
            args=[provider, tenant_id, code, code_verifier, redirect_uri],
            start_to_close_timeout=_HANDSHAKE_TIMEOUT,
        )
        return result
