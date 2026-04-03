import React, { useState, useEffect } from 'react';
import { Card, Row, Col, Button, Badge, Spinner } from 'react-bootstrap';
import { FiDownload, FiEye } from 'react-icons/fi';
import { useNavigate } from 'react-router-dom';
import dynamicWorkflowService from '../../services/dynamicWorkflowService';

const TRIGGER_LABELS = {
  cron: 'Scheduled', interval: 'Interval', webhook: 'Webhook',
  event: 'Event', manual: 'Manual', agent: 'Agent',
};

export default function TemplatesTab() {
  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  useEffect(() => {
    dynamicWorkflowService.browseTemplates()
      .then((data) => setTemplates(data || []))
      .catch(() => setTemplates([]))
      .finally(() => setLoading(false));
  }, []);

  const handleInstall = async (templateId) => {
    try {
      const installed = await dynamicWorkflowService.installTemplate(templateId);
      navigate(`/workflows/builder/${installed.id}`);
    } catch (err) {
      console.error('Install failed:', err);
    }
  };

  if (loading) return <div className="text-center p-4"><Spinner /></div>;

  if (templates.length === 0) {
    return (
      <div className="text-center p-5" style={{ color: '#64748b' }}>
        <h5>No templates available</h5>
        <p>Templates will appear here as native, community, and shared workflows are added.</p>
      </div>
    );
  }

  return (
    <Row xs={1} md={2} lg={3} className="g-3">
      {templates.map((t) => (
        <Col key={t.id}>
          <Card className="h-100" style={{
            background: 'rgba(15, 23, 42, 0.6)', border: '1px solid #1e293b',
            backdropFilter: 'blur(20px)', color: '#e2e8f0',
          }}>
            <Card.Body>
              <Card.Title style={{ fontSize: 14 }}>{t.name}</Card.Title>
              <Card.Text style={{ fontSize: 12, color: '#94a3b8' }}>
                {t.description}
              </Card.Text>
              <div className="d-flex gap-1 flex-wrap">
                <Badge bg="secondary" style={{ fontSize: 10 }}>
                  {TRIGGER_LABELS[t.trigger_config?.type] || 'Manual'}
                </Badge>
                <Badge bg="info" style={{ fontSize: 10 }}>
                  {(t.definition?.steps || []).length} steps
                </Badge>
                <Badge bg="primary" style={{ fontSize: 10 }}>{t.tier}</Badge>
              </div>
            </Card.Body>
            <Card.Footer style={{ background: 'transparent', borderTop: '1px solid #1e293b' }}
              className="d-flex gap-2">
              <Button variant="outline-primary" size="sm" onClick={() => handleInstall(t.id)}>
                <FiDownload size={12} /> Install
              </Button>
              <Button variant="outline-secondary" size="sm"
                onClick={() => navigate(`/workflows/builder/${t.id}`)}>
                <FiEye size={12} /> Preview
              </Button>
            </Card.Footer>
          </Card>
        </Col>
      ))}
    </Row>
  );
}
