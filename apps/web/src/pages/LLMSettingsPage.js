import { useEffect, useState } from 'react';
import { Alert, Badge, Button, Col, Container, Form, InputGroup, Row, Spinner } from 'react-bootstrap';
import { useTranslation } from 'react-i18next';
import { FaCheckCircle, FaMicrochip, FaEye, FaEyeSlash, FaKey, FaTimesCircle } from 'react-icons/fa';
import PremiumCard from '../components/common/PremiumCard';
import Layout from '../components/Layout';
import llmService from '../services/llm';

const LLMSettingsPage = () => {
  const { t } = useTranslation('settings');
  const [providers, setProviders] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [savingProvider, setSavingProvider] = useState(null);
  const [apiKeys, setApiKeys] = useState({});
  const [showKeys, setShowKeys] = useState({});
  const [saveSuccess, setSaveSuccess] = useState({});

  useEffect(() => {
    loadProviders();
  }, []);

  const loadProviders = async () => {
    try {
      setLoading(true);
      const data = await llmService.getProviderStatus();
      setProviders(data);
    } catch (err) {
      setError(t('llm.errors.load'));
    } finally {
      setLoading(false);
    }
  };

  const handleKeyChange = (providerName, value) => {
    setApiKeys(prev => ({ ...prev, [providerName]: value }));
    setSaveSuccess(prev => ({ ...prev, [providerName]: false }));
  };

  const handleSaveKey = async (providerName) => {
    const key = apiKeys[providerName];
    if (!key) return;

    try {
      setSavingProvider(providerName);
      await llmService.setProviderKey(providerName, key);
      setSaveSuccess(prev => ({ ...prev, [providerName]: true }));
      setApiKeys(prev => ({ ...prev, [providerName]: '' }));
      await loadProviders();
    } catch (err) {
      setError(t('llm.errors.saveKey', { provider: providerName }));
    } finally {
      setSavingProvider(null);
    }
  };

  const toggleShowKey = (providerName) => {
    setShowKeys(prev => ({ ...prev, [providerName]: !prev[providerName] }));
  };

  const getProviderIcon = (name) => {
    const icons = {
      openai: '\uD83E\uDD16',
      anthropic: '\uD83E\uDDE0',
      deepseek: '\uD83D\uDD0D',
      google: '\uD83C\uDF10',
      mistral: '\uD83D\uDCA8'
    };
    return icons[name] || '\uD83D\uDD0C';
  };

  if (loading) {
    return (
      <Layout>
        <Container className="py-4 text-center">
          <Spinner animation="border" variant="primary" />
          <p className="mt-3 text-soft">{t('llm.loading')}</p>
        </Container>
      </Layout>
    );
  }

  return (
    <Layout>
      <Container fluid className="py-2">
        <div className="d-flex align-items-center mb-4">
          <div className="icon-pill-sm me-3">
            <FaMicrochip size={24} />
          </div>
          <div>
            <h2 className="mb-1 fw-bold">{t('llm.title')}</h2>
            <p className="text-soft mb-0">{t('llm.subtitle')}</p>
          </div>
        </div>

        {error && (
          <Alert variant="danger" dismissible onClose={() => setError(null)}>
            {error}
          </Alert>
        )}

        <Row xs={1} md={2} lg={3} className="g-4">
          {providers.map((provider) => (
            <Col key={provider.name}>
              <PremiumCard className={`h-100 ${provider.configured ? 'border-primary border-opacity-50' : ''}`}>
                <div className="d-flex align-items-center justify-content-between mb-3">
                  <div className="d-flex align-items-center">
                    <span className="me-2" style={{ fontSize: '1.5rem' }}>
                      {getProviderIcon(provider.name)}
                    </span>
                    <strong>{provider.display_name}</strong>
                  </div>
                  {provider.configured ? (
                    <Badge bg="success" className="d-flex align-items-center bg-opacity-25 text-success border border-success">
                      <FaCheckCircle className="me-1" /> {t('llm.connected')}
                    </Badge>
                  ) : (
                    <Badge bg="secondary" className="d-flex align-items-center bg-opacity-25 text-secondary border border-secondary">
                      <FaTimesCircle className="me-1" /> {t('llm.notConfigured')}
                    </Badge>
                  )}
                </div>

                <div className="mb-3">
                  <Form.Label className="small text-soft">
                    <FaKey className="me-1" />
                    {t('llm.apiKey')}
                  </Form.Label>
                  <InputGroup>
                    <Form.Control
                      type={showKeys[provider.name] ? 'text' : 'password'}
                      placeholder={provider.configured ? t('llm.apiKeyMasked') : t('llm.apiKeyPlaceholder')}
                      value={apiKeys[provider.name] || ''}
                      onChange={(e) => handleKeyChange(provider.name, e.target.value)}
                      disabled={savingProvider === provider.name}
                      className="border-secondary border-opacity-50"
                    />
                    <Button
                      variant="outline-secondary"
                      className="border-secondary border-opacity-50 text-soft"
                      onClick={() => toggleShowKey(provider.name)}
                    >
                      {showKeys[provider.name] ? <FaEyeSlash /> : <FaEye />}
                    </Button>
                  </InputGroup>
                </div>

                {saveSuccess[provider.name] && (
                  <small className="text-success mb-3 d-block">
                    <FaCheckCircle className="me-1" /> {t('llm.keySaved')}
                  </small>
                )}

                <div className="mt-auto d-grid">
                  <Button
                    variant="primary"
                    size="sm"
                    onClick={() => handleSaveKey(provider.name)}
                    disabled={!apiKeys[provider.name] || savingProvider === provider.name}
                  >
                    {savingProvider === provider.name ? (
                      <Spinner animation="border" size="sm" />
                    ) : (
                      t('llm.saveKey')
                    )}
                  </Button>
                </div>

                <div className="mt-3 pt-3 border-top border-secondary border-opacity-25 text-center">
                  <small className="text-muted">
                    {provider.is_openai_compatible ? t('llm.openaiCompatible') : t('llm.nativeApi')}
                  </small>
                </div>
              </PremiumCard>
            </Col>
          ))}
        </Row>
      </Container>
    </Layout>
  );
};

export default LLMSettingsPage;
