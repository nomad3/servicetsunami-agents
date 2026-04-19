import { useState, useEffect, useRef } from 'react';
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Form,
  Modal,
  Row,
  Spinner,
  Table
} from 'react-bootstrap';
import {
  FaMicrochip,
  FaPlus,
  FaVideo,
  FaCheckCircle,
  FaTimesCircle,
  FaSyncAlt,
  FaTrash,
  FaSatelliteDish,
  FaCamera
} from 'react-icons/fa';
import api from '../services/api';
import './DevicePanel.css';

// ─── Camera View Component ───────────────────────────────────────────────────

const CameraView = ({ device }) => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [streamActive, setStreamActive] = useState(false);
  const videoRef = useRef(null);
  const pcRef = useRef(null);

  const startStream = async () => {
    try {
      setLoading(true);
      setError(null);

      // 1. Create peer connection
      const pc = new RTCPeerConnection({
        iceServers: [{ urls: 'stun:stun.l.google.com:19302' }]
      });
      pcRef.current = pc;

      // 2. Handle remote track
      pc.ontrack = (event) => {
        if (videoRef.current) {
          videoRef.current.srcObject = event.streams[0];
        }
      };

      // 3. Create offer
      const offer = await pc.createOffer({ offerToReceiveVideo: true });
      await pc.setLocalDescription(offer);

      // 4. Send to bridge. In production the main API will proxy this to the
      // bridge so the browser doesn't have to hit the LAN directly.
      const bridgeUrl = device.config?.bridge_url || 'http://localhost:8088';

      if (window.location.protocol === 'https:' && bridgeUrl.startsWith('http:')) {
        throw new Error(
          'Mixed content blocked: page served over HTTPS cannot fetch bridge on HTTP. ' +
          'Configure the bridge behind HTTPS or proxy via the main API.'
        );
      }

      const bridgeToken = device.config?.bridge_token || '';
      const headers = { 'Content-Type': 'application/json' };
      if (bridgeToken) headers['X-Bridge-Token'] = bridgeToken;

      const response = await fetch(`${bridgeUrl}/bridge/connect`, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          device_id: device.device_id,
          sdp: pc.localDescription.sdp,
          type: pc.localDescription.type
        })
      });

      if (!response.ok) throw new Error('Bridge connection failed');
      const answer = await response.json();

      // 5. Set remote description
      await pc.setRemoteDescription(new RTCSessionDescription(answer));
      setStreamActive(true);
    } catch (err) {
      console.error('Stream failed:', err);
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const stopStream = () => {
    if (pcRef.current) {
      pcRef.current.close();
      pcRef.current = null;
    }
    if (videoRef.current) {
      videoRef.current.srcObject = null;
    }
    setStreamActive(false);
  };

  useEffect(() => {
    return () => stopStream();
  }, []);

  return (
    <div className="camera-view">
      <div className="camera-display bg-black rounded overflow-hidden position-relative" style={{ aspectRatio: '16/9' }}>
        {error && (
          <div className="position-absolute top-0 start-0 w-100 h-100 d-flex flex-column align-items-center justify-content-center text-danger bg-dark bg-opacity-75">
            <FaTimesCircle size={32} className="mb-2" />
            <span>{error}</span>
            <Button variant="outline-light" size="sm" className="mt-2" onClick={startStream}>Retry</Button>
          </div>
        )}
        
        {loading && (
          <div className="position-absolute top-0 start-0 w-100 h-100 d-flex align-items-center justify-content-center text-light bg-dark bg-opacity-50">
            <Spinner animation="border" size="sm" className="me-2" />
            <span>Connecting...</span>
          </div>
        )}

        <video
          ref={videoRef}
          autoPlay
          playsInline
          muted
          className="w-100 h-100"
          style={{ objectFit: 'contain', display: streamActive ? 'block' : 'none' }}
        />

        {!streamActive && !loading && !error && (
          <div className="position-absolute top-0 start-0 w-100 h-100 d-flex flex-column align-items-center justify-content-center text-light">
            <FaVideo size={48} className="mb-3 opacity-25" />
            <Button variant="primary" onClick={startStream}>Start Live Stream</Button>
          </div>
        )}

        {streamActive && (
          <div className="position-absolute bottom-0 end-0 p-2">
            <Button variant="danger" size="sm" onClick={stopStream}>Stop</Button>
          </div>
        )}
      </div>
      <div className="mt-2 small text-muted d-flex justify-content-between">
        <span>{device.device_name}</span>
        <Badge bg={device.status === 'online' ? 'success' : 'secondary'}>{device.status}</Badge>
      </div>
    </div>
  );
};

