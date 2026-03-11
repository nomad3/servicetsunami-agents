import React, { useState } from 'react';
import { Container, Form, Button, Card, Alert } from 'react-bootstrap';
import { useTranslation } from 'react-i18next';
import { useNavigate, Link } from 'react-router-dom';
import authService from '../services/auth';

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
    try {
      await authService.register(email, password, fullName, tenantName);
      setSuccess(t('register.success'));
      setTimeout(() => {
        navigate('/login');
      }, 2000);
    } catch (err) {
      setError(err.response?.data?.detail || t('register.error'));
      console.error('Registration error:', err);
    }
  };

  return (
    <Container className="d-flex justify-content-center align-items-center" style={{ minHeight: '100vh' }}>
      <Card style={{ width: '400px' }} className="shadow-lg p-4">
        <Card.Body>
          <h2 className="text-center mb-4">{t('register.title')}</h2>
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
                required
              />
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
