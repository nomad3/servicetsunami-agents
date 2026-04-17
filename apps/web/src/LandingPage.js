import React from 'react';
import LandingNav from './components/marketing/LandingNav';
import HeroSection from './components/marketing/HeroSection';
import ProductDemo from './components/marketing/ProductDemo';
import BentoGrid from './components/marketing/BentoGrid';
import MetricsStrip from './components/marketing/MetricsStrip';
import IntegrationsMarquee from './components/marketing/IntegrationsMarquee';
import CTASection from './components/marketing/CTASection';
import LandingFooter from './components/marketing/LandingFooter';
import './LandingPage.css';

export default function LandingPage() {
  return (
    <>
      <LandingNav />
      <main>
        <HeroSection />
        <ProductDemo />
        <BentoGrid />
        <MetricsStrip />
        <IntegrationsMarquee />
        <CTASection />
      </main>
      <LandingFooter />
    </>
  );
}
