"""Domain-based branding — returns brand config based on Host header."""
from fastapi import APIRouter, Request

router = APIRouter()

DOMAIN_BRANDING = {
    "wolfpoint.ai": {
        "brand_name": "wolfpoint.ai",
        "logo_url": "/assets/wolfpoint-logo.png",
        "theme": "ocean",
        "tagline": "The Distributed Agent Network",
    },
    "servicetsunami.com": {
        "brand_name": "wolfpoint.ai",
        "logo_url": "/assets/wolfpoint-logo.png",
        "theme": "ocean",
        "tagline": "The Distributed Agent Network",
    },
    "agentprovision.com": {
        "brand_name": "AgentProvision",
        "logo_url": "/assets/agentprovision-logo.png",
        "theme": "ocean",
        "tagline": "Enterprise AI Agent Platform",
    },
}

DEFAULT_BRANDING = DOMAIN_BRANDING["wolfpoint.ai"]


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
