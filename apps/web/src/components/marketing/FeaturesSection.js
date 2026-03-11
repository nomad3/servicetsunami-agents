import React from "react";
import { Container, Row, Col } from "react-bootstrap";
import { useTranslation } from "react-i18next";
import {
  FaRobot,
  FaBrain,
  FaComments,
  FaPlug,
  FaCogs,
  FaShieldAlt,
  FaCode,
  FaBullhorn,
  FaEnvelopeOpenText,
} from "react-icons/fa";

const featureKeys = [
  { key: "multiAgentTeams", icon: FaRobot },
  { key: "persistentMemory", icon: FaBrain },
  { key: "aiChat", icon: FaComments },
  { key: "oauthIntegrations", icon: FaPlug },
  { key: "durableWorkflows", icon: FaCogs },
  { key: "enterpriseSecurity", icon: FaShieldAlt },
  { key: "autonomousCodeAgent", icon: FaCode },
  { key: "marketingIntelligence", icon: FaBullhorn },
  { key: "proactiveInboxMonitor", icon: FaEnvelopeOpenText },
];

const FeaturesSection = () => {
  const { t } = useTranslation(["landing"]);

  return (
    <section className="features-section py-5">
      <Container>
        <Row className="text-center mb-5">
          <Col>
            <h2 className="display-5 fw-bold text-white mb-3">
              {t("landing:featuresGrid.heading")}
            </h2>
            <p className="lead text-soft">
              {t("landing:featuresGrid.subtitle")}
            </p>
          </Col>
        </Row>

        <Row className="g-4">
          {featureKeys.map(({ key, icon: Icon }) => (
            <Col md={6} lg={4} key={key} className="mb-4">
              <div className="feature-card text-center">
                <div className="feature-icon">
                  <Icon />
                </div>
                <h4 className="feature-title">
                  {t(`landing:featuresGrid.items.${key}.title`)}
                </h4>
                <p className="feature-description">
                  {t(`landing:featuresGrid.items.${key}.description`)}
                </p>
              </div>
            </Col>
          ))}
        </Row>
      </Container>
    </section>
  );
};

export default FeaturesSection;
