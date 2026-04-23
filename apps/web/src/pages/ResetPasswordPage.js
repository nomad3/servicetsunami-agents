import { useState, useEffect } from 'react';
import { Alert, Button, Card, Container, Form } from 'react-bootstrap';
import { useTranslation } from 'react-i18next';
import { Link, useSearchParams } from 'react-router-dom';
import authService from '../services/auth';

const ResetPasswordPage = () => {
  const { t } = useTranslation('auth');
  const [searchParams] = useSearchParams();
  const [email, setEmail] = useState('');
  const [token, setToken] = useState('');
  const [tokenFromUrl, setTokenFromUrl] = useState(false);
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState('');
  const [success, setSuccess] = useState(false);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const t = searchParams.get('token');
    const e = searchParams.get('email');
    if (t) {
      setToken(t);
      setTokenFromUrl(true);
    }
    if (e) setEmail(e);
  }, [searchParams]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');

    if (password !== confirmPassword) {
      setError(t('reset.mismatch'));
      return;
    }
    if (password.length < 8) {
      setError(t('reset.tooShort'));
      return;
    }

    setLoading(true);
    try {
      await authService.resetPassword(email, token, password);
      setSuccess(true);
    } catch (err) {
      setError(err?.response?.data?.detail || t('reset.error'));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Container className="d-flex justify-content-center align-items-center" style={{ minHeight: '100vh' }}>
      <Card style={{ width: '400px' }} className="shadow-lg p-4">
        <Card.Body>
          <div className="text-center mb-4">
            <img src={`${process.env.PUBLIC_URL}/assets/brand/ap-logo-dark.png`} alt="agentprovision.com" style={{ width: 120, marginBottom: 16 }} />
            <h2>{t('reset.title')}</h2>
          </div>
          
          {success ? (
            <div className="text-center">
              <Alert variant="success">{t('reset.success')}</Alert>
              <Link to="/login">
                <Button variant="primary" className="w-100">{t('login.title')}</Button>
              </Link>
            </div>
          ) : (
            <>
              {error && <Alert variant="danger">{error}</Alert>}
              <Form onSubmit={handleSubmit}>
                <Form.Group className="mb-3">
                  <Form.Label>{t('reset.email')}</Form.Label>
                  <Form.Control
                    type="email"
                    placeholder={t('reset.emailPlaceholder')}
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    required
                  />
                </Form.Group>

                <Form.Group className="mb-3">
                  <Form.Label>{t('reset.token')}</Form.Label>
                  <Form.Control
                    type="text"
                    placeholder={t('reset.tokenPlaceholder')}
                    value={token}
                    onChange={(e) => setToken(e.target.value)}
                    readOnly={tokenFromUrl}
                    required
                  />
                </Form.Group>

                <Form.Group className="mb-3">
                  <Form.Label>{t('reset.newPassword')}</Form.Label>
                  <Form.Control
                    type="password"
                    placeholder={t('reset.newPasswordPlaceholder')}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    required
                  />
                </Form.Group>

                <Form.Group className="mb-3">
                  <Form.Label>{t('reset.confirmPassword')}</Form.Label>
                  <Form.Control
                    type="password"
                    placeholder={t('reset.confirmPasswordPlaceholder')}
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    required
                  />
                </Form.Group>

                <Button variant="primary" type="submit" className="w-100 mb-2" disabled={loading}>
                  {loading ? t('reset.processing') : t('reset.submit')}
                </Button>
                <div className="text-center mt-3">
                  <Link to="/login">{t('register.loginLink')}</Link>
                </div>
              </Form>
            </>
          )}
        </Card.Body>
      </Card>
    </Container>
  );
};

export default ResetPasswordPage;
