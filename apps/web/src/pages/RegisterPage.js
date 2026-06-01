import React, { useState } from 'react';
import { Container, Form, Button, Card, Alert } from 'react-bootstrap';
import { useTranslation } from 'react-i18next';
import { useNavigate, Link } from 'react-router-dom';
import authService from '../services/auth';
import BrandMark from '../components/BrandMark';

// Mirrors the server-side policy in apps/api/app/schemas/auth.py
// (_validate_password_complexity): 12+ chars and at least 3 of
// {uppercase, lowercase, digit, symbol}. Enforced client-side so users
// get an inline message instead of a bare 422.
const PASSWORD_MIN_LENGTH = 12;
const PASSWORD_MIN_CLASSES = 3;

const countPasswordClasses = (pw) =>
  [/[a-z]/, /[A-Z]/, /[0-9]/, /[^A-Za-z0-9]/].filter((re) => re.test(pw)).length;

const isPasswordValid = (pw) =>
  pw.length >= PASSWORD_MIN_LENGTH && countPasswordClasses(pw) >= PASSWORD_MIN_CLASSES;

// FastAPI returns a string `detail` for HTTPExceptions (e.g. "email taken")
// but an array of {loc, msg, ...} for 422 validation errors. Render either
// as readable text instead of dropping the user on a generic message.
const extractErrorMessage = (err, t) => {
  const detail = err.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    const msgs = detail.map((d) => d && d.msg).filter(Boolean);
    if (msgs.length) return msgs.join(' ');
  }
  return t('register.error');
};

const RegisterPage = () => {
  const { t } = useTranslation('auth');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [fullName, setFullName] = useState('');
  const [tenantName, setTenantName] = useState('');
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const navigate = useNavigate();

  const handleSubmit = async (event) => {
    event.preventDefault();
    setError('');
    setSuccess('');
    if (!isPasswordValid(password)) {
      setError(t('register.passwordWeak'));
      return;
    }
    try {
      await authService.register(email, password, fullName, tenantName);
      setSuccess(t('register.success'));
      setTimeout(() => {
        navigate('/login');
      }, 2000);
    } catch (err) {
      setError(extractErrorMessage(err, t));
      console.error('Registration error:', err);
    }
  };

  return (
    <Container className="d-flex justify-content-center align-items-center" style={{ minHeight: '100vh' }}>
      <Card style={{ width: '400px' }} className="shadow-lg p-4">
        <Card.Body>
          <div className="text-center mb-4">
            <div style={{ marginBottom: 16 }}><BrandMark /></div>
            <h2>{t('register.title')}</h2>
          </div>
          {error && <Alert variant="danger">{error}</Alert>}
          {success && <Alert variant="success">{success}</Alert>}
          <Form onSubmit={handleSubmit}>
            <Form.Group className="mb-3" controlId="formBasicEmail">
              <Form.Label>{t('register.email')}</Form.Label>
              <Form.Control
                type="email"
                placeholder={t('register.emailPlaceholder')}
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </Form.Group>

            <Form.Group className="mb-3" controlId="formBasicPassword">
              <Form.Label>{t('register.password')}</Form.Label>
              <Form.Control
                type="password"
                placeholder={t('register.passwordPlaceholder')}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                minLength={PASSWORD_MIN_LENGTH}
                required
              />
              <Form.Text className="text-muted">{t('register.passwordHint')}</Form.Text>
            </Form.Group>

            <Form.Group className="mb-3" controlId="formBasicFullName">
              <Form.Label>{t('register.fullName')}</Form.Label>
              <Form.Control
                type="text"
                placeholder={t('register.fullNamePlaceholder')}
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                required
              />
            </Form.Group>

            <Form.Group className="mb-3" controlId="formBasicTenantName">
              <Form.Label>{t('register.tenantName')}</Form.Label>
              <Form.Control
                type="text"
                placeholder={t('register.tenantNamePlaceholder')}
                value={tenantName}
                onChange={(e) => setTenantName(e.target.value)}
                required
              />
            </Form.Group>

            <Button variant="primary" type="submit" className="w-100 mb-2">
              {t('register.submit')}
            </Button>
            <div className="text-center mt-3">
              {t('register.hasAccount')} <Link to="/login">{t('register.loginLink')}</Link>
            </div>
          </Form>
        </Card.Body>
      </Card>
    </Container>
  );
};

export default RegisterPage;
