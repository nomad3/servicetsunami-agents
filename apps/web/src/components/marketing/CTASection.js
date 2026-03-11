import React from "react";
import { Container, Row, Col, Button } from "react-bootstrap";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { FaArrowRight, FaRocket } from "react-icons/fa";

const CTASection = () => {
  const navigate = useNavigate();
  const { t } = useTranslation(["landing", "common"]);

  return (
    <section className="cta-section py-5">
      <Container>
        <Row className="align-items-center">
          <Col lg={6} className="text-center text-lg-start mb-4 mb-lg-0">
            <h2 className="display-4 fw-bold text-white mb-3">
              {t("landing:ctaBanner.heading")}
            </h2>
            <p className="lead text-soft mb-4">
              {t("landing:ctaBanner.description")}
            </p>
            <div className="d-flex flex-column flex-md-row gap-3 justify-content-center justify-content-lg-start">
              <Button
                size="lg"
                className="px-5 py-3 cta-primary"
                onClick={() => navigate("/register")}
              >
                <FaRocket className="me-2" />
                {t("common:cta.startFree")}
              </Button>
              <Button
                size="lg"
                variant="outline-light"
                className="px-5 py-3 cta-secondary"
                onClick={() => navigate("/login")}
              >
                {t("common:cta.signIn")}
                <FaArrowRight className="ms-2" />
              </Button>
            </div>
            <div className="trust-badges mt-4">
              <span className="badge-item">{t("landing:ctaBanner.badges.multiTenant")}</span>
              <span className="badge-item">{t("landing:ctaBanner.badges.encryptedVault")}</span>
              <span className="badge-item">{t("landing:ctaBanner.badges.kubernetes")}</span>
            </div>
          </Col>
          <Col lg={6}>
            <div className="cta-visual">
              <div className="cta-stats">
                <div className="stat-item">
                  <div className="stat-number">8</div>
                  <div className="stat-label">{t("landing:ctaBanner.stats.agentTeams")}</div>
                </div>
                <div className="stat-item">
                  <div className="stat-number">30+</div>
                  <div className="stat-label">{t("landing:ctaBanner.stats.builtInTools")}</div>
                </div>
                <div className="stat-item">
                  <div className="stat-number">10+</div>
                  <div className="stat-label">{t("landing:ctaBanner.stats.integrations")}</div>
                </div>
              </div>
            </div>
          </Col>
        </Row>
      </Container>
    </section>
  );
};

export default CTASection;
