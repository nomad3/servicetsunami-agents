"""Domain-based branding — returns brand config based on Host header."""
from fastapi import APIRouter, Request

router = APIRouter()

DOMAIN_BRANDING = {
    "servicetsunami.com": {
        "brand_name": "ServiceTsunami",
        "logo_url": "/assets/servicetsunami-logo.png",
        "theme": "ocean",
        "tagline": "AI Agent Orchestration Platform",
    },
    "agentprovision.com": {
        "brand_name": "AgentProvision",
        "logo_url": "/assets/agentprovision-logo.png",
        "theme": "ocean",
        "tagline": "Enterprise AI Agent Platform",
    },
}

DEFAULT_BRANDING = DOMAIN_BRANDING["servicetsunami.com"]


@router.get("/domain-branding")
def get_branding(request: Request):
    """Return branding config for the current domain. No auth required."""
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or ""
    ).split(":")[0].lower()

    if host.startswith("www."):
        host = host[4:]

    return DOMAIN_BRANDING.get(host, DEFAULT_BRANDING)