// ─── Main Device Panel ───────────────────────────────────────────────────────

const DevicePanel = () => {
  const [devices, setDevices] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [showAddModal, setShowAddModal] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [newToken, setNewToken] = useState(null);
  
  const [form, setForm] = useState({
    device_name: '',
    device_type: 'camera',
    rtsp_url: '',
    username: '',
    password: '',
    bridge_url: 'http://localhost:8088'
  });

  const fetchDevices = async () => {
    try {
      setLoading(true);
      const res = await api.get('/devices');
      setDevices(res.data);
    } catch (err) {
      setError("Failed to load devices");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchDevices();
  }, []);

  const handleAdd = async (e) => {
    e.preventDefault();
    try {
      setSubmitting(true);
      const res = await api.post('/devices', {
        device_name: form.device_name,
        device_type: form.device_type,
        capabilities: ['video', 'rtsp-relay'],
        config: {
          rtsp_url: form.rtsp_url,
          username: form.username,
          password: form.password,
          bridge_url: form.bridge_url
        }
      });

      setNewToken(res.data.device_token);
      fetchDevices();
    } catch (err) {
      setError("Failed to register device");
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (id) => {
    if (!window.confirm("Remove this device?")) return;
    try {
      await api.delete(`/devices/${id}`);
      fetchDevices();
    } catch (err) {
      setError("Failed to remove device");
    }
  };

  return (
    <div className="device-panel p-3">
      <div className="d-flex justify-content-between align-items-center mb-4">
        <div>
          <h4 className="mb-1"><FaMicrochip className="me-2" />Device Registry</h4>
          <p className="text-muted small mb-0">Connect local IoT hardware, cameras, and robots to Luna's brain.</p>
        </div>
        <Button variant="primary" onClick={() => setShowAddModal(true)}>
          <FaPlus className="me-2" />Add Device
        </Button>
      </div>

      {error && <Alert variant="danger" dismissible onClose={() => setError(null)}>{error}</Alert>}

      {loading ? (
        <div className="text-center py-5"><Spinner animation="border" /></div>
      ) : (
        <Row className="g-4">
          {devices.map(device => (
            <Col key={device.id} md={6} xl={4}>
              <Card className="h-100 device-card">
                <Card.Header className="d-flex justify-content-between align-items-center bg-transparent border-0 pt-3 px-3">
                  <div className="d-flex align-items-center">
                    <div className="device-type-icon me-2">
                      {device.device_type === 'camera' ? <FaCamera /> : <FaMicrochip />}
                    </div>
                    <strong className="text-truncate" style={{ maxWidth: '150px' }}>{device.device_name}</strong>
                  </div>
                  <Badge bg={device.status === 'online' ? 'success' : 'secondary'}>{device.status}</Badge>
                </Card.Header>
                <Card.Body className="p-3">
                  {device.device_type === 'camera' ? (
                    <CameraView device={device} />
                  ) : (
                    <div className="device-placeholder p-4 text-center text-muted border rounded">
                      <FaSatelliteDish size={32} className="mb-2 opacity-25" />
                      <p className="small mb-0">No preview for {device.device_type}</p>
                    </div>
                  )}
                  
                  <div className="mt-3 pt-3 border-top d-flex justify-content-between align-items-center">
                    <small className="text-muted">ID: {device.device_id.split('-').pop()}</small>
                    <div className="actions">
                      <Button variant="link" className="text-danger p-0 ms-2" onClick={() => handleDelete(device.device_id)}><FaTrash /></Button>
                    </div>
                  </div>
                </Card.Body>
              </Card>
            </Col>
          ))}

          {devices.length === 0 && (
            <Col xs={12}>
              <div className="text-center py-5 text-muted border rounded bg-light bg-opacity-10">
                <FaMicrochip size={48} className="mb-3 opacity-25" />
                <h5>No devices registered</h5>
                <p>Register your first local device or camera to extend Luna's reach.</p>
                <Button variant="outline-primary" onClick={() => setShowAddModal(true)}>Register Device</Button>
              </div>
            </Col>
          )}
        </Row>
      )}

      {/* Add Device Modal */}
      <Modal show={showAddModal} onHide={() => { setShowAddModal(false); setNewToken(null); }} size="lg">
        <Modal.Header closeButton>
          <Modal.Title>Register New Device</Modal.Title>
        </Modal.Header>
        <Modal.Body>
          {newToken ? (
            <Alert variant="warning">
              <h6>Device Token Generated!</h6>
              <p>Save this token now. It will not be shown again:</p>
              <code className="d-block p-3 bg-dark text-warning rounded mb-3" style={{ fontSize: '1.2rem' }}>{newToken}</code>
              <p className="mb-0 small">Use this token in your Device Bridge or IoT client to authenticate with Luna.</p>
            </Alert>
          ) : (
            <Form onSubmit={handleAdd}>
              <Row>
                <Col md={6}>
                  <Form.Group className="mb-3">
                    <Form.Label>Device Name</Form.Label>
                    <Form.Control 
                      type="text" 
                      placeholder="e.g. Kitchen Camera" 
                      value={form.device_name} 
                      onChange={e => setForm({...form, device_name: e.target.value})} 
                      required 
                    />
                  </Form.Group>
                </Col>
                <Col md={6}>
                  <Form.Group className="mb-3">
                    <Form.Label>Device Type</Form.Label>
                    <Form.Select value={form.device_type} onChange={e => setForm({...form, device_type: e.target.value})}>
                      <option value="camera">Camera (RTSP/EZVIZ)</option>
                      <option value="robot">Robot (Luna Desk/IoT)</option>
                      <option value="necklace">Wearable (Necklace)</option>
                      <option value="sensor">Sensor Node</option>
                    </Form.Select>
                  </Form.Group>
                </Col>
              </Row>

              <hr />
              <h6>Local Connection Settings</h6>
              <p className="text-muted small">These settings are used by the Device Bridge to connect to your local hardware.</p>

              <Form.Group className="mb-3">
                <Form.Label>RTSP Stream URL</Form.Label>
                <Form.Control 
                  type="text" 
                  placeholder="rtsp://192.168.1.100:554/stream1" 
                  value={form.rtsp_url} 
                  onChange={e => setForm({...form, rtsp_url: e.target.value})} 
                />
                <Form.Text className="text-muted">For EZVIZ H6: <code>rtsp://[IP]:554/h264/ch01/main/av_stream</code></Form.Text>
              </Form.Group>

              <Row>
                <Col md={6}>
                  <Form.Group className="mb-3">
                    <Form.Label>RTSP Username</Form.Label>
                    <Form.Control 
                      type="text" 
                      placeholder="admin" 
                      value={form.username} 
                      onChange={e => setForm({...form, username: e.target.value})} 
                    />
                  </Form.Group>
                </Col>
                <Col md={6}>
                  <Form.Group className="mb-3">
                    <Form.Label>RTSP Password / Verification Code</Form.Label>
                    <Form.Control 
                      type="password" 
                      value={form.password} 
                      onChange={e => setForm({...form, password: e.target.value})} 
                    />
                  </Form.Group>
                </Col>
              </Row>

              <Form.Group className="mb-3">
                <Form.Label>Device Bridge URL</Form.Label>
                <Form.Control 
                  type="text" 
                  value={form.bridge_url} 
                  onChange={e => setForm({...form, bridge_url: e.target.value})} 
                />
                <Form.Text className="text-muted">The local IP:Port where your Device Bridge microservice is running.</Form.Text>
              </Form.Group>

              <div className="text-end mt-4">
                <Button variant="secondary" className="me-2" onClick={() => setShowAddModal(false)}>Cancel</Button>
                <Button variant="primary" type="submit" disabled={submitting}>
                  {submitting ? <Spinner size="sm" /> : "Register & Generate Token"}
                </Button>
              </div>
            </Form>
          )}
        </Modal.Body>
      </Modal>
    </div>
  );
};

export default DevicePanel;
