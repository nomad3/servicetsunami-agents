import React from 'react';
import { Card, Row, Col, Badge, Button } from 'react-bootstrap';
import { FaPen as Pencil } from 'react-icons/fa';

const ReviewStep = ({ wizardData, onEdit }) => {
  const { template, basicInfo, personality, skills } = wizardData;

  const enabledTools = Object.entries(skills)
    .filter(([_, enabled]) => enabled)
    .map(([tool, _]) => tool);

  const toolNames = {
    sql_query: 'SQL Query Tool',
    data_summary: 'Data Summary Tool',
    calculator: 'Calculator Tool',
    entity_extraction: 'Entity Extraction',
    knowledge_search: 'Knowledge Search',
    lead_scoring: 'Lead Scoring',
  };

  const rubricNames = {
    ai_lead: 'AI Lead Scoring',
    hca_deal: 'M&A Deal Scoring',
    marketing_signal: 'Marketing Signal Scoring',
  };

  const personalityNames = {
    formal: 'Formal & Professional',
    friendly: 'Friendly & Conversational',
    creative: 'Creative & Expressive',
    analytical: 'Analytical & Precise',
  };

  return (
    <div className="review-step">
      <h3 className="mb-2">Review your agent</h3>
      <p className="text-muted mb-4">Double-check everything looks good before creating</p>

      <Row>
        <Col lg={12}>
          {/* Template */}
          <Card className="mb-3">
            <Card.Body>
              <div className="d-flex justify-content-between align-items-start mb-2">
                <h6 className="mb-0">Template</h6>
                <Button variant="link" size="sm" className="p-0" onClick={() => onEdit(1)}>
                  <Pencil size={14} className="me-1" />
                  Edit
                </Button>
              </div>
              <div className="d-flex align-items-center gap-2">
                <span style={{ fontSize: '1.5rem' }}>{basicInfo.avatar || '🤖'}</span>
                <span>{template?.name || 'Custom Agent'}</span>
              </div>
            </Card.Body>
          </Card>

          {/* Basic Info */}
          <Card className="mb-3">
            <Card.Body>
              <div className="d-flex justify-content-between align-items-start mb-2">
                <h6 className="mb-0">Basic Information</h6>
                <Button variant="link" size="sm" className="p-0" onClick={() => onEdit(2)}>
                  <Pencil size={14} className="me-1" />
                  Edit
                </Button>
              </div>
              <div>
                <strong>Name:</strong> {basicInfo.name}
              </div>
              {basicInfo.description && (
                <div className="mt-1">
                  <strong>Description:</strong> {basicInfo.description}
                </div>
              )}
            </Card.Body>
          </Card>

          {/* Personality */}
          <Card className="mb-3">
            <Card.Body>
              <div className="d-flex justify-content-between align-items-start mb-2">
                <h6 className="mb-0">Personality</h6>
                <Button variant="link" size="sm" className="p-0" onClick={() => onEdit(3)}>
                  <Pencil size={14} className="me-1" />
                  Edit
                </Button>
              </div>
              <div>
                <Badge bg="info">{personalityNames[personality.preset]}</Badge>
                <div className="mt-2 small text-muted">
                  Temperature: {personality.temperature.toFixed(1)} • Max tokens: {personality.max_tokens}
                </div>
              </div>
            </Card.Body>
          </Card>

          {/* Skills */}
          <Card className="mb-3">
            <Card.Body>
              <div className="d-flex justify-content-between align-items-start mb-2">
                <h6 className="mb-0">Skills</h6>
                <Button variant="link" size="sm" className="p-0" onClick={() => onEdit(4)}>
                  <Pencil size={14} className="me-1" />
                  Edit
                </Button>
              </div>
              {enabledTools.length > 0 ? (
                <div className="d-flex flex-wrap gap-2">
                  {enabledTools.map((tool) => (
                    <Badge key={tool} bg="primary">
                      {toolNames[tool]}
                    </Badge>
                  ))}
                </div>
              ) : (
                <small className="text-muted">No special tools enabled</small>
              )}
              {wizardData.scoring_rubric && (
                <div className="mt-2 small text-muted">
                  Scoring rubric: <Badge bg="secondary">{rubricNames[wizardData.scoring_rubric] || wizardData.scoring_rubric}</Badge>
                </div>
              )}
            </Card.Body>
          </Card>

        </Col>
      </Row>
    </div>
  );
};

export default ReviewStep;
