import { useEffect, useState } from 'react';
import { Alert, Button, Col, Container, Form, Row } from 'react-bootstrap';
import { useTranslation } from 'react-i18next';
import PremiumCard from '../components/common/PremiumCard';
import Layout from '../components/Layout';
import { brandingService } from '../services/branding';

function BrandingPage() {
  const { t } = useTranslation('settings');
  const [branding, setBranding] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState(null);

  useEffect(() => {
    loadBranding();
  }, []);

  const loadBranding = async () => {
    try {
      const data = await brandingService.getBranding();
      setBranding(data);
    } catch (error) {
      console.error('Failed to load branding:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async (e) => {
    e.preventDefault();
    setSaving(true);
    setMessage(null);
    try {
      await brandingService.updateBranding(branding);
      setMessage({ type: 'success', text: t('branding.success') });
    } catch (error) {
      setMessage({ type: 'danger', text: t('branding.error') });
    } finally {
      setSaving(false);
    }
  };

  const updateField = (field, value) => {
    setBranding({ ...branding, [field]: value });
  };

  if (loading) {
    return (
      <Layout>
        <Container className="py-5 text-center text-soft">{t('branding.loading')}</Container>
      </Layout>
    );
  }

  return (
    <Layout>
      <Container fluid className="py-2">
        <Row className="mb-4">
          <Col>
            <h2 className="fw-bold mb-1">{t('branding.title')}</h2>
            <p className="text-soft mb-0">{t('branding.subtitle')}</p>
          </Col>
        </Row>

        {message && <Alert variant={message.type}>{message.text}</Alert>}

        <Form onSubmit={handleSave}>
          <Row>
            <Col md={6}>
              <PremiumCard className="mb-4 h-100">
                <div className="mb-4 border-bottom border-secondary border-opacity-25 pb-2">
                  <h5 className="mb-0">{t('branding.brandIdentity')}</h5>
                </div>
                <Form.Group className="mb-3">
                  <Form.Label className="text-soft">{t('branding.companyName')}</Form.Label>
                  <Form.Control
                    type="text"
                    value={branding?.company_name || ''}
                    onChange={(e) => updateField('company_name', e.target.value)}
                    className="border-secondary border-opacity-50"
                  />
                </Form.Group>
                <Form.Group className="mb-3">
                  <Form.Label className="text-soft">{t('branding.logoUrl')}</Form.Label>
                  <Form.Control
                    type="url"
                    value={branding?.logo_url || ''}
                    onChange={(e) => updateField('logo_url', e.target.value)}
                    className="border-secondary border-opacity-50"
                  />
                </Form.Group>
                <Form.Group className="mb-3">
                  <Form.Label className="text-soft">{t('branding.supportEmail')}</Form.Label>
                  <Form.Control
                    type="email"
                    value={branding?.support_email || ''}
                    onChange={(e) => updateField('support_email', e.target.value)}
                    className="border-secondary border-opacity-50"
                  />
                </Form.Group>
              </PremiumCard>
            </Col>

            <Col md={6}>
              <PremiumCard className="mb-4 h-100">
                <div className="mb-4 border-bottom border-secondary border-opacity-25 pb-2">
                  <h5 className="mb-0">{t('branding.colors')}</h5>
                </div>
                <Row>
                  <Col md={6}>
                    <Form.Group className="mb-3">
                      <Form.Label className="text-soft">{t('branding.primaryColor')}</Form.Label>
                      <Form.Control
                        type="color"
                        value={branding?.primary_color || '#2b7de9'}
                        onChange={(e) => updateField('primary_color', e.target.value)}
                        className="border-secondary border-opacity-50"
                        style={{ minHeight: '40px' }}
                      />
                    </Form.Group>
                  </Col>
                  <Col md={6}>
                    <Form.Group className="mb-3">
                      <Form.Label className="text-soft">{t('branding.secondaryColor')}</Form.Label>
                      <Form.Control
                        type="color"
                        value={branding?.secondary_color || '#5ec5b0'}
                        onChange={(e) => updateField('secondary_color', e.target.value)}
                        className="border-secondary border-opacity-50"
                        style={{ minHeight: '40px' }}
                      />
                    </Form.Group>
                  </Col>
                </Row>
                <Row>
                  <Col md={6}>
                    <Form.Group className="mb-3">
                      <Form.Label className="text-soft">{t('branding.accentColor')}</Form.Label>
                      <Form.Control
                        type="color"
                        value={branding?.accent_color || '#2b7de9'}
                        onChange={(e) => updateField('accent_color', e.target.value)}
                        className="border-secondary border-opacity-50"
                        style={{ minHeight: '40px' }}
                      />
                    </Form.Group>
                  </Col>
                </Row>
              </PremiumCard>
            </Col>
          </Row>

          <Row>
            <Col md={6}>
              <PremiumCard className="mb-4 h-100">
                <div className="mb-4 border-bottom border-secondary border-opacity-25 pb-2">
                  <h5 className="mb-0">{t('branding.aiAssistant')}</h5>
                </div>
                <Form.Group className="mb-3">
                  <Form.Label className="text-soft">{t('branding.assistantName')}</Form.Label>
                  <Form.Control
                    type="text"
                    value={branding?.ai_assistant_name || ''}
                    onChange={(e) => updateField('ai_assistant_name', e.target.value)}
                    placeholder={t('branding.assistantNamePlaceholder')}
                    className="border-secondary border-opacity-50"
                  />
                </Form.Group>
                <Form.Group className="mb-3">
                  <Form.Label className="text-soft">{t('branding.industry')}</Form.Label>
                  <Form.Select
                    value={branding?.industry || ''}
                    onChange={(e) => updateField('industry', e.target.value)}
                    className="border-secondary border-opacity-50"
                  >
                    <option value="">{t('branding.industryPlaceholder')}</option>
                    <option value="healthcare">{t('branding.industries.healthcare')}</option>
                    <option value="finance">{t('branding.industries.finance')}</option>
                    <option value="legal">{t('branding.industries.legal')}</option>
                    <option value="retail">{t('branding.industries.retail')}</option>
                    <option value="technology">{t('branding.industries.technology')}</option>
                  </Form.Select>
                </Form.Group>
              </PremiumCard>
            </Col>

            <Col md={6}>
              <PremiumCard className="mb-4 h-100">
                <div className="mb-4 border-bottom border-secondary border-opacity-25 pb-2">
                  <h5 className="mb-0">{t('branding.customDomain')}</h5>
                </div>
                <Form.Group className="mb-3">
                  <Form.Label className="text-soft">{t('branding.domain')}</Form.Label>
                  <Form.Control
                    type="text"
                    value={branding?.custom_domain || ''}
                    onChange={(e) => updateField('custom_domain', e.target.value)}
                    placeholder={t('branding.domainPlaceholder')}
                    className="border-secondary border-opacity-50"
                  />
                </Form.Group>
                <p className="text-soft small">
                  {branding?.domain_verified
                    ? `\u2705 ${t('branding.domainVerified')}`
                    : `\u26A0\uFE0F ${t('branding.domainNotVerified')}`}
                </p>
              </PremiumCard>
            </Col>
          </Row>

          <div className="d-flex justify-content-end">
            <Button type="submit" variant="primary" disabled={saving} size="lg" className="px-5">
              {saving ? t('branding.saving') : t('branding.saveChanges')}
            </Button>
          </div>
        </Form>
      </Container>
    </Layout>
  );
}

export default BrandingPage;
