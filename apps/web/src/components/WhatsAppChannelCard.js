import { useEffect, useState, useRef, useCallback } from 'react';
import {
  Alert,
  Badge,
  Button,
  Form,
  Spinner,
} from 'react-bootstrap';
import {
  FaWhatsapp,
  FaQrcode,
  FaSignOutAlt,
  FaPaperPlane,
  FaCheckCircle,
  FaTimesCircle,
  FaLink,
} from 'react-icons/fa';
import channelService from '../services/channelService';

const WhatsAppChannelCard = () => {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);

  // Enable form
  const [dmPolicy, setDmPolicy] = useState('allowlist');
  const [allowFrom, setAllowFrom] = useState('');
  const [enabling, setEnabling] = useState(false);

  // QR pairing
  const [qrDataUrl, setQrDataUrl] = useState(null);
  const [pairing, setPairing] = useState(false);
  const pollRef = useRef(null);

  // Allowlist editing
  const [editingAllowlist, setEditingAllowlist] = useState(false);
  const [editAllowFrom, setEditAllowFrom] = useState('');
  const [editDmPolicy, setEditDmPolicy] = useState('allowlist');
  const [savingAllowlist, setSavingAllowlist] = useState(false);

  // Send test
  const [sendTo, setSendTo] = useState('');
  const [sendMessage, setSendMessage] = useState('');
  const [sending, setSending] = useState(false);

  // Logout
  const [loggingOut, setLoggingOut] = useState(false);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await channelService.getWhatsAppStatus();
      setStatus(res.data);
    } catch (err) {
      // 502 means no instance or gateway error — treat as not enabled
      setStatus({ enabled: false, linked: false, connected: false, accounts: [] });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const handleEnable = async () => {
    try {
      setEnabling(true);
      setError(null);
      const phones = allowFrom
        .split(',')
        .map((p) => p.trim())
        .filter(Boolean);
      await channelService.enableWhatsApp({
        dm_policy: dmPolicy,
        allow_from: phones,
      });
      setSuccess('WhatsApp channel enabled');
      setTimeout(() => setSuccess(null), 3000);
      await fetchStatus();
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to enable WhatsApp');
      setTimeout(() => setError(null), 5000);
    } finally {
      setEnabling(false);
    }
  };

  const handleDisable = async () => {
    try {
      setEnabling(true);
      setError(null);
      await channelService.disableWhatsApp();
      setSuccess('WhatsApp channel disabled');
      setTimeout(() => setSuccess(null), 3000);
      await fetchStatus();
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to disable WhatsApp');
      setTimeout(() => setError(null), 5000);
    } finally {
      setEnabling(false);
    }
  };

  const handleStartPairing = async () => {
    try {
      setPairing(true);
      setError(null);
      setQrDataUrl(null);
      const res = await channelService.startPairing({ force: false });
      setQrDataUrl(res.data.qr_data_url);

      // Poll for pairing completion
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = setInterval(async () => {
        try {
          const pollRes = await channelService.getPairingStatus({
            account_id: 'default',
          });
          if (pollRes.data.connected) {
            clearInterval(pollRef.current);
            pollRef.current = null;
            setQrDataUrl(null);
            setPairing(false);
            setSuccess('WhatsApp linked successfully!');
            setTimeout(() => setSuccess(null), 5000);
            await fetchStatus();
          }
        } catch {
          // Polling errors are transient, keep trying
        }
      }, 3000);

      // Stop polling after 2 minutes
      setTimeout(() => {
        if (pollRef.current) {
          clearInterval(pollRef.current);
          pollRef.current = null;
          setPairing(false);
          setQrDataUrl(null);
          setError('Pairing timed out. Try again.');
          setTimeout(() => setError(null), 5000);
        }
      }, 120000);
    } catch (err) {
      setPairing(false);
      setError(err.response?.data?.detail || 'Failed to start pairing');
      setTimeout(() => setError(null), 5000);
    }
  };

  const handleLogout = async () => {
    try {
      setLoggingOut(true);
      setError(null);
      await channelService.logoutWhatsApp({});
      setSuccess('WhatsApp disconnected');
      setTimeout(() => setSuccess(null), 3000);
      await fetchStatus();
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to logout');
      setTimeout(() => setError(null), 5000);
    } finally {
      setLoggingOut(false);
    }
  };

  const handleSend = async () => {
    if (!sendTo.trim() || !sendMessage.trim()) return;
    try {
      setSending(true);
      setError(null);
      await channelService.sendWhatsApp({
        to: sendTo.trim(),
        message: sendMessage.trim(),
      });
      setSuccess('Message sent');
      setSendMessage('');
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to send message');
      setTimeout(() => setError(null), 5000);
    } finally {
      setSending(false);
    }
  };

  if (loading) {
    return (
      <div className="text-center py-3">
        <Spinner animation="border" size="sm" variant="primary" />
        <p className="text-muted mt-2 mb-0" style={{ fontSize: '0.82rem' }}>
          Checking WhatsApp status...
        </p>
      </div>
    );
  }

  const isEnabled = status?.enabled;
  const isConnected = status?.connected;
  const isLinked = status?.linked;

  return (
    <div>
      {error && (
        <Alert
          variant="danger"
          onClose={() => setError(null)}
          dismissible
          className="mb-2"
          style={{ fontSize: '0.82rem' }}
        >
          <FaTimesCircle className="me-1" size={12} />
          {error}
        </Alert>
      )}
      {success && (
        <Alert
          variant="success"
          onClose={() => setSuccess(null)}
          dismissible
          className="mb-2"
          style={{ fontSize: '0.82rem' }}
        >
          <FaCheckCircle className="me-1" size={12} />
          {success}
        </Alert>
      )}

      {/* ── Connection Status Badge ── */}
      <div className="d-flex align-items-center justify-content-between mb-3">
        <div className="d-flex align-items-center gap-2">
          <FaWhatsapp size={16} style={{ color: '#25D366' }} />
          <span style={{ fontSize: '0.85rem', fontWeight: 600, color: 'var(--color-foreground)' }}>
            Channel Status
          </span>
        </div>
        <Badge
          bg={isConnected ? 'success' : isLinked ? 'warning' : isEnabled ? 'info' : 'secondary'}
          style={{ fontSize: '0.7rem' }}
        >
          {isConnected
            ? 'Connected'
            : isLinked
              ? 'Disconnected'
              : isEnabled
                ? 'Not Linked'
                : 'Not Enabled'}
        </Badge>
      </div>

      {/* ── Not Enabled: Show enable form ── */}
      {!isEnabled && !qrDataUrl && (
        <div>
          <Form.Group className="mb-2">
            <Form.Label style={{ fontSize: '0.78rem', color: 'var(--color-muted)' }}>
              DM Policy
            </Form.Label>
            <Form.Select
              size="sm"
              value={dmPolicy}
              onChange={(e) => setDmPolicy(e.target.value)}
              style={{
                background: 'var(--surface-contrast, rgba(0,0,0,0.2))',
                border: '1px solid var(--color-border)',
                color: 'var(--color-foreground)',
                fontSize: '0.82rem',
              }}
            >
              <option value="allowlist">Allowlist (specific numbers only)</option>
              <option value="pairing">Pairing (approve new contacts)</option>
              <option value="open">Open (anyone can message)</option>
            </Form.Select>
          </Form.Group>

          <Form.Group className="mb-3">
            <Form.Label style={{ fontSize: '0.78rem', color: 'var(--color-muted)' }}>
              Allowed Phone Numbers (comma-separated, E.164)
            </Form.Label>
            <Form.Control
              type="text"
              size="sm"
              placeholder="+15551234567, +15559876543"
              value={allowFrom}
              onChange={(e) => setAllowFrom(e.target.value)}
              style={{
                background: 'var(--surface-contrast, rgba(0,0,0,0.2))',
                border: '1px solid var(--color-border)',
                color: 'var(--color-foreground)',
                fontSize: '0.82rem',
              }}
            />
          </Form.Group>

          <Button
            variant="success"
            size="sm"
            className="w-100"
            onClick={handleEnable}
            disabled={enabling}
          >
            {enabling ? (
              <Spinner animation="border" size="sm" style={{ width: 14, height: 14, borderWidth: 1.5 }} className="me-2" />
            ) : (
              <FaWhatsapp className="me-2" size={14} />
            )}
            Enable WhatsApp Channel
          </Button>
        </div>
      )}

      {/* ── Enabled: Show allowlist settings + pair/disable buttons ── */}
      {isEnabled && !isLinked && !qrDataUrl && (
        <div>
          {/* Allowlist settings */}
          <div className="mb-3">
            <Form.Group className="mb-2">
              <Form.Label style={{ fontSize: '0.78rem', color: 'var(--color-muted)' }}>
                DM Policy
              </Form.Label>
              <Form.Select
                size="sm"
                value={editingAllowlist ? editDmPolicy : (status?.dm_policy || 'allowlist')}
                onChange={(e) => {
                  setEditingAllowlist(true);
                  setEditDmPolicy(e.target.value);
                }}
                style={{
                  background: 'var(--surface-contrast, rgba(0,0,0,0.2))',
                  border: '1px solid var(--color-border)',
                  color: 'var(--color-foreground)',
                  fontSize: '0.82rem',
                }}
              >
                <option value="allowlist">Allowlist (specific numbers only)</option>
                <option value="pairing">Pairing (approve new contacts)</option>
                <option value="open">Open (anyone can message)</option>
              </Form.Select>
            </Form.Group>

            <Form.Group className="mb-2">
              <Form.Label style={{ fontSize: '0.78rem', color: 'var(--color-muted)' }}>
                Allowed Phone Numbers (comma-separated, E.164)
              </Form.Label>
              <Form.Control
                type="text"
                size="sm"
                placeholder="+15551234567, +15559876543"
                value={editingAllowlist ? editAllowFrom : (status?.allow_from || []).join(', ')}
                onChange={(e) => {
                  setEditingAllowlist(true);
                  setEditAllowFrom(e.target.value);
                }}
                onFocus={() => {
                  if (!editingAllowlist) {
                    setEditAllowFrom((status?.allow_from || []).join(', '));
                    setEditDmPolicy(status?.dm_policy || 'allowlist');
                    setEditingAllowlist(true);
                  }
                }}
                style={{
                  background: 'var(--surface-contrast, rgba(0,0,0,0.2))',
                  border: '1px solid var(--color-border)',
                  color: 'var(--color-foreground)',
                  fontSize: '0.82rem',
                }}
              />
            </Form.Group>

            {editingAllowlist && (
              <Button
                variant="primary"
                size="sm"
                className="w-100 mb-2"
                onClick={async () => {
                  try {
                    setSavingAllowlist(true);
                    const phones = editAllowFrom.split(',').map(p => p.trim()).filter(Boolean);
                    await channelService.updateWhatsAppSettings({
                      dm_policy: editDmPolicy,
                      allow_from: phones,
                    });
                    setEditingAllowlist(false);
                    setSuccess('Allowlist updated');
                    setTimeout(() => setSuccess(null), 3000);
                    await fetchStatus();
                  } catch (err) {
                    setError(err.response?.data?.detail || 'Failed to update settings');
                    setTimeout(() => setError(null), 5000);
                  } finally {
                    setSavingAllowlist(false);
                  }
                }}
                disabled={savingAllowlist}
              >
                {savingAllowlist ? (
                  <Spinner animation="border" size="sm" style={{ width: 14, height: 14, borderWidth: 1.5 }} className="me-2" />
                ) : (
                  <FaCheckCircle className="me-2" size={12} />
                )}
                Save Settings
              </Button>
            )}
          </div>

          <p className="text-muted mb-2 text-center" style={{ fontSize: '0.82rem' }}>
            Link your WhatsApp by scanning a QR code
          </p>
          <div className="d-flex gap-2">
            <Button
              variant="outline-success"
              size="sm"
              className="flex-grow-1"
              onClick={handleStartPairing}
              disabled={pairing}
            >
              {pairing ? (
                <Spinner animation="border" size="sm" style={{ width: 14, height: 14, borderWidth: 1.5 }} className="me-2" />
              ) : (
                <FaQrcode className="me-2" size={12} />
              )}
              Link Phone
            </Button>
            <Button
              variant="outline-danger"
              size="sm"
              onClick={handleDisable}
              disabled={enabling}
            >
              Disable
            </Button>
          </div>
        </div>
      )}

      {/* ── QR Code Display ── */}
      {qrDataUrl && (
        <div className="text-center">
          <p className="text-muted mb-2" style={{ fontSize: '0.82rem' }}>
            Open WhatsApp on your phone &rarr; Linked Devices &rarr; Scan
          </p>
          <div
            style={{
              background: '#fff',
              borderRadius: 12,
              padding: 16,
              display: 'inline-block',
              marginBottom: 12,
            }}
          >
            <img
              src={qrDataUrl}
              alt="WhatsApp QR Code"
              style={{ width: 220, height: 220 }}
            />
          </div>
          <div className="d-flex align-items-center justify-content-center gap-2">
            <Spinner animation="border" size="sm" style={{ width: 12, height: 12, borderWidth: 1.5, color: '#25D366' }} />
            <span className="text-muted" style={{ fontSize: '0.78rem' }}>
              Waiting for scan...
            </span>
          </div>
          <Button
            variant="outline-secondary"
            size="sm"
            className="mt-2"
            onClick={() => {
              if (pollRef.current) clearInterval(pollRef.current);
              pollRef.current = null;
              setQrDataUrl(null);
              setPairing(false);
            }}
          >
            Cancel
          </Button>
        </div>
      )}

      {/* ── Connected: Show test send + logout ── */}
      {isEnabled && isLinked && !qrDataUrl && (
        <div>
          {/* Account info */}
          {status?.accounts?.length > 0 && (
            <div className="mb-3" style={{ fontSize: '0.78rem', color: 'var(--color-muted)' }}>
              {status.accounts.map((acc, i) => (
                <div key={i} className="d-flex align-items-center gap-2 mb-1">
                  <FaLink size={10} />
                  <span>
                    {acc.accountId || 'default'}
                    {acc.connected && (
                      <Badge bg="success" className="ms-2" style={{ fontSize: '0.65rem' }}>
                        Live
                      </Badge>
                    )}
                  </span>
                </div>
              ))}
            </div>
          )}

          {/* Allowlist settings (editable when connected) */}
          <div className="mb-3 pb-3" style={{ borderBottom: '1px solid var(--color-border)' }}>
            <div className="d-flex align-items-center justify-content-between mb-2">
              <span style={{ fontSize: '0.8rem', fontWeight: 600, color: 'var(--color-foreground)' }}>
                Allowlist
              </span>
              <Badge bg="info" style={{ fontSize: '0.65rem' }}>
                {status?.dm_policy || 'allowlist'}
              </Badge>
            </div>
            <Form.Control
              type="text"
              size="sm"
              placeholder="+15551234567, +15559876543"
              value={editingAllowlist ? editAllowFrom : (status?.allow_from || []).join(', ')}
              onChange={(e) => {
                setEditingAllowlist(true);
                setEditAllowFrom(e.target.value);
              }}
              onFocus={() => {
                if (!editingAllowlist) {
                  setEditAllowFrom((status?.allow_from || []).join(', '));
                  setEditDmPolicy(status?.dm_policy || 'allowlist');
                  setEditingAllowlist(true);
                }
              }}
              style={{
                background: 'var(--surface-contrast, rgba(0,0,0,0.2))',
                border: '1px solid var(--color-border)',
                color: 'var(--color-foreground)',
                fontSize: '0.82rem',
              }}
            />
            {editingAllowlist && (
              <Button
                variant="primary"
                size="sm"
                className="w-100 mt-2"
                onClick={async () => {
                  try {
                    setSavingAllowlist(true);
                    const phones = editAllowFrom.split(',').map(p => p.trim()).filter(Boolean);
                    await channelService.updateWhatsAppSettings({
                      dm_policy: editDmPolicy,
                      allow_from: phones,
                    });
                    setEditingAllowlist(false);
                    setSuccess('Allowlist updated');
                    setTimeout(() => setSuccess(null), 3000);
                    await fetchStatus();
                  } catch (err) {
                    setError(err.response?.data?.detail || 'Failed to update settings');
                    setTimeout(() => setError(null), 5000);
                  } finally {
                    setSavingAllowlist(false);
                  }
                }}
                disabled={savingAllowlist}
              >
                {savingAllowlist ? (
                  <Spinner animation="border" size="sm" style={{ width: 14, height: 14, borderWidth: 1.5 }} className="me-2" />
                ) : (
                  <FaCheckCircle className="me-2" size={12} />
                )}
                Save Allowlist
              </Button>
            )}
          </div>

          {/* Test send form */}
          <div
            className="mb-2"
            style={{ display: 'flex', alignItems: 'center', gap: 6 }}
          >
            <FaPaperPlane size={12} style={{ color: 'var(--color-muted)' }} />
            <span
              style={{
                fontSize: '0.8rem',
                fontWeight: 600,
                color: 'var(--color-foreground)',
                textTransform: 'uppercase',
                letterSpacing: '0.5px',
              }}
            >
              Test Send
            </span>
          </div>

          <Form.Group className="mb-2">
            <Form.Control
              type="text"
              size="sm"
              placeholder="To (E.164, e.g. +15551234567)"
              value={sendTo}
              onChange={(e) => setSendTo(e.target.value)}
              style={{
                background: 'var(--surface-contrast, rgba(0,0,0,0.2))',
                border: '1px solid var(--color-border)',
                color: 'var(--color-foreground)',
                fontSize: '0.82rem',
              }}
            />
          </Form.Group>

          <Form.Group className="mb-2">
            <Form.Control
              as="textarea"
              rows={2}
              size="sm"
              placeholder="Message..."
              value={sendMessage}
              onChange={(e) => setSendMessage(e.target.value)}
              style={{
                background: 'var(--surface-contrast, rgba(0,0,0,0.2))',
                border: '1px solid var(--color-border)',
                color: 'var(--color-foreground)',
                fontSize: '0.82rem',
                resize: 'none',
              }}
            />
          </Form.Group>

          <div className="d-flex gap-2">
            <Button
              variant="success"
              size="sm"
              className="flex-grow-1"
              onClick={handleSend}
              disabled={sending || !sendTo.trim() || !sendMessage.trim()}
            >
              {sending ? (
                <Spinner animation="border" size="sm" style={{ width: 14, height: 14, borderWidth: 1.5 }} className="me-2" />
              ) : (
                <FaPaperPlane className="me-2" size={12} />
              )}
              Send
            </Button>
            <Button
              variant="outline-secondary"
              size="sm"
              onClick={handleStartPairing}
              disabled={pairing}
              title="Re-link phone"
            >
              <FaQrcode size={12} />
            </Button>
            <Button
              variant="outline-danger"
              size="sm"
              onClick={handleLogout}
              disabled={loggingOut}
              title="Disconnect WhatsApp"
            >
              {loggingOut ? (
                <Spinner animation="border" size="sm" style={{ width: 14, height: 14, borderWidth: 1.5 }} />
              ) : (
                <FaSignOutAlt size={12} />
              )}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
};

export default WhatsAppChannelCard;
