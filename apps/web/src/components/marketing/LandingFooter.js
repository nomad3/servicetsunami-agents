import { useTranslation } from 'react-i18next';
import { FiGithub, FiTwitter, FiLinkedin } from 'react-icons/fi';

export default function LandingFooter() {
  const { t } = useTranslation('landing');
  const year = new Date().getFullYear();

  return (
    <footer className="landing-footer">
      <div className="landing-footer__inner">
        <div className="landing-footer__brand">
          <span className="landing-footer__logo">AgentProvision</span>
          <p className="landing-footer__tagline">{t('footer.tagline')}</p>
        </div>

        <nav className="landing-footer__nav">
          <a href="#platform" className="landing-footer__link">{t('footer.links.platform')}</a>
          <a href="#features" className="landing-footer__link">{t('footer.links.features')}</a>
          {/* TODO: wire real /docs route */}
          <a href="#" className="landing-footer__link" onClick={e => e.preventDefault()}>{t('footer.links.docs')}</a>
          <a href="#" className="landing-footer__link" onClick={e => e.preventDefault()}>{t('footer.links.github')}</a>
        </nav>

        <div className="landing-footer__social">
          <a href="#" className="landing-footer__social-link" aria-label="GitHub" onClick={e => e.preventDefault()}><FiGithub size={20} /></a>
          <a href="#" className="landing-footer__social-link" aria-label="Twitter" onClick={e => e.preventDefault()}><FiTwitter size={20} /></a>
          <a href="#" className="landing-footer__social-link" aria-label="LinkedIn" onClick={e => e.preventDefault()}><FiLinkedin size={20} /></a>
        </div>
      </div>
      <p className="landing-footer__copy">{t('footer.copy', { year })}</p>
    </footer>
  );
}
