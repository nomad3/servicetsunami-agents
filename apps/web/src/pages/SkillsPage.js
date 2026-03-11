import React, { useEffect, useState } from 'react';
import { Badge, Card, Col, Container, Row } from 'react-bootstrap';
import { useTranslation } from 'react-i18next';
import { FaPuzzlePiece } from 'react-icons/fa';
import Layout from '../components/Layout';
import { EmptyState, LoadingSpinner } from '../components/common';
import api from '../services/api';

const SkillsPage = () => {
  const { t } = useTranslation('skills');
  const [skills, setSkills] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    const fetchSkills = async () => {
      try {
        setLoading(true);
        const response = await api.get('/skills/library');
        setSkills(response.data || []);
      } catch (err) {
        console.error('Error fetching file skills:', err);
        setError(t('errors.load'));
      } finally {
        setLoading(false);
      }
    };
    fetchSkills();
  }, [t]);

  return (
    <Layout>
      <Container fluid className="py-4">
        <div className="d-flex align-items-center mb-4 gap-3">
          <FaPuzzlePiece size={28} className="text-primary" />
          <div>
            <h2 className="mb-0">{t('title')}</h2>
            <p className="text-muted mb-0 small">{t('subtitle')}</p>
          </div>
        </div>

        {loading && <LoadingSpinner />}

        {!loading && error && (
          <div className="alert alert-danger">{error}</div>
        )}

        {!loading && !error && skills.length === 0 && (
          <EmptyState
            icon={<FaPuzzlePiece size={48} />}
            title={t('noSkills')}
            description={t('noSkillsDesc')}
          />
        )}

        {!loading && !error && skills.length > 0 && (
          <Row xs={1} md={2} lg={3} className="g-4">
            {skills.map((skill) => (
              <Col key={skill.skill_dir}>
                <Card className="h-100 shadow-sm">
                  <Card.Body>
                    <div className="d-flex align-items-start justify-content-between mb-2">
                      <Card.Title className="mb-0">{skill.name}</Card.Title>
                      <Badge bg="secondary" className="ms-2 text-capitalize">
                        {skill.engine}
                      </Badge>
                    </div>
                    {skill.description && (
                      <Card.Text className="text-muted small">
                        {skill.description}
                      </Card.Text>
                    )}
                    {skill.inputs && skill.inputs.length > 0 && (
                      <div className="mt-3">
                        <small className="text-muted fw-semibold d-block mb-1">
                          {t('inputs')}
                        </small>
                        {skill.inputs.map((input) => (
                          <div
                            key={input.name}
                            className="d-flex align-items-center gap-2 mb-1"
                          >
                            <code className="small">{input.name}</code>
                            <Badge bg={input.required ? 'primary' : 'light'} text={input.required ? 'white' : 'dark'} className="small">
                              {input.required ? t('required') : t('optional')}
                            </Badge>
                            {input.description && (
                              <span className="text-muted small">{input.description}</span>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </Card.Body>
                </Card>
              </Col>
            ))}
          </Row>
        )}
      </Container>
    </Layout>
  );
};

export default SkillsPage;
