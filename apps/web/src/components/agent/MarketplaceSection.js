import { useEffect, useState } from 'react';
import { Col, Row, Spinner } from 'react-bootstrap';
import api from '../../services/api';

const MarketplaceSection = () => {
  const [listings, setListings] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState(null);
  const [notice, setNotice] = useState(null);

  const load = async () => {
    setLoading(true);
    try {
      const res = await api.get('/marketplace/listings');
      setListings(res.data || []);
    } catch (e) {
      setNotice({ type: 'danger', text: e?.response?.data?.detail || 'Failed to load marketplace' });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const subscribe = async (listingId) => {
    setBusyId(listingId);
    setNotice(null);
    try {
      const res = await api.post('/marketplace/subscribe', { listing_id: listingId });
      const status = res.data?.status || 'pending';
      setNotice({
        type: status === 'approved' ? 'success' : 'info',
        text: status === 'approved'
          ? 'Subscribed. Agent added to External Agents.'
          : 'Subscription request sent. Awaiting publisher approval.',
      });
    } catch (e) {
      setNotice({ type: 'danger', text: e?.response?.data?.detail || 'Subscribe failed' });
    } finally {
      setBusyId(null);
    }
  };

  return (
    <>
      <div className="ap-section-label" style={{ marginTop: 'var(--ap-space-6)' }}>
        Marketplace
      </div>

      {notice && (
        <div
          className="ap-card"
          style={{
            padding: 12,
            marginBottom: 12,
            color: notice.type === 'danger' ? 'var(--ap-danger)' : notice.type === 'success' ? 'var(--ap-success)' : 'var(--ap-text)',
          }}
        >
          {notice.text}
        </div>
      )}

      {loading ? (
        <div className="text-center py-4">
          <Spinner animation="border" size="sm" variant="primary" />
        </div>
      ) : listings.length === 0 ? (
        <div className="ap-empty">
          <p className="ap-empty-text" style={{ marginBottom: 0 }}>
            No agents have been published to the marketplace yet.
          </p>
        </div>
      ) : (
        <Row className="g-3">
          {listings.map((l) => (
            <Col key={l.id} md={6} xl={4}>
              <article className="ap-card" style={{ borderLeft: '4px solid var(--ap-accent)' }}>
                <div className="ap-card-body">
                  <div className="d-flex align-items-start justify-content-between mb-2">
                    <h3 className="ap-card-title" style={{ margin: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {l.name}
                    </h3>
                    <span className="ap-badge-solid" style={{ background: 'var(--ap-primary-tint)', color: 'var(--ap-primary)' }}>
                      {l.protocol}
                    </span>
                  </div>
                  <p className="ap-card-text" style={{ minHeight: 40 }}>
                    {l.description || '—'}
                  </p>
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
                    {(l.capabilities || []).slice(0, 5).map((c) => (
                      <span key={c} className="ap-badge-outline">{c}</span>
                    ))}
                  </div>
                  <div style={{ fontSize: 'var(--ap-fs-xs)', color: 'var(--ap-text-subtle)', marginBottom: 12 }}>
                    {l.pricing_model === 'free' ? 'Free' : `${l.pricing_model} · $${l.price_per_call_usd ?? 0}/call`}
                    {' · '}
                    {l.install_count || 0} installs
                  </div>
                  <footer className="d-flex justify-content-end">
                    <button
                      type="button"
                      className="ap-btn-primary ap-btn-sm"
                      disabled={busyId === l.id}
                      onClick={() => subscribe(l.id)}
                    >
                      {busyId === l.id ? 'Subscribing…' : 'Subscribe'}
                    </button>
                  </footer>
                </div>
              </article>
            </Col>
          ))}
        </Row>
      )}
    </>
  );
};

export default MarketplaceSection;
