import { useTranslation } from 'react-i18next';

const logos = [
  { id: 'google', name: 'Google' },
  { id: 'github', name: 'GitHub' },
  { id: 'meta', name: 'Meta Ads' },
  { id: 'whatsapp', name: 'WhatsApp' },
  { id: 'jira', name: 'Jira' },
  { id: 'gmail', name: 'Gmail' },
  { id: 'google-calendar', name: 'Google Calendar' },
  { id: 'tiktok', name: 'TikTok' },
  { id: 'slack', name: 'Slack' },
  { id: 'huggingface', name: 'HuggingFace' },
  { id: 'postgresql', name: 'PostgreSQL' },
  { id: 'redis', name: 'Redis' },
];

function LogoRow({ direction }) {
  const items = [...logos, ...logos];
  return (
    <div className={`marquee-row marquee-row--${direction}`} aria-hidden="true">
      <div className="marquee-track">
        {items.map((logo, i) => (
          <img
            key={`${logo.id}-${i}`}
            src={`${process.env.PUBLIC_URL}/logos/integrations/${logo.id}.svg`}
            alt={logo.name}
            className="marquee-logo"
            loading="lazy"
          />
        ))}
      </div>
    </div>
  );
}

export default function IntegrationsMarquee() {
  const { t } = useTranslation('landing');
  return (
    <section className="integrations-showcase" id="integrations">
      <div className="integrations-showcase__inner">
        <h2 className="integrations-showcase__heading">
          {t('integrations.headline')}
        </h2>
      </div>
      <div className="marquee-container">
        <LogoRow direction="left" />
        <LogoRow direction="right" />
        <div className="marquee-fade marquee-fade--left" />
        <div className="marquee-fade marquee-fade--right" />
      </div>
    </section>
  );
}
