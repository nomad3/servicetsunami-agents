import React, { useEffect, useMemo, useState } from "react";
import {
  Button,
  Card,
  Col,
  Container,
  Dropdown,
  Nav,
  Navbar,
  Row,
} from "react-bootstrap";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import AnimatedSection from "./components/common/AnimatedSection";
import CTASection from "./components/marketing/CTASection";
import {
  aiHighlights,
  architectureLayers,
  featureBlocks,
  lakehouseHighlights,
  pipelineHighlights,
  roadmapItems,
} from "./components/marketing/data";
import FeatureDemoSection from "./components/marketing/FeatureDemoSection";
import FeaturesSection from "./components/marketing/FeaturesSection";
import HeroSection from "./components/marketing/HeroSection";
import InteractivePreview from "./components/marketing/InteractivePreview";
import "./LandingPage.css";

const LandingPage = () => {
  const { t, i18n } = useTranslation(["common", "landing"]);
  const navigate = useNavigate();
  const [scrolled, setScrolled] = useState(false);

  const goToRegister = React.useCallback(() => {
    navigate("/register");
  }, [navigate]);

  const goToLogin = React.useCallback(() => {
    navigate("/login");
  }, [navigate]);

  // Handle navbar scroll effect
  useEffect(() => {
    const handleScroll = () => {
      setScrolled(window.scrollY > 50);
    };
    window.addEventListener("scroll", handleScroll);
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  const currentLanguage = (i18n.language || "en").split("-")[0];
  const languageOptions = useMemo(
    () => [
      { code: "en", label: t("common:language.english") },
      { code: "es", label: t("common:language.spanish") },
    ],
    [t, i18n.language],
  );

  const logos = useMemo(
    () => t("landing:logos.items", { returnObjects: true }) || [],
    [t, i18n.language],
  );
  const metricsItems = useMemo(
    () => t("landing:metrics.items", { returnObjects: true }) || [],
    [t, i18n.language],
  );
  const metricsChunks = useMemo(() => {
    if (!Array.isArray(metricsItems)) {
      return [];
    }
    const chunkSize = 3;
    const chunks = [];
    for (let i = 0; i < metricsItems.length; i += chunkSize) {
      chunks.push(metricsItems.slice(i, i + chunkSize));
    }
    return chunks;
  }, [metricsItems]);

  const featureItems = useMemo(
    () =>
      featureBlocks.map(({ key, icon: Icon }) => {
        const definition =
          t(`landing:features.items.${key}`, { returnObjects: true }) || {};
        return {
          key,
          Icon,
          title: definition.title || "",
          description: definition.description || "",
        };
      }),
    [t, i18n.language],
  );

  const lakehousePrimary = useMemo(
    () =>
      lakehouseHighlights.map(({ key, icon: Icon }) => {
        const definition =
          t(`landing:lakehouse.highlights.${key}`, { returnObjects: true }) ||
          {};
        return {
          key,
          Icon,
          title: definition.title || "",
          description: definition.description || "",
        };
      }),
    [t, i18n.language],
  );

  const pipelineItems = useMemo(
    () =>
      pipelineHighlights.map(({ key, icon: Icon }) => {
        const definition =
          t(`landing:lakehouse.secondary.items.${key}`, {
            returnObjects: true,
          }) || {};
        return {
          key,
          Icon,
          title: definition.title || "",
          description: definition.description || "",
        };
      }),
    [t, i18n.language],
  );

  const aiItems = useMemo(
    () =>
      aiHighlights.map(({ key, icon: Icon }) => {
        const definition =
          t(`landing:ai.items.${key}`, { returnObjects: true }) || {};
        return {
          key,
          Icon,
          title: definition.title || "",
          description: definition.description || "",
        };
      }),
    [t, i18n.language],
  );

  const roadmap = useMemo(
    () =>
      roadmapItems.map(({ key, icon: Icon }) => {
        const definition =
          t(`landing:roadmap.items.${key}`, { returnObjects: true }) || {};
        return {
          key,
          Icon,
          title: definition.title || "",
          description: definition.description || "",
        };
      }),
    [t, i18n.language],
  );

  const architecture = useMemo(
    () =>
      architectureLayers.map(({ key, icon: Icon }) => {
        const definition =
          t(`landing:architecture.layers.${key}`, { returnObjects: true }) ||
          {};
        return {
          key,
          Icon,
          title: definition.title || "",
          description: definition.description || "",
        };
      }),
    [t, i18n.language],
  );

  const testimonials = useMemo(
    () => t("landing:testimonials.items", { returnObjects: true }) || [],
    [t, i18n.language],
  );

  const handleLanguageChange = (code) => {
    i18n.changeLanguage(code);
  };

  return (
    <div>
      <Navbar
        expand="lg"
        fixed="top"
        className={`nav-dark py-3 ${scrolled ? "scrolled" : ""}`}
      >
        <Container>
          <Navbar.Brand href="#hero" className="fw-semibold text-white">
            {t("common:brand")}
          </Navbar.Brand>
          <Navbar.Toggle aria-controls="primary-nav" className="border-0" />
          <Navbar.Collapse id="primary-nav">
            <Nav className="ms-auto align-items-lg-center gap-lg-4">
              <Nav.Link href="#features" className="mx-2">
                {t("common:nav.platform")}
              </Nav.Link>
              <Nav.Link href="#architecture" className="mx-2">
                {t("common:nav.architecture")}
              </Nav.Link>
              <Nav.Link href="#stories" className="mx-2">
                {t("common:nav.customers")}
              </Nav.Link>
              <Nav.Link href="#cta" className="mx-2">
                {t("common:nav.pricing")}
              </Nav.Link>
              <Nav.Link href="#roadmap" className="mx-2">
                Roadmap
              </Nav.Link>
              <Dropdown align="end">
                <Dropdown.Toggle
                  variant="outline-light"
                  size="sm"
                  className="ms-lg-2 text-uppercase"
                  id="landing-language-switch"
                >
                  {currentLanguage.toUpperCase()}
                </Dropdown.Toggle>
                <Dropdown.Menu>
                  {languageOptions.map(({ code, label }) => (
                    <Dropdown.Item
                      key={code}
                      active={currentLanguage === code}
                      onClick={() => handleLanguageChange(code)}
                    >
                      {label}
                    </Dropdown.Item>
                  ))}
                </Dropdown.Menu>
              </Dropdown>
              <Button onClick={goToRegister} className="ms-lg-4 px-4 py-2">
                {t("common:cta.startFree")}
              </Button>
            </Nav>
          </Navbar.Collapse>
        </Container>
      </Navbar>

      <main>
        <HeroSection onPrimaryCta={goToRegister} onSecondaryCta={goToLogin} />
        <InteractivePreview />
        <FeatureDemoSection />
        <FeaturesSection />
        <CTASection />

        <section id="logos" className="section-thin">
          <Container>
            <AnimatedSection animation="fade-in">
              <div className="d-flex flex-wrap justify-content-center align-items-center gap-3 text-uppercase text-soft">
                <span className="fw-semibold me-2">
                  {t("landing:logos.heading")}
                </span>
                {logos.map((name, index) => (
                  <AnimatedSection
                    key={name}
                    animation="scale-up"
                    delay={100 * index}
                    className="d-inline-block"
                  >
                    <span className="logo-badge">{name}</span>
                  </AnimatedSection>
                ))}
              </div>
            </AnimatedSection>
          </Container>
        </section>

        <section
          className="section-wrapper section-ink metrics-section section-separator"
          id="metrics"
        >
          <Container>
            {metricsChunks.map((group, index) => (
              <Row
                className="g-4 justify-content-center"
                key={`metrics-${index}`}
              >
                {group.map((metric, metricIndex) => (
                  <Col md={4} key={metric.label}>
                    <AnimatedSection
                      animation="slide-up"
                      delay={metricIndex * 150}
                    >
                      <div className="metric-tile h-100">
                        <div className="text-uppercase text-sm text-soft fw-semibold tracking-wide">
                          {metric.label}
                        </div>
                        <h3 className="display-5 fw-bold mt-2 mb-3">
                          {metric.value}
                        </h3>
                        <p className="text-contrast mb-0">
                          {metric.description}
                        </p>
                      </div>
                    </AnimatedSection>
                  </Col>
                ))}
              </Row>
            ))}
          </Container>
        </section>

        <section id="features" className="section-wrapper section-dark section-separator">
          <Container>
            <AnimatedSection animation="fade-in">
              <div className="text-center mb-5">
                <h2 className="display-4 fw-bold section-heading gradient-text">
                  {t("landing:features.heading")}
                </h2>
                <p className="lead section-subtitle mt-3">
                  {t("landing:features.subtitle")}
                </p>
              </div>
            </AnimatedSection>
            <Row className="g-4">
              {featureItems.map(({ key, Icon, title, description }, index) => (
                <Col md={4} key={key}>
                  <AnimatedSection animation="scale-up" delay={index * 100}>
                    <Card className="feature-card h-100 p-4 border-0">
                      <div className="icon-pill">
                        <Icon size={28} />
                      </div>
                      <Card.Title className="text-white fw-semibold fs-4">
                        {title}
                      </Card.Title>
                      <Card.Text className="text-soft mt-3">
                        {description}
                      </Card.Text>
                    </Card>
                  </AnimatedSection>
                </Col>
              ))}
            </Row>
          </Container>
        </section>

        <section id="lakehouse" className="section-wrapper section-with-bg bg-data section-separator">
          <Container>
            <Row className="g-5 align-items-center">
              <Col lg={6}>
                <AnimatedSection animation="slide-left">
                  <div className="panel-glass p-5">
                    <h2 className="display-5 fw-bold text-white">
                      {t("landing:lakehouse.heading")}
                    </h2>
                    <div className="gradient-divider my-4" />
                    {lakehousePrimary.map(
                      ({ key, Icon, title, description }) => (
                        <div
                          className="d-flex align-items-start gap-3 mb-3"
                          key={key}
                        >
                          <div className="icon-pill">
                            <Icon size={22} />
                          </div>
                          <div>
                            <h5 className="text-white fw-semibold mb-1">
                              {title}
                            </h5>
                            <p className="mb-0 text-soft">{description}</p>
                          </div>
                        </div>
                      ),
                    )}
                  </div>
                </AnimatedSection>
              </Col>
              <Col lg={6}>
                <AnimatedSection animation="slide-right">
                  <div className="glass-card">
                    <h3 className="fs-3 fw-semibold text-white">
                      {t("landing:lakehouse.secondary.heading")}
                    </h3>
                    <Row className="g-3 mt-4">
                      {pipelineItems.map(
                        ({ key, title, description, Icon }) => (
                          <Col md={6} key={key}>
                            <div className="feature-card p-4 h-100">
                              <Icon size={26} className="text-primary" />
                              <h5 className="text-white fw-semibold mt-3">
                                {title}
                              </h5>
                              <p className="text-soft mb-0">{description}</p>
                            </div>
                          </Col>
                        ),
                      )}
                    </Row>
                  </div>
                </AnimatedSection>
              </Col>
            </Row>
          </Container>
        </section>

        <section id="architecture" className="section-wrapper section-with-bg bg-devops">
          <Container>
            <Row className="g-5 align-items-center">
              <Col lg={5}>
                <AnimatedSection animation="slide-left">
                  <h2 className="display-5 fw-bold text-white">
                    {t("landing:architecture.heading")}
                  </h2>
                </AnimatedSection>
              </Col>
              <Col lg={7}>
                <Row className="g-4">
                  {architecture.map(
                    ({ key, Icon, title, description }, index) => (
                      <Col md={6} key={key}>
                        <AnimatedSection
                          animation="slide-up"
                          delay={index * 100}
                        >
                          <div className="feature-card h-100 p-4">
                            <div className="icon-pill">
                              <Icon size={26} />
                            </div>
                            <h5 className="text-white fw-semibold mt-2">
                              {title}
                            </h5>
                            <p className="text-soft mb-0">{description}</p>
                          </div>
                        </AnimatedSection>
                      </Col>
                    ),
                  )}
                </Row>
              </Col>
            </Row>
          </Container>
        </section>

        <section id="ai" className="section-wrapper section-with-bg bg-ai">
          <Container>
            <AnimatedSection animation="fade-in">
              <div className="text-center mb-5">
                <h2 className="display-5 fw-bold text-white">
                  {t("landing:ai.heading")}
                </h2>
                <p className="lead text-soft mt-3">
                  {t("landing:ai.description")}
                </p>
              </div>
            </AnimatedSection>
            <Row className="g-4">
              {aiItems.map(({ key, Icon, title, description }, index) => (
                <Col md={6} lg={4} key={key}>
                  <AnimatedSection animation="scale-up" delay={index * 100}>
                    <div className="feature-card h-100 p-4">
                      <div className="icon-pill">
                        <Icon size={26} />
                      </div>
                      <h5 className="text-white fw-semibold">{title}</h5>
                      <p className="text-soft">{description}</p>
                    </div>
                  </AnimatedSection>
                </Col>
              ))}
            </Row>
          </Container>
        </section>

        <section id="roadmap" className="section-wrapper section-with-bg bg-tech">
          <Container>
            <AnimatedSection animation="fade-in">
              <div className="text-center mb-5">
                <h2 className="display-4 fw-bold gradient-text">
                  {t("landing:roadmap.heading")}
                </h2>
                <p className="section-subtitle">
                  {t("landing:roadmap.subtitle")}
                </p>
              </div>
            </AnimatedSection>
            <Row className="g-4">
              {roadmap.map(({ key, Icon, title, description }, index) => (
                <Col md={4} key={key}>
                  <AnimatedSection animation="rotate-in" delay={index * 150}>
                    <div className="feature-card h-100 p-4">
                      <div className="icon-pill">
                        <Icon size={26} />
                      </div>
                      <h5 className="text-white fw-semibold">{title}</h5>
                      <p className="text-contrast">{description}</p>
                    </div>
                  </AnimatedSection>
                </Col>
              ))}
            </Row>
          </Container>
        </section>

        <section id="stories" className="section-wrapper section-with-bg bg-people">
          <Container>
            <AnimatedSection animation="fade-in">
              <div className="text-center mb-5">
                <h2 className="display-5 fw-bold gradient-text">
                  {t("landing:testimonials.heading")}
                </h2>
                <p className="text-soft fs-5 mt-3">
                  {t("landing:testimonials.subtitle")}
                </p>
              </div>
            </AnimatedSection>
            <Row className="g-4">
              {testimonials.map(({ quote, author, role }, index) => (
                <Col md={6} key={author}>
                  <AnimatedSection
                    animation={index % 2 === 0 ? "slide-left" : "slide-right"}
                    delay={index * 100}
                  >
                    <div className="feature-card testimonial-card p-4 h-100">
                      <p className="fs-5 text-contrast mb-4">"{quote}"</p>
                      <div className="mt-auto">
                        <div className="fw-semibold text-white fs-6">
                          {author}
                        </div>
                        <div className="text-soft small mt-1">{role}</div>
                      </div>
                    </div>
                  </AnimatedSection>
                </Col>
              ))}
            </Row>
          </Container>
        </section>

        <section id="memory" className="section-wrapper section-dark section-separator">
          <Container>
            <AnimatedSection animation="fade-in">
              <Row className="align-items-center g-5">
                <Col lg={7} className="text-center text-lg-start">
                  <h2 className="display-5 fw-bold gradient-text">
                    {t("landing:memory.heading")}
                  </h2>
                  <p className="lead text-soft mt-3 mb-4">
                    {t("landing:memory.description")}
                  </p>
                  <div className="d-flex flex-wrap gap-3 justify-content-center justify-content-lg-start">
                    <span className="badge-glow">{t("landing:memory.badges.entityExtraction")}</span>
                    <span className="badge-glow">{t("landing:memory.badges.relationMapping")}</span>
                    <span className="badge-glow">{t("landing:memory.badges.contextualRecall")}</span>
                    <span className="badge-glow">{t("landing:memory.badges.activityFeed")}</span>
                  </div>
                </Col>
                <Col lg={5}>
                  <div className="glass-card p-4">
                    <div className="d-flex justify-content-between align-items-center mb-3">
                      <h5 className="text-white fw-semibold mb-0">{t("landing:memory.overview.title")}</h5>
                      <span className="text-soft small">{t("landing:memory.overview.subtitle")}</span>
                    </div>
                    <div className="d-flex gap-3 mb-3">
                      <div className="flex-fill text-center p-3" style={{background: 'rgba(43,125,233,0.1)', borderRadius: 12}}>
                        <div className="text-white fw-bold fs-4">21</div>
                        <div className="text-soft small">{t("landing:memory.overview.entities")}</div>
                      </div>
                      <div className="flex-fill text-center p-3" style={{background: 'rgba(236,72,153,0.1)', borderRadius: 12}}>
                        <div className="text-white fw-bold fs-4">1</div>
                        <div className="text-soft small">{t("landing:memory.overview.memories")}</div>
                      </div>
                      <div className="flex-fill text-center p-3" style={{background: 'rgba(94,197,176,0.1)', borderRadius: 12}}>
                        <div className="text-white fw-bold fs-4">9</div>
                        <div className="text-soft small">{t("landing:memory.overview.relations")}</div>
                      </div>
                    </div>
                    <div className="text-soft small" style={{borderTop: '1px solid rgba(255,255,255,0.1)', paddingTop: 12}}>
                      <div className="mb-1">{t("landing:memory.overview.event1")}</div>
                      <div className="mb-1">{t("landing:memory.overview.event2")}</div>
                      <div>{t("landing:memory.overview.event3")}</div>
                    </div>
                  </div>
                </Col>
              </Row>
            </AnimatedSection>
          </Container>
        </section>

        <section id="cta" className="section-wrapper">
          <Container>
            <AnimatedSection animation="scale-up">
              <div className="cta-banner shadow-lg">
                <div className="cta-banner-content text-white text-center text-md-start">
                  <Row className="align-items-center">
                    <Col md={8}>
                      <h2 className="display-5 fw-bold gradient-text">
                        {t("landing:cta.heading")}
                      </h2>
                      <p className="mt-3 mb-0 fs-5 text-soft">
                        {t("landing:cta.description")}
                      </p>
                    </Col>
                    <Col md={4} className="mt-4 mt-md-0 text-md-end">
                      <Button
                        size="lg"
                        className="px-5 py-3"
                        onClick={goToRegister}
                      >
                        {t("common:cta.startFree")}
                      </Button>
                    </Col>
                  </Row>
                </div>
              </div>
            </AnimatedSection>
          </Container>
        </section>
      </main>

      <footer className="footer py-4 mt-5">
        <Container className="text-center text-soft">
          {t("common:footer.copyright", {
            year: new Date().getFullYear(),
          })}
        </Container>
      </footer>
    </div>
  );
};

export default LandingPage;
