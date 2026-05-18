import { useState, useEffect } from 'react';
import { Alert, Button, Card, Container, Form } from 'react-bootstrap';
import { useTranslation } from 'react-i18next';
import { Link, useSearchParams } from 'react-router-dom';
import authService from '../services/auth';
import BrandMark from '../components/BrandMark';

/**
 * Two-step password recovery.
 *
 *   stage = "request" — user lands on /reset-password fresh. They
 *           enter their email and we POST /auth/password-recovery/{email}.
 *           The backend always returns the same generic success message
 *           (no enumeration) and emails the token if the user exists.
 *
 *   stage = "confirm" — user followed a link from the email
 *           (`/reset-password?token=...&email=...`) OR clicked the
 *           "I already have a token" toggle. We show the token + new-
 *           password form and POST /auth/reset-password on submit.
 *
 * Earlier versions of this page only showed the confirm step, which
 * meant a user clicking "Forgot password?" from the login screen had
 * no path to actually GET the token — they got dropped onto a form
 * asking for one (see screenshot from 2026-05-12).
 */
const ResetPasswordPage = () => {
  const { t } = useTranslation('auth');
  const [searchParams] = useSearchParams();

  // Two stages; `tokenFromUrl` differentiates "user clicked the email
  // link" (auto-confirm) from "user toggled manually" (so the token
  // field stays editable when they typed it themselves).
  const [stage, setStage] = useState('request');
  const [tokenFromUrl, setTokenFromUrl] = useState(false);

  const [email, setEmail] = useState('');
  const [token, setToken] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');

  const [error, setError] = useState('');
  const [requestSentMessage, setRequestSentMessage] = useState('');
  const [success, setSuccess] = useState(false);
  const [loading, setLoading] = useState(false);

  // Hydrate from URL when the user lands via the email link.
  //
  // SECURITY (B-1 from 2026-05-12 review): the token lives in the URL
  // FRAGMENT (after `#`), NOT the query string. Fragments never appear
  // in:
  //   - the Referer header (so a third-party <img>/font/CDN/analytics
  //     pixel on this page can't leak the token)
  //   - server access logs (nginx, Cloudflare tunnel, Sentry)
  //   - document.referrer for the next-navigated page
  //
  // NIT-5 (round-7 review): even though the email isn't the secret,
  // leaving it in the address bar after we pull it into state means
  // a shoulder-surfer / screen-share / browser-extension snapshot of
  // the URL still leaks the user identity. After capturing both
  // token (from fragment) and email (from query), replaceState to
  // a clean `/reset-password` URL — same scrub pattern as the token.
  useEffect(() => {
    const eFromUrl = searchParams.get('email');
    if (eFromUrl) setEmail(eFromUrl);

    if (typeof window !== 'undefined' && window.location.hash) {
      // hash starts with `#` — strip it then parse like a query string
      // so we tolerate other fragment params landing alongside `token`.
      const hashParams = new URLSearchParams(window.location.hash.slice(1));
      const tFromHash = hashParams.get('token');
      if (tFromHash) {
        setToken(tFromHash);
        setTokenFromUrl(true);
        setStage('confirm');
      }
    }

    // NIT-5: scrub BOTH the fragment (token) and the search-string
    // (email) from the address bar once they're in state. Even if the
    // user landed without a fragment (manual visit), we still drop the
    // email query param to avoid leaking it via screenshots / address
    // bar / browser history.
    if (
      typeof window !== 'undefined' &&
      (window.location.hash || window.location.search)
    ) {
      try {
        window.history.replaceState({}, '', window.location.pathname);
      } catch (_e) {
        // replaceState can throw in privacy/sandboxed contexts; failing
        // closed (leave the URL alone) is harmless.
      }
    }
  }, [searchParams]);

  const handleRequest = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      await authService.requestPasswordReset(email);
      // The backend returns the same generic message regardless of
      // whether the email exists (prevents enumeration). Echo that
      // back to the user verbatim — `reset.requestSent` resolves to
      // 'If an account exists for that email, a reset link has been
      // sent.' on both en + es.
      setRequestSentMessage(t('reset.requestSent'));
    } catch (err) {
      // 429 = rate-limited (slowapi 3/hour per IP). Surface a
      // friendlier message than the raw FastAPI 429 body.
      const status = err?.response?.status;
      if (status === 429) {
        setError(t('reset.rateLimited'));
      } else {
        setError(err?.response?.data?.detail || t('reset.requestError'));
      }
    } finally {
      setLoading(false);
    }
  };

  const handleConfirm = async (e) => {
    e.preventDefault();
    setError('');

    if (password !== confirmPassword) {
      setError(t('reset.mismatch'));
      return;
    }
    // N-1 (security review 2026-05-12): policy lifted from 8-char
    // minimum to 12 chars + ≥3 of {upper, lower, digit, symbol}.
    // Client-side enforcement matches the server-side validator at
    // apps/api/app/schemas/auth.py — keeps users out of the latency
    // of a round-trip just to learn their password is too weak.
    if (password.length < 12) {
      setError(t('reset.tooShort'));
      return;
    }
    const classes =
      Number(/[A-Z]/.test(password)) +
      Number(/[a-z]/.test(password)) +
      Number(/[0-9]/.test(password)) +
      Number(/[^A-Za-z0-9]/.test(password));
    if (classes < 3) {
      setError(t('reset.weakComplexity'));
      return;
    }

    setLoading(true);
    try {
      await authService.resetPassword(email, token, password);
      setSuccess(true);
    } catch (err) {
      // I-N1 (security review 2026-05-12 round 2): the server returns
      // a SPECIFIC detail when the cross-browser-binding cookie is
      // missing (very common with mobile email clients that open the
      // link in a different browser than the one that initiated the
      // reset). Map to a clearer error so users don't bounce through
      // 3 attempts and burn their token.
      const detail = err?.response?.data?.detail || '';
      if (detail.startsWith('Open this link in the same browser')) {
        setError(t('reset.sameBrowserRequired'));
      } else {
        setError(detail || t('reset.error'));
      }
    } finally {
      setLoading(false);
    }
  };

  // Toggle to the confirm stage when the user already has a token but
  // didn't land via the email link (e.g. someone gave them the token
  // out-of-band). Resets transient state so the form is clean.
  const switchToConfirm = () => {
    setStage('confirm');
    setRequestSentMessage('');
    setError('');
  };

  return (
    <Container className="d-flex justify-content-center align-items-center" style={{ minHeight: '100vh' }}>
      <Card style={{ width: '400px' }} className="shadow-lg p-4">
        <Card.Body>
          <div className="text-center mb-4">
            <div style={{ marginBottom: 16 }}><BrandMark /></div>
            <h2>{t('reset.title')}</h2>
          </div>

          {success ? (
            <div className="text-center">
              <Alert variant="success">{t('reset.success')}</Alert>
              <Link to="/login">
                <Button variant="primary" className="w-100">{t('login.title')}</Button>
              </Link>
            </div>
          ) : stage === 'request' ? (
            <>
              {error && <Alert variant="danger">{error}</Alert>}
              {requestSentMessage && (
                <Alert variant="success">{requestSentMessage}</Alert>
              )}
              <p className="text-muted small">{t('reset.requestIntro')}</p>
              <Form onSubmit={handleRequest}>
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

                <Button
                  variant="primary"
                  type="submit"
                  className="w-100 mb-2"
                  disabled={loading}
                >
                  {loading ? t('reset.processing') : t('reset.sendLink')}
                </Button>
              </Form>
              {/* Out-of-band path: user already has a token (admin
                  copy-pasted it, or they grabbed it from a previous
                  email). Toggle to the confirm form without firing a
                  duplicate email request. */}
              <div className="text-center mt-3">
                <Button
                  variant="link"
                  size="sm"
                  className="text-muted p-0"
                  onClick={switchToConfirm}
                >
                  {t('reset.haveTokenLink')}
                </Button>
              </div>
              <div className="text-center mt-2">
                <Link to="/login">{t('register.loginLink')}</Link>
              </div>
            </>
          ) : (
            <>
              {error && <Alert variant="danger">{error}</Alert>}
              <p className="text-muted small">{t('reset.confirmIntro')}</p>
              <Form onSubmit={handleConfirm}>
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
                  {/* I-6 (security review 2026-05-12): the token must
                      NOT be remembered by password managers, NOT be
                      auto-corrected by mobile keyboards, and NOT be
                      visible to screen-share overlays. `type=password`
                      gets us all three behaviours; the label tells
                      humans what to paste regardless. */}
                  <Form.Control
                    type="password"
                    placeholder={t('reset.tokenPlaceholder')}
                    value={token}
                    onChange={(e) => setToken(e.target.value)}
                    readOnly={tokenFromUrl}
                    required
                    autoComplete="off"
                    autoCorrect="off"
                    autoCapitalize="off"
                    spellCheck="false"
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

                <Button
                  variant="primary"
                  type="submit"
                  className="w-100 mb-2"
                  disabled={loading}
                >
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
