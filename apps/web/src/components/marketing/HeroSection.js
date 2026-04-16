import { Button, Col, Container, Row } from "react-bootstrap";
import { FaArrowRight } from "react-icons/fa";
import { useTranslation } from "react-i18next";
import NeuralCanvas from "../common/NeuralCanvas";

const noop = () => { };

const HeroSection = ({ onPrimaryCta = noop, onSecondaryCta = noop }) => {
  const { t } = useTranslation(["landing", "common"]);

  return (
    <section className="hero-section pt-5 pb-4" id="hero">
      <div className="hero-bg-enterprise" />
      <NeuralCanvas />
      <div className="hero-overlay" />
      <Container className="hero-content py-5">
        <Row className="align-items-center gy-5">
          <Col lg={6} className="text-center text-lg-start pe-lg-5">
            <span className="badge-glow">{t('hero.badge')}</span>
            <h1 className="display-2 fw-bold mt-4 mb-3 section-heading">
              {t('hero.title')}
            </h1>
            <div className="d-flex flex-column flex-md-row gap-3 justify-content-center justify-content-lg-start mt-4">
              <Button size="lg" className="ap-btn-primary px-5 py-3" onClick={onPrimaryCta}>
                {t('common:cta.startFree', 'Start Free')}
              </Button>
              <Button
                size="lg"
                className="ap-btn-secondary px-5 py-3"
                onClick={onSecondaryCta}
              >
                {t('common:cta.signIn', 'Sign In')}
                <FaArrowRight className="ms-2" size={14} />
              </Button>
            </div>
          </Col>
          <Col lg={6} className="text-center">
            <p className="lead text-soft mb-0" style={{ fontSize: '1.15rem', lineHeight: 1.7 }}>
              {t('hero.lead')}
            </p>
            <style>{`
              @keyframes luna-hero-pulse {
                0%, 100% { opacity: 0.7; transform: translate(-50%, -50%) scale(1); }
                50% { opacity: 1; transform: translate(-50%, -50%) scale(1.06); }
              }
            `}</style>
          </Col>
        </Row>
      </Container>
    </section>
  );
};

export default HeroSection;
