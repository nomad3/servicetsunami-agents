import React, { useState, useEffect } from 'react';
import { Row, Col, Spinner } from 'react-bootstrap';
import { FiDownload, FiEye } from 'react-icons/fi';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import dynamicWorkflowService from '../../services/dynamicWorkflowService';

const TRIGGER_LABELS = {
  cron: 'Scheduled', interval: 'Interval', webhook: 'Webhook',
  event: 'Event', manual: 'Manual', agent: 'Agent',
};

export default function TemplatesTab() {
  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();
  const { t } = useTranslation('workflows');

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
      <div className="ap-empty">
        <div className="ap-empty-title">{t('templates.noTemplates')}</div>
        <div className="ap-empty-text">{t('templates.noTemplatesDesc')}</div>
      </div>
    );
  }

  return (
    <Row xs={1} md={2} lg={3} className="g-3">
      {templates.map((tmpl) => (
        <Col key={tmpl.id}>
          <article className="ap-card template-card h-100">
            <div className="ap-card-body">
              <h3 className="ap-card-title">{tmpl.name}</h3>
              <p className="ap-card-text card-text">{tmpl.description}</p>
              <div className="d-flex gap-1 flex-wrap">
                <span className="ap-badge-outline">
                  {TRIGGER_LABELS[tmpl.trigger_config?.type] || 'Manual'}
                </span>
                <span className="ap-badge-outline">
                  {t('templates.steps', { count: (tmpl.definition?.steps || []).length })}
                </span>
                <span className="ap-badge-outline">{tmpl.tier}</span>
              </div>
            </div>
            <footer className="card-footer d-flex gap-2 p-3">
              <button type="button" className="ap-btn-primary ap-btn-sm" onClick={() => handleInstall(tmpl.id)}>
                <FiDownload size={12} /> {t('templates.install')}
              </button>
              <button
                type="button"
                className="ap-btn-secondary ap-btn-sm"
                onClick={() => navigate(`/workflows/builder/${tmpl.id}`)}
              >
                <FiEye size={12} /> {t('templates.preview')}
              </button>
            </footer>
          </article>
        </Col>
      ))}
    </Row>
  );
}
