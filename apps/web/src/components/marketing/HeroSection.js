import { Button, Col, Container, Row } from "react-bootstrap";
import { FaArrowRight } from "react-icons/fa";
import { useTranslation } from "react-i18next";
import NeuralCanvas from "../common/NeuralCanvas";

const noop = () => { };

const HeroSection = ({ onPrimaryCta = noop, onSecondaryCta = noop }) => {
  const { t } = useTranslation(["landing", "common"]);

  return (
    <section className="hero-section pt-5 pb-4" id="hero">
      <div className="hero-wolf-bg" style={{ backgroundImage: `url(${process.env.PUBLIC_URL}/assets/brand/wolf-hero-bg.png)` }} />
      <NeuralCanvas />
      <div className="hero-overlay" />
      <Container className="hero-content py-5">
        <Row className="align-items-center gy-5">
          <Col lg={6} className="text-center text-lg-start pe-lg-5">
            <span className="badge-glow">{t('hero.badge')}</span>
            <h1 className="display-2 fw-bold mt-4 mb-3 section-heading">
              {t('hero.title')}
            </h1>
            <p className="lead text-soft mb-4" style={{ fontSize: '1.15rem', lineHeight: 1.7 }}>
              {t('hero.lead')}
            </p>
            <div className="d-flex flex-column flex-md-row gap-3 justify-content-center justify-content-lg-start mt-4">
              <Button size="lg" className="wolf-btn-primary px-5 py-3" onClick={onPrimaryCta}>
                {t('common:cta.startFree', 'Start Free')}
              </Button>
              <Button
                size="lg"
                className="wolf-btn-secondary px-5 py-3"
                onClick={onSecondaryCta}
              >
                {t('common:cta.signIn', 'Sign In')}
                <FaArrowRight className="ms-2" size={14} />
              </Button>
            </div>
          </Col>
          <Col lg={6} className="text-center">
            <p className="text-soft mt-3" style={{ fontSize: '0.95rem', opacity: 0.7 }}>
              {t('hero.subtext')}
            </p>
          </Col>
        </Row>
      </Container>
    </section>
  );
};

export default HeroSection;
