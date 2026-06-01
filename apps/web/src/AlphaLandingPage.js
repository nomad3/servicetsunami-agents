/**
 * `alpha.agentprovision.com` landing page — focused on the CLI as a
 * product. Reuses LandingNav + LandingFooter + CTASection from the
 * main agentprovision.com landing, with four alpha-specific sections
 * in between.
 *
 * Wired at `/alpha` on the existing frontend (App.js); the
 * `alpha.agentprovision.com` subdomain points to the same SPA and the
 * router resolves the path. No new Cloudflare tunnel required.
 */
import React from 'react';
import LandingNav from './components/marketing/LandingNav';
import LandingFooter from './components/marketing/LandingFooter';
import CTASection from './components/marketing/CTASection';
import AlphaHero from './components/marketing/alpha/AlphaHero';
import AlphaEngines from './components/marketing/alpha/AlphaEngines';
import AlphaMetrics from './components/marketing/alpha/AlphaMetrics';
import AlphaDifferentiators from './components/marketing/alpha/AlphaDifferentiators';
import AlphaRealityLedger from './components/marketing/alpha/AlphaRealityLedger';
import AlphaCommands from './components/marketing/alpha/AlphaCommands';
import AlphaPlatformPower from './components/marketing/alpha/AlphaPlatformPower';
import './LandingPage.css'; // shared design tokens + nav/footer/cta styles
import './AlphaLandingPage.css';

// Reused shared components are parameterized so dead anchors and
// subdomain-broken auth flows don't ship on the alpha page.
// PR #450 review BLOCKER B1 + IMPORTANT I1.
//
// `register` + `signIn` CTAs land on the apex (agentprovision.com)
// regardless of which subdomain the user came from. Auth flows POST
// to relative /api/v1/auth/* paths; only the apex has that route in
// cloudflared ingress. Sending alpha visitors to the apex auth surface
// avoids needing to add /api/* to the alpha subdomain's tunnel rules.
const APEX_REGISTER = 'https://agentprovision.com/register';
const APEX_SIGNIN = 'https://agentprovision.com/login';

// Anchors that actually exist on this page. The shared LandingNav and
// LandingFooter default to the main landing's set; we pass our own so
// clicks don't scroll to nowhere. `engines` leads the set — it's the
// spine of the 2026-05-31 redesign (the four-engine substrate).
const ALPHA_NAV_LINKS = ['engines', 'differentiators', 'reality', 'commands', 'platform'];
const ALPHA_FOOTER_LINKS = [
  { key: 'engines', href: '#engines' },
  { key: 'differentiators', href: '#differentiators' },
  { key: 'reality', href: '#reality' },
  { key: 'commands', href: '#commands' },
  { key: 'platform', href: '#platform' },
  // GitHub link is a real external href, not a fake anchor.
  {
    key: 'github',
    href: 'https://github.com/nomad3/agentprovision-agents/tree/main/apps/agentprovision-cli',
  },
];

export default function AlphaLandingPage() {
  return (
    <>
      <LandingNav
        links={ALPHA_NAV_LINKS}
        registerHref={APEX_REGISTER}
        signInHref={APEX_SIGNIN}
      />
      <main className="alpha-landing">
        <AlphaHero />
        <AlphaEngines />
        <AlphaMetrics />
        <AlphaDifferentiators />
        <AlphaRealityLedger />
        <AlphaCommands />
        <AlphaPlatformPower />
        <CTASection registerHref={APEX_REGISTER} />
      </main>
      <LandingFooter links={ALPHA_FOOTER_LINKS} />
    </>
  );
}
