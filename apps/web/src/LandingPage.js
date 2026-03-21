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
          <Navbar.Brand href="#hero" className="fw-semibold text-white d-flex align-items-center gap-2">
            <img src={`${process.env.PUBLIC_URL}/assets/brand/wolf-icon.png`} alt="" width={32} height={32} style={{ borderRadius: 6 }} />
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
        <FeaturesSection />
        <FeatureDemoSection />

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
