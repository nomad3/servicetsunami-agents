import { useEffect, useMemo, useState } from 'react';
import { Alert, Badge, Button, Card, Col, Container, Form, ListGroup, Modal, Row, Spinner, Tab, Tabs } from 'react-bootstrap';
import { useAuth } from '../App';
import Layout from '../components/Layout';
import ReportVisualization from '../components/chat/ReportVisualization';
import agentKitService from '../services/agentKit';
import chatService from '../services/chat';
import datasetService from '../services/dataset';
import datasetGroupService from '../services/datasetGroup';

const initialSessionState = {
  datasetId: '',
  datasetGroupId: '',
  agentKitId: '',
  title: '',
  sourceType: 'dataset', // 'dataset' or 'group'
};

const ChatPage = () => {
  const auth = useAuth();
  const [sessions, setSessions] = useState([]);
  const [datasets, setDatasets] = useState([]);
  const [datasetGroups, setDatasetGroups] = useState([]);
  const [agentKits, setAgentKits] = useState([]);
  const [selectedSession, setSelectedSession] = useState(null);
  const [messages, setMessages] = useState([]);
  const [messageDraft, setMessageDraft] = useState('');
  const [loadingSessions, setLoadingSessions] = useState(false);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [postingMessage, setPostingMessage] = useState(false);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [sessionForm, setSessionForm] = useState(initialSessionState);
  const [formErrors, setFormErrors] = useState('');
  const [globalError, setGlobalError] = useState('');

  useEffect(() => {
    if (!auth.user) {
      return;
    }
    loadReferenceData();
    loadSessions();
  }, [auth.user]);

  const loadReferenceData = async () => {
    try {
      const [datasetsResp, groupsResp, agentKitsResp] = await Promise.all([
        datasetService.getAll(),
        datasetGroupService.getAll(),
        agentKitService.getAll(),
      ]);
      setDatasets(datasetsResp.data);
      setDatasetGroups(groupsResp.data);
      setAgentKits(agentKitsResp.data);
    } catch (err) {
      console.error(err);
      setGlobalError('Failed to load supporting data (datasets, groups, or agent kits).');
    }
  };

  const loadSessions = async () => {
    setLoadingSessions(true);
    setGlobalError('');
    try {
      const response = await chatService.listSessions();
      setSessions(response.data);
      if (response.data.length > 0 && !selectedSession) {
        handleSelectSession(response.data[0]);
      }
    } catch (err) {
      console.error(err);
      setGlobalError('Failed to fetch chat sessions.');
    } finally {
      setLoadingSessions(false);
    }
  };

  const loadMessages = async (sessionId) => {
    if (!sessionId) {
      setMessages([]);
      return;
    }
    setLoadingMessages(true);
    try {
      const response = await chatService.listMessages(sessionId);
      setMessages(response.data);
    } catch (err) {
      console.error(err);
      setGlobalError('Failed to load messages.');
    } finally {
      setLoadingMessages(false);
    }
  };

  const handleSelectSession = (session) => {
    setSelectedSession(session);
    loadMessages(session.id);
  };

  const handleMessageSubmit = async (event) => {
    event.preventDefault();
    if (!messageDraft.trim() || !selectedSession) {
      return;
    }
    setPostingMessage(true);
    setGlobalError('');
    try {
      const response = await chatService.postMessage(selectedSession.id, messageDraft.trim());
      setMessages((prev) => [...prev, response.data.user_message, response.data.assistant_message]);
      setMessageDraft('');
    } catch (err) {
      console.error(err);
      setGlobalError('Failed to send message to agent.');
    } finally {
      setPostingMessage(false);
    }
  };

  const handleCreateSessionModal = () => {
    setShowCreateModal(true);
    setSessionForm(initialSessionState);
    setFormErrors('');
    if (auth.user) {
      loadReferenceData();
    }
  };

  const handleCreateSessionChange = (event) => {
    const { name, value } = event.target;
    setSessionForm((prev) => ({ ...prev, [name]: value }));
  };

  const handleSourceTypeSelect = (k) => {
    setSessionForm((prev) => ({
      ...prev,
      sourceType: k,
      datasetId: k === 'dataset' ? prev.datasetId : '',
      datasetGroupId: k === 'group' ? prev.datasetGroupId : ''
    }));
  };

  const handleCreateSession = async (event) => {
    event.preventDefault();

    try {
      const payload = {
        title: sessionForm.title ? sessionForm.title.trim() : undefined,
      };

      if (sessionForm.agentKitId) {
        payload.agent_kit_id = sessionForm.agentKitId;
      }

      if (sessionForm.sourceType === 'dataset' && sessionForm.datasetId) {
        payload.dataset_id = sessionForm.datasetId;
      } else if (sessionForm.sourceType === 'group' && sessionForm.datasetGroupId) {
        payload.dataset_group_id = sessionForm.datasetGroupId;
      }

      const response = await chatService.createSession(payload);
      setSessions((prev) => [response.data, ...prev]);
      setShowCreateModal(false);
      setSelectedSession(response.data);
      loadMessages(response.data.id);
    } catch (err) {
      console.error(err);
      setFormErrors('Unable to create chat session. Ensure the selected dataset/group and agent kit are valid.');
    }
  };

  const datasetById = useMemo(() => {
    return datasets.reduce((acc, dataset) => {
      acc[dataset.id] = dataset;
      return acc;
    }, {});
  }, [datasets]);

  const groupById = useMemo(() => {
    return datasetGroups.reduce((acc, group) => {
      acc[group.id] = group;
      return acc;
    }, {});
  }, [datasetGroups]);

  const agentKitById = useMemo(() => {
    return agentKits.reduce((acc, kit) => {
      acc[kit.id] = kit;
      return acc;
    }, {});
  }, [agentKits]);

  const renderMessage = (message) => {
    const timeLabel = message.created_at ? new Date(message.created_at).toLocaleTimeString() : '';
    const queryResults = message.context?.query_results || [];

    return (
      <ListGroup.Item key={message.id} style={message.role === 'assistant' ? { background: 'var(--surface-contrast)' } : {}}>
        <div className="d-flex justify-content-between align-items-start">
          <div>
            <Badge bg={message.role === 'assistant' ? 'primary' : 'secondary'} className="me-2 text-uppercase">
              {message.role}
            </Badge>
            <span style={{ whiteSpace: 'pre-wrap' }}>{message.content}</span>
          </div>
          <small className="text-muted">{timeLabel}</small>
        </div>

        {/* Render Visualizations */}
        {queryResults.map((result, idx) => {
          if (result.tool === 'generate_report' || (result.tool && result.tool.startsWith('report_generation'))) {
            return <ReportVisualization key={idx} toolResult={result} />;
          }
          return null;
        })}

        {message.role === 'assistant' && message.context?.entities_extracted > 0 && (
          <div className="mt-2">
            <Badge bg="info" style={{ fontSize: '0.7rem', fontWeight: 500 }}>
              {message.context.entities_extracted} {message.context.entities_extracted === 1 ? 'entity' : 'entities'} extracted
            </Badge>
          </div>
        )}

        {message.context && message.context.summary && (
          <details className="mt-2">
            <summary>View agent context</summary>
            <pre className="rounded p-2 mt-2" style={{ whiteSpace: 'pre-wrap', background: 'var(--surface-page)', color: 'var(--color-soft)', border: '1px solid var(--color-border)' }}>
              {JSON.stringify(message.context, null, 2)}
            </pre>
          </details>
        )}
      </ListGroup.Item>
    );
  };

  const getSessionSubtitle = (session) => {
    let sourceName = 'Unknown Source';
    if (session.dataset_id && datasetById[session.dataset_id]) {
      sourceName = datasetById[session.dataset_id].name;
    } else if (session.dataset_group_id && groupById[session.dataset_group_id]) {
      sourceName = `${groupById[session.dataset_group_id].name} (Group)`;
    } else if (session.dataset_group_id) {
      sourceName = 'Dataset Group';
    }

    const kitName = (agentKitById[session.agent_kit_id] && agentKitById[session.agent_kit_id].name) || 'Agent Kit';
    return `${sourceName} · ${kitName}`;
  };

  return (
    <Layout>
      <Container fluid>
        <Row className="g-4">
          <Col lg={4} xl={3}>
            <div className="d-flex justify-content-between align-items-center mb-3">
              <h3 className="mb-0">Sessions</h3>
              <Button size="sm" variant="outline-primary" onClick={handleCreateSessionModal}>
                New session
              </Button>
            </div>
            {loadingSessions ? (
              <div className="d-flex align-items-center gap-2 text-muted">
                <Spinner animation="border" size="sm" />
                <span>Loading sessions…</span>
              </div>
            ) : (
              <ListGroup className="shadow-sm">
                {sessions.map((session) => (
                  <ListGroup.Item
                    action
                    key={session.id}
                    active={selectedSession && session.id === selectedSession.id}
                    onClick={() => handleSelectSession(session)}
                  >
                    <div className="fw-semibold">{session.title || 'Untitled session'}</div>
                    <div className="small text-muted">
                      {getSessionSubtitle(session)}
                    </div>
                  </ListGroup.Item>
                ))}
                {sessions.length === 0 && (
                  <ListGroup.Item className="text-muted text-center">
                    No sessions yet. Create one to start chatting with your data.
                  </ListGroup.Item>
                )}
              </ListGroup>
            )}
          </Col>

          <Col lg={8} xl={9}>
            {globalError && <Alert variant="danger">{globalError}</Alert>}
            {selectedSession ? (
              <Card className="shadow-sm">
                <Card.Header>
                  <div className="d-flex justify-content-between">
                    <div>
                      <h5 className="mb-0">{selectedSession.title || 'Agent session'}</h5>
                      <small className="text-muted">
                        {getSessionSubtitle(selectedSession)}
                      </small>
                    </div>
                  </div>
                </Card.Header>
                <Card.Body style={{ height: '60vh', display: 'flex', flexDirection: 'column' }}>
                  <div style={{ flex: 1, overflowY: 'auto' }}>
                    {loadingMessages ? (
                      <div className="d-flex align-items-center gap-2 text-muted">
                        <Spinner animation="border" size="sm" />
                        <span>Loading conversation…</span>
                      </div>
                    ) : (
                      <ListGroup variant="flush">
                        {messages.map((message) => renderMessage(message))}
                        {messages.length === 0 && (
                          <div className="py-4">
                            <div className="text-center text-muted mb-4">
                              <h5>Get Started</h5>
                              <p>Ask your AI assistant about your data. Try one of these:</p>
                            </div>
                            <div className="row g-2 px-3">
                              {[
                                { icon: '📊', text: 'What was our revenue last month?' },
                                { icon: '📈', text: 'Show me the top trends in our data' },
                                { icon: '🎯', text: 'What are the key insights from this dataset?' },
                                { icon: '📋', text: 'Generate a summary report' },
                                { icon: '🔮', text: 'What is the forecast for next quarter?' },
                                { icon: '⚡', text: 'What anomalies or issues should I know about?' },
                              ].map((prompt, idx) => (
                                <div key={idx} className="col-md-6">
                                  <Button
                                    variant="outline-secondary"
                                    className="w-100 text-start py-2 px-3"
                                    style={{ borderRadius: '12px', fontSize: '0.9rem' }}
                                    onClick={() => {
                                      setMessageDraft(prompt.text);
                                      document.getElementById('chatMessage')?.focus();
                                    }}
                                  >
                                    <span className="me-2">{prompt.icon}</span>
                                    {prompt.text}
                                  </Button>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}
                      </ListGroup>
                    )}
                  </div>
                  <Form onSubmit={handleMessageSubmit} className="pt-3">
                    <Row className="g-2 align-items-end">
                      <Col xs={12} md={9}>
                        <Form.Group controlId="chatMessage">
                          <Form.Label className="visually-hidden">Message</Form.Label>
                          <Form.Control
                            as="textarea"
                            rows={2}
                            placeholder="Ask a question or request an action."
                            value={messageDraft}
                            onChange={(event) => setMessageDraft(event.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter' && !e.shiftKey) {
                                e.preventDefault();
                                handleMessageSubmit(e);
                              }
                            }}
                          />
                        </Form.Group>
                      </Col>
                      <Col xs={12} md={3} className="d-grid">
                        <Button type="submit" variant="primary" disabled={postingMessage || !messageDraft.trim()}>
                          {postingMessage ? 'Sending…' : 'Send'}
                        </Button>
                      </Col>
                    </Row>
                  </Form>
                </Card.Body>
              </Card>
            ) : (
              <Card className="shadow-sm">
                <Card.Body className="text-center text-muted">
                  <p className="mb-1">Select a session to view the conversation.</p>
                  <p className="mb-0">Need a new one? Click “New session” and choose a dataset plus agent kit.</p>
                </Card.Body>
              </Card>
            )}
          </Col>
        </Row>
      </Container>

      <Modal show={showCreateModal} onHide={() => setShowCreateModal(false)} centered>
        <Form onSubmit={handleCreateSession}>
          <Modal.Header closeButton>
            <Modal.Title>Start new agent session</Modal.Title>
          </Modal.Header>
          <Modal.Body>
            {formErrors && <Alert variant="danger">{formErrors}</Alert>}
            <Form.Group className="mb-3">
              <Form.Label>Title</Form.Label>
              <Form.Control
                type="text"
                name="title"
                placeholder="Optional label (e.g. Q4 forecast review)"
                value={sessionForm.title}
                onChange={handleCreateSessionChange}
              />
            </Form.Group>

            <Form.Group className="mb-3">
              <Form.Label>Data Source</Form.Label>
              <Tabs
                activeKey={sessionForm.sourceType}
                onSelect={handleSourceTypeSelect}
                className="mb-2"
              >
                <Tab eventKey="dataset" title="Single Dataset">
                  <Form.Select
                    name="datasetId"
                    value={sessionForm.datasetId}
                    onChange={handleCreateSessionChange}
                    disabled={sessionForm.sourceType !== 'dataset'}
                  >
                    <option value="">Select dataset…</option>
                    {datasets.map((dataset) => (
                      <option key={dataset.id} value={dataset.id}>
                        {dataset.name}
                      </option>
                    ))}
                  </Form.Select>
                </Tab>
                <Tab eventKey="group" title="Dataset Group">
                  <Form.Select
                    name="datasetGroupId"
                    value={sessionForm.datasetGroupId}
                    onChange={handleCreateSessionChange}
                    disabled={sessionForm.sourceType !== 'group'}
                  >
                    <option value="">Select dataset group…</option>
                    {datasetGroups.map((group) => (
                      <option key={group.id} value={group.id}>
                        {group.name} ({group.datasets ? group.datasets.length : 0} datasets)
                      </option>
                    ))}
                  </Form.Select>
                </Tab>
              </Tabs>
            </Form.Group>

            <Form.Group className="mb-3">
              <Form.Label>Agent Kit</Form.Label>
              <Form.Select name="agentKitId" value={sessionForm.agentKitId} onChange={handleCreateSessionChange}>
                <option value="">Select agent kit…</option>
                {agentKits.map((kit) => (
                  <option key={kit.id} value={kit.id}>
                    {kit.name} (v{kit.version || '1.0'})
                  </option>
                ))}
              </Form.Select>
            </Form.Group>
          </Modal.Body>
          <Modal.Footer>
            <Button variant="outline-secondary" onClick={() => setShowCreateModal(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary">
              Create session
            </Button>
          </Modal.Footer>
        </Form>
      </Modal>
    </Layout>
  );
};

export default ChatPage;
