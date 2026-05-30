/**
 * `vet.agentprovision.com` landing page — the operating system for
 * veterinary practices. Reuses LandingNav + CTASection + LandingFooter
 * from the main agentprovision.com landing (all prop-driven), with five
 * vet-specific sections in between.
 *
 * Positioning is Luna-led: a practice OS (shared state, workflow
 * orchestration, an agent fleet, approval gates, audit trail) — NOT an
 * "AI veterinarian", NOT a chatbot wrapper. A licensed human approves
 * every clinical and financial decision. Cardiology is one depth
 * example, never the headline.
 *
 * Wired at `/vet` on the existing frontend (App.js); the
 * vet.agentprovision.com subdomain points to the same SPA and the root
 * hostname-sniff resolves it. No separate build, no new tunnel — one
 * ingress rule (cloudflared/config.yml + kubernetes/cloudflared-
 * deployment.yaml, kept in sync) routes vet.* → http://web:80.
 */
import React from 'react';
import LandingNav from './components/marketing/LandingNav';
import LandingFooter from './components/marketing/LandingFooter';
import CTASection from './components/marketing/CTASection';
import VetHero from './components/marketing/vet/VetHero';
import VetConnectors from './components/marketing/vet/VetConnectors';
import VetAgentFleet from './components/marketing/vet/VetAgentFleet';
import VetTrust from './components/marketing/vet/VetTrust';
import VetCardiologyShowcase from './components/marketing/vet/VetCardiologyShowcase';
import './LandingPage.css'; // shared design tokens + nav/footer/cta styles
import './VetLandingPage.css';

// Reused shared components are parameterized so dead anchors and
// subdomain-broken auth flows don't ship on the vet page. Mirrors the
// AlphaLandingPage approach (PR #450 BLOCKER B1 + IMPORTANT I1).
//
// register + signIn CTAs land on the apex (agentprovision.com) because
// auth flows POST to relative /api/v1/auth/* paths and only the apex
// has that route in cloudflared ingress.
const APEX_REGISTER = 'https://agentprovision.com/register';
const APEX_SIGNIN = 'https://agentprovision.com/login';

// Anchors that actually exist on this page. The shared LandingNav and
// LandingFooter default to the main landing's set; we pass our own so
// clicks scroll to real sections. Keys resolve via t('nav.${key}') /
// t('footer.links.${key}') — added to landing.json (en + es).
//
// i18n scope: the reused nav + footer resolve through t('nav.*') /
// t('footer.links.*') and keep their en + es keys. The five vet-specific
// section components (VetHero, VetConnectors, VetAgentFleet, VetTrust,
// VetCardiologyShowcase) hardcode English copy on purpose —
// English-only for launch; section-body i18n deferred.
const VET_NAV_LINKS = ['connectors', 'fleet', 'trust', 'cardiology'];
const VET_FOOTER_LINKS = [
  { key: 'connectors', href: '#connectors' },
  { key: 'fleet', href: '#fleet' },
  { key: 'trust', href: '#trust' },
  { key: 'cardiology', href: '#cardiology' },
];

export default function VetLandingPage() {
  return (
    <>
      <LandingNav
        links={VET_NAV_LINKS}
        registerHref={APEX_REGISTER}
        signInHref={APEX_SIGNIN}
      />
      <main className="vet-landing">
        <VetHero />
        <VetConnectors />
        <VetAgentFleet />
        <VetTrust />
        <VetCardiologyShowcase />
        <CTASection
          registerHref={APEX_REGISTER}
          title="Run your practice from one source-traceable record."
          subtitle="Connect your systems, coordinate your agent fleet, and keep every clinical and financial decision under human approval."
          buttonText="Request a vet-OS demo"
        />
      </main>
      <LandingFooter links={VET_FOOTER_LINKS} />
    </>
  );
}
