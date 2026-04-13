import { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Badge, Button, Card, Col, Container, Form, ListGroup, Modal, Row, Spinner } from 'react-bootstrap';
import { FaCopy } from 'react-icons/fa';
import { useTranslation } from 'react-i18next';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useAuth } from '../App';
import Layout from '../components/Layout';
import FeedbackActions from '../components/chat/FeedbackActions';
import ReportVisualization from '../components/chat/ReportVisualization';
import CollaborationPanel from '../components/CollaborationPanel';
// LunaAvatar removed
import { useLunaPresence } from '../context/LunaPresenceContext';
import agentKitService from '../services/agentKit';
import chatService from '../services/chat';
import './ChatPage.css';

const initialSessionState = {
  agentKitId: '',
  title: '',
};

const ChatPage = () => {
  const { t } = useTranslation('chat');
  const auth = useAuth();
  const lunaCtx = useLunaPresence();
  const lunaState = lunaCtx?.presence?.state || 'idle';
  const lunaMood = lunaCtx?.presence?.mood || 'calm';
  const [sessions, setSessions] = useState([]);
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
  const [attachedFile, setAttachedFile] = useState(null);
  const [streamingText, setStreamingText] = useState('');
  const [responseEmotion, setResponseEmotion] = useState(null);
  const responseEmotionTimerRef = useRef(null);
  const streamAbortRef = useRef(null);
  const [isRecording, setIsRecording] = useState(false);
  const [ttsEnabled, setTtsEnabled] = useState(false);
  const [speakingMessageId, setSpeakingMessageId] = useState(null);
  const fileInputRef = useRef(null);
  const messagesEndRef = useRef(null);
  const mediaRecorderRef = useRef(null);
  const audioChunksRef = useRef([]);

  // Collaboration state
  const [activeCollaboration, setActiveCollaboration] = useState(null);
  const [showCollabPanel, setShowCollabPanel] = useState(false);
  const sessionEventsRef = useRef(null);
  const API_BASE = process.env.REACT_APP_API_BASE_URL || '';

  const PATTERN_PHASES = {
    incident_investigation: ['triage', 'investigate', 'analyze', 'command'],
    research_synthesize: ['research', 'synthesize'],
    plan_verify: ['plan', 'verify'],
    propose_critique_revise: ['propose', 'critique', 'revise'],
  };

  // Auto-scroll to bottom when messages change or typing indicator appears
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, postingMessage]);

  // Auto-speak last assistant message when TTS is enabled
  useEffect(() => {
    if (!ttsEnabled || postingMessage || messages.length === 0) return;
    const last = messages[messages.length - 1];
    if (last.role === 'assistant' && last.id && !last.id.startsWith('temp-')) {
      speakText(last.content, last.id);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages, ttsEnabled]);

  useEffect(() => {
    if (!auth.user) {
      return;
    }
    loadReferenceData();
    loadSessions();
  }, [auth.user]);

  useEffect(() => {
    if (agentKits.length === 1 && !sessionForm.agentKitId) {
      setSessionForm(prev => ({ ...prev, agentKitId: agentKits[0].id }));
    }
  }, [agentKits]);

  // Abort any active stream on unmount and clear emotion timer
  useEffect(() => {
    return () => {
      if (streamAbortRef.current) {
        streamAbortRef.current.abort();
      }
      if (sessionEventsRef.current) {
        sessionEventsRef.current.abort();
      }
      if (responseEmotionTimerRef.current) {
        clearTimeout(responseEmotionTimerRef.current);
      }
    };
  }, []);

  // Open long-lived session events SSE when a session is selected.
  // fetch() used instead of EventSource because EventSource cannot send Authorization headers.
  useEffect(() => {
    const sessionId = selectedSession?.id;
    const token = auth.user?.access_token || '';
    if (!sessionId || !token) return;

    const ctrl = new AbortController();
    sessionEventsRef.current = ctrl;

    (async () => {
      try {
        const res = await fetch(
          `${API_BASE}/api/v1/chat/sessions/${sessionId}/events`,
          { headers: { Authorization: `Bearer ${token}` }, signal: ctrl.signal }
        );
        if (!res.ok) return;
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = '';
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          const lines = buf.split('\n');
          buf = lines.pop();
          for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            try {
              const data = JSON.parse(line.slice(6));
              if (data.event_type === 'collaboration_started' && data.payload) {
                const { collaboration_id, agents } = data.payload;
                const phases = Array.isArray(agents)
                  ? agents.map(a => a.phase).filter(Boolean)
                  : [];
                setActiveCollaboration({
                  id: collaboration_id,
                  phases: phases.length ? phases : ['triage', 'investigate', 'analyze', 'command'],
                  isCompleted: false,
                });
                setShowCollabPanel(true);
              } else if (data.event_type === 'collaboration_completed') {
                setActiveCollaboration(prev => prev ? { ...prev, isCompleted: true } : prev);
                const finalReport = data.payload?.final_report;
                if (finalReport) {
                  setMessages(prev => [...prev, {
                    id: `collab-report-${data.payload?.collaboration_id || Date.now()}`,
                    role: 'assistant',
                    content: `**A2A Collaboration Complete**\n\n${finalReport}`,
                    created_at: new Date().toISOString(),
                  }]);
                }
              }
            } catch (e) {
              if (process.env.NODE_ENV === 'development') console.debug('[ChatPage SSE]', e);
            }
          }
        }
      } catch (e) {
        if (e.name !== 'AbortError') console.warn('[ChatPage] session SSE error', e);
      }
    })();

    return () => { ctrl.abort(); };
  }, [selectedSession?.id, auth.user?.access_token]);

  // Set emotion from assistant response, auto-revert after 10s
  const applyResponseEmotion = (emotion) => {
    if (!emotion) return;
    if (responseEmotionTimerRef.current) {
      clearTimeout(responseEmotionTimerRef.current);
    }
    setResponseEmotion(emotion);
    responseEmotionTimerRef.current = setTimeout(() => {
      setResponseEmotion(null);
      responseEmotionTimerRef.current = null;
    }, 10000);
  };

  const loadReferenceData = async () => {
    try {
      const agentKitsResp = await agentKitService.getAll();
      setAgentKits(agentKitsResp.data);
    } catch (err) {
      console.error(err);
      setGlobalError(t('errors.loadKits'));
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
      setGlobalError(t('errors.loadSessions'));
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
      setGlobalError(t('errors.loadMessages'));
    } finally {
      setLoadingMessages(false);
    }
  };

  // ── Voice helpers ────────────────────────────────────────────────────────

  const stripMarkdown = (text) => {
    return text
      .replace(/#{1,6}\s+/g, '')           // headings
      .replace(/(\*\*|__)(.*?)\1/g, '$2')  // bold
      .replace(/(\*|_)(.*?)\1/g, '$2')     // italic
      .replace(/`{1,3}[^`]*`{1,3}/g, '')   // code
      .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1') // links
      .replace(/^[-*+]\s+/gm, '')          // list bullets
      .replace(/^\d+\.\s+/gm, '')          // numbered list
      .replace(/>\s+/g, '')                // blockquotes
      .replace(/\n{2,}/g, '. ')            // paragraph breaks → pause
      .trim();
  };

  const speakText = (text, messageId) => {
    if (!window.speechSynthesis) return;
    window.speechSynthesis.cancel();
    if (speakingMessageId === messageId) {
      setSpeakingMessageId(null);
      return;
    }
    const utterance = new SpeechSynthesisUtterance(stripMarkdown(text));
    utterance.rate = 1.05;
    utterance.pitch = 1.0;
    // Prefer a natural-sounding voice if available
    const voices = window.speechSynthesis.getVoices();
    const preferred = voices.find(v => /samantha|karen|google us english|zira/i.test(v.name));
    if (preferred) utterance.voice = preferred;
    utterance.onend = () => setSpeakingMessageId(null);
    utterance.onerror = () => setSpeakingMessageId(null);
    setSpeakingMessageId(messageId);
    window.speechSynthesis.speak(utterance);
  };

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mimeType = MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : 'audio/ogg';
      const recorder = new MediaRecorder(stream, { mimeType });
      audioChunksRef.current = [];
      recorder.ondataavailable = (e) => { if (e.data.size > 0) audioChunksRef.current.push(e.data); };
      recorder.onstop = () => {
        stream.getTracks().forEach(t => t.stop());
        const blob = new Blob(audioChunksRef.current, { type: mimeType });
        const ext = mimeType.includes('webm') ? 'webm' : 'ogg';
        const file = new File([blob], `voice-message.${ext}`, { type: mimeType });
        setAttachedFile(file);
        setIsRecording(false);
      };
      mediaRecorderRef.current = recorder;
      recorder.start();
      setIsRecording(true);
    } catch {
      alert('Microphone access denied. Please allow microphone permissions.');
    }
  };

  const stopRecording = () => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      mediaRecorderRef.current.stop();
    }
  };

  const handleSelectSession = (session) => {
    if (streamAbortRef.current) {
      streamAbortRef.current.abort();
      streamAbortRef.current = null;
      setStreamingText('');
      setPostingMessage(false);
    }
    setSelectedSession(session);
    setActiveCollaboration(null);
    setShowCollabPanel(false);
    if (sessionEventsRef.current) {
      sessionEventsRef.current.abort();
      sessionEventsRef.current = null;
    }
    loadMessages(session.id);

    // Rehydrate collaboration panel if this session has a prior collaboration
    const token = auth.user?.access_token || '';
    if (token) {
      fetch(`${API_BASE}/api/v1/chat/sessions/${session.id}/collaborations`, {
        headers: { Authorization: `Bearer ${token}` },
      })
        .then(res => res.ok ? res.json() : null)
        .then(collabs => {
          if (!Array.isArray(collabs) || collabs.length === 0) return;
          const latest = collabs[0];
          const phases = PATTERN_PHASES[latest.pattern] || ['triage', 'investigate', 'analyze', 'command'];
          setActiveCollaboration({
            id: latest.id,
            phases,
            isCompleted: latest.status === 'completed',
          });
          setShowCollabPanel(true);
        })
        .catch(() => { /* non-fatal */ });
    }
  };

  const handleMessageSubmit = async (event) => {
    event.preventDefault();
    if ((!messageDraft.trim() && !attachedFile) || !selectedSession) {
      return;
    }
    const sentText = messageDraft.trim();
    const sentFile = attachedFile;

    // Immediately show the user's message + typing indicator
    const tempUserMsg = {
      id: `temp-${Date.now()}`,
      role: 'user',
      content: sentText || (sentFile ? `[${sentFile.name}]` : ''),
      created_at: new Date().toISOString(),
      context: sentFile ? { attachment: { type: sentFile.type.split('/')[0], name: sentFile.name } } : null,
    };
    setMessages((prev) => [...prev, tempUserMsg]);
    setMessageDraft('');
    setPostingMessage(true);
    setGlobalError('');

    if (sentFile) {
      setAttachedFile(null);
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }

    if (sentFile) {
      // File upload: no streaming, use existing endpoint
      try {
        const response = await chatService.postMessageWithFile(selectedSession.id, sentText, sentFile);
        setMessages((prev) => {
          const withoutTemp = prev.filter((m) => m.id !== tempUserMsg.id);
          return [...withoutTemp, response.data.user_message, response.data.assistant_message];
        });
        // Extract emotion from file upload response
        const assistantMsg = response.data.assistant_message;
        const emotion = assistantMsg?.emotion || assistantMsg?.context?.emotion;
        applyResponseEmotion(emotion);
      } catch (err) {
        console.error(err);
        const detail = err?.response?.data?.detail || err?.response?.data?.error || err?.message || '';
        const userMsg = detail.includes('timeout') || detail.includes('timed out')
          ? t('errors.timeout')
          : detail.includes('connection') || detail.includes('Connection')
          ? t('errors.connection')
          : detail
          ? t('errors.genericDetail', { detail: detail.slice(0, 150) })
          : t('errors.generic');
        setGlobalError(userMsg);
        setMessages((prev) => prev.filter((m) => m.id !== tempUserMsg.id));
      } finally {
        setPostingMessage(false);
      }
      return;
    }

    // Text message: stream 2 tokens at a time
    setStreamingText('');
    const ctrl = chatService.postMessageStream(
      selectedSession.id,
      sentText,
      // onToken
      (chunk) => setStreamingText((prev) => prev + chunk),
      // onUserSaved — swap temp user message with persisted one
      (userMsg) => setMessages((prev) => {
        const withoutTemp = prev.filter((m) => m.id !== tempUserMsg.id);
        return [...withoutTemp, userMsg];
      }),
      // onDone — replace streaming bubble with final persisted message
      (assistantMsg) => {
        setStreamingText('');
        setPostingMessage(false);
        setMessages((prev) => [...prev, assistantMsg]);
        // Extract emotion from response context for avatar reaction
        const emotion = assistantMsg?.emotion || assistantMsg?.context?.emotion;
        applyResponseEmotion(emotion);
      },
      // onError
      (errMsg) => {
        setStreamingText('');
        setPostingMessage(false);
        setGlobalError(errMsg || t('errors.generic'));
        setMessages((prev) => prev.filter((m) => m.id !== tempUserMsg.id));
      },
    );
    streamAbortRef.current = ctrl;
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

  const handleCreateSession = async (event) => {
    event.preventDefault();

    try {
      const payload = {
        title: sessionForm.title ? sessionForm.title.trim() : undefined,
      };

      if (sessionForm.agentKitId) {
        payload.agent_kit_id = sessionForm.agentKitId;
      }

      const response = await chatService.createSession(payload);
      setSessions((prev) => [response.data, ...prev]);
      setShowCreateModal(false);
      setSelectedSession(response.data);
      loadMessages(response.data.id);
    } catch (err) {
      console.error(err);
      setFormErrors(t('errors.createSession'));
    }
  };

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
            {message.role === 'user' && message.context?.attachment && (
              <Badge bg="outline-info" className="me-2" style={{ border: '1px solid var(--bs-info)', color: 'var(--bs-info)', fontSize: '0.7rem' }}>
                {message.context.attachment.type === 'image' ? '\uD83D\uDDBC\uFE0F' : message.context.attachment.type === 'audio' ? '\uD83C\uDFA4' : '\uD83D\uDCC4'}{' '}
                {message.context.attachment.filename || 'file'}
              </Badge>
            )}
            {message.role === 'assistant' ? (
              <div className="chat-markdown" style={{ fontSize: '0.9rem', lineHeight: 1.6 }}>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
              </div>
            ) : (
              <span style={{ whiteSpace: 'pre-wrap' }}>{message.content}</span>
            )}
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

        {message.role === 'assistant' && (
          <div className="mt-2 d-flex align-items-center gap-2">
            <FeedbackActions
              trajectoryId={message.id}
              stepIndex={0}
            />
            <button
              type="button"
              title="Copy response"
              onClick={() => navigator.clipboard.writeText(message.content)}
              style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '2px 4px', opacity: 0.6 }}
            >
              <FaCopy size={14} />
            </button>
            {window.speechSynthesis && (
              <button
                type="button"
                title={speakingMessageId === message.id ? 'Stop speaking' : 'Read aloud'}
                onClick={() => speakText(message.content, message.id)}
                style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '2px 4px', fontSize: '1rem', opacity: 0.6 }}
              >
                {speakingMessageId === message.id ? '🔇' : '🔊'}
              </button>
            )}
          </div>
        )}

        {message.role === 'assistant' && message.context?.entities_extracted > 0 && (
          <div className="mt-2">
            <Badge bg="info" style={{ fontSize: '0.7rem', fontWeight: 500 }}>
              {t('entitiesExtracted', { count: message.context.entities_extracted })}
            </Badge>
          </div>
        )}

        {message.context && message.context.summary && (
          <details className="mt-2">
            <summary>{t('viewContext')}</summary>
            <pre className="rounded p-2 mt-2" style={{ whiteSpace: 'pre-wrap', background: 'var(--surface-page)', color: 'var(--color-soft)', border: '1px solid var(--color-border)' }}>
              {JSON.stringify(message.context, null, 2)}
            </pre>
          </details>
        )}
      </ListGroup.Item>
    );
  };

  const getSessionSubtitle = (session) => {
    const kitName = (agentKitById[session.agent_kit_id] && agentKitById[session.agent_kit_id].name) || t('agentKit');
    return kitName;
  };

  return (
    <Layout>
      <Container fluid className="chat-page-container">
        <Row className="g-4">
          <Col lg={4} xl={3} className="chat-sessions-col">
            <div className="d-flex justify-content-between align-items-center mb-3">
              <h3 className="mb-0">{t('sessions')}</h3>
              <Button size="sm" variant="outline-primary" onClick={handleCreateSessionModal}>
                {t('newSession')}
              </Button>
            </div>
            {loadingSessions ? (
              <div className="d-flex align-items-center gap-2 text-muted">
                <Spinner animation="border" size="sm" />
                <span>{t('loadingSessions')}</span>
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
                    <div className="fw-semibold">{session.title || t('untitledSession')}</div>
                    <div className="small text-muted">
                      {getSessionSubtitle(session)}
                    </div>
                  </ListGroup.Item>
                ))}
                {sessions.length === 0 && (
                  <ListGroup.Item className="text-muted text-center">
                    {t('noSessions')}
                  </ListGroup.Item>
                )}
              </ListGroup>
            )}
          </Col>

          <Col lg={8} xl={9} className="chat-main-col">
            {globalError && <Alert variant="danger">{globalError}</Alert>}
            <div style={{ display: 'flex', gap: '1rem', height: '100%' }}>
            <div style={{ flex: 1, minWidth: 0 }}>
            {selectedSession ? (
              <Card className="shadow-sm">
                {/* ═══ Session Header ═══ */}
                <div
                  className="luna-tamagotchi-panel"
                  style={{
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    justifyContent: 'center',
                    padding: '16px',
                    borderBottom: '1px solid var(--bs-border-color, rgba(255,255,255,0.08))',
                  }}
                >
                  <div style={{ marginTop: 4, textAlign: 'center', zIndex: 1 }}>
                    <h6 className="mb-0" style={{ fontSize: '0.95rem', opacity: 0.9 }}>
                      {selectedSession.title || t('agentSession')}
                    </h6>
                    <small className="text-muted" style={{ fontSize: '0.7rem' }}>
                      {getSessionSubtitle(selectedSession)}
                    </small>
                  </div>
                  {/* TTS toggle */}
                  {window.speechSynthesis && (
                    <Button
                      size="sm"
                      variant={ttsEnabled ? 'primary' : 'outline-secondary'}
                      title={ttsEnabled ? 'Voice responses ON — click to turn off' : 'Turn on voice responses'}
                      aria-label={ttsEnabled ? 'Disable voice responses' : 'Enable voice responses'}
                      onClick={() => {
                        if (ttsEnabled) window.speechSynthesis.cancel();
                        setTtsEnabled(v => !v);
                        setSpeakingMessageId(null);
                      }}
                      style={{
                        position: 'absolute', top: 8, right: 8,
                        fontSize: '0.8rem', opacity: 0.7,
                      }}
                    >
                      {ttsEnabled ? '🔊' : '🔇'}
                    </Button>
                  )}
                  {/* A2A Collaboration panel toggle */}
                  {activeCollaboration && (
                    <Button
                      size="sm"
                      variant={showCollabPanel ? 'outline-info' : 'outline-secondary'}
                      onClick={() => setShowCollabPanel(v => !v)}
                      style={{
                        position: 'absolute', top: 8, right: window.speechSynthesis ? 50 : 8,
                        fontSize: '0.8rem', opacity: 0.8,
                      }}
                    >
                      {showCollabPanel ? 'Hide A2A' : 'View A2A'}
                    </Button>
                  )}
                </div>
                {/* ═══ Chat messages below ═══ */}
                <Card.Body style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column', position: 'relative' }}>
                  <div style={{ flex: 1, overflowY: 'auto' }}>
                    {loadingMessages ? (
                      <div className="d-flex align-items-center gap-2 text-muted">
                        <Spinner animation="border" size="sm" />
                        <span>{t('loadingConversation')}</span>
                      </div>
                    ) : (
                      <ListGroup variant="flush">
                        {messages.map((message) => renderMessage(message))}
                        {postingMessage && !streamingText && (
                          <ListGroup.Item className="border-0 py-2">
                            <div className="d-flex align-items-center gap-2 text-muted" style={{ fontSize: '0.9rem' }}>
                              <Spinner animation="grow" size="sm" style={{ width: '8px', height: '8px' }} />
                              <Spinner animation="grow" size="sm" style={{ width: '8px', height: '8px', animationDelay: '0.2s' }} />
                              <Spinner animation="grow" size="sm" style={{ width: '8px', height: '8px', animationDelay: '0.4s' }} />
                              <span className="ms-1">{t('thinking')}</span>
                            </div>
                          </ListGroup.Item>
                        )}
                        {streamingText && (
                          <ListGroup.Item style={{ background: 'var(--surface-contrast)' }}>
                            <div className="d-flex justify-content-between align-items-start">
                              <div>
                                <Badge bg="primary" className="me-2 text-uppercase">assistant</Badge>
                                <div className="chat-markdown" style={{ fontSize: '0.9rem', lineHeight: 1.6 }}>
                                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{streamingText}</ReactMarkdown>
                                </div>
                              </div>
                            </div>
                          </ListGroup.Item>
                        )}
                        <div ref={messagesEndRef} />
                        {messages.length === 0 && !postingMessage && (
                          <div className="py-4">
                            <div className="text-center text-muted mb-4">
                              <h5>{t('getStarted')}</h5>
                              <p>{t('getStartedDesc')}</p>
                            </div>
                            <div className="row g-2 px-3">
                              {[
                                { icon: '\uD83D\uDCCA', key: 'revenue' },
                                { icon: '\uD83D\uDCC8', key: 'trends' },
                                { icon: '\uD83C\uDFAF', key: 'insights' },
                                { icon: '\uD83D\uDCCB', key: 'report' },
                                { icon: '\uD83D\uDD2E', key: 'forecast' },
                                { icon: '\u26A1', key: 'anomalies' },
                              ].map((prompt, idx) => (
                                <div key={idx} className="col-md-6">
                                  <Button
                                    variant="outline-secondary"
                                    className="w-100 text-start py-2 px-3"
                                    style={{ borderRadius: '12px', fontSize: '0.9rem' }}
                                    onClick={() => {
                                      setMessageDraft(t(`prompts.${prompt.key}`));
                                      document.getElementById('chatMessage')?.focus();
                                    }}
                                  >
                                    <span className="me-2">{prompt.icon}</span>
                                    {t(`prompts.${prompt.key}`)}
                                  </Button>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}
                      </ListGroup>
                    )}
                  </div>
                  {/* Floating mini avatar — shows emotion reaction */}
                  <Form onSubmit={handleMessageSubmit} className="pt-3">
                    <input
                      type="file"
                      ref={fileInputRef}
                      accept="image/*,audio/*,.pdf"
                      style={{ display: 'none' }}
                      onChange={(e) => {
                        if (e.target.files && e.target.files[0]) {
                          setAttachedFile(e.target.files[0]);
                        }
                      }}
                    />
                    {attachedFile && (
                      <div className="mb-2 d-flex align-items-center" style={{ gap: '0.5rem' }}>
                        <Badge
                          bg="secondary"
                          className="d-inline-flex align-items-center py-2 px-3"
                          style={{ fontSize: '0.8rem', borderRadius: '16px', maxWidth: '100%' }}
                        >
                          <span className="me-1">
                            {attachedFile.type?.startsWith('image/') ? '\uD83D\uDDBC\uFE0F' : attachedFile.type?.startsWith('audio/') ? '\uD83C\uDFA4' : '\uD83D\uDCC4'}
                          </span>
                          <span className="text-truncate" style={{ maxWidth: '200px' }}>{attachedFile.name}</span>
                          <span
                            role="button"
                            className="ms-2"
                            style={{ cursor: 'pointer', opacity: 0.8 }}
                            onClick={() => {
                              setAttachedFile(null);
                              if (fileInputRef.current) {
                                fileInputRef.current.value = '';
                              }
                            }}
                          >
                            &times;
                          </span>
                        </Badge>
                      </div>
                    )}
                    <Row className="g-2 align-items-end">
                      <Col xs={12} md={9}>
                        <Form.Group controlId="chatMessage">
                          <Form.Label className="visually-hidden">Message</Form.Label>
                          <Form.Control
                            as="textarea"
                            rows={2}
                            placeholder={t('messagePlaceholder')}
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
                      <Col xs={12} md={3} className="d-grid" style={{ gap: '0.5rem' }}>
                        <div className="d-flex gap-2">
                          <Button
                            type="button"
                            variant="outline-secondary"
                            title={t('attachFile')}
                            onClick={() => fileInputRef.current?.click()}
                            style={{ flex: '0 0 auto' }}
                            disabled={isRecording}
                          >
                            {'\uD83D\uDCCE'}
                          </Button>
                          {navigator.mediaDevices && (
                            <Button
                              type="button"
                              variant={isRecording ? 'danger' : 'outline-secondary'}
                              title={isRecording ? 'Stop recording' : 'Record voice message'}
                              onClick={isRecording ? stopRecording : startRecording}
                              style={{ flex: '0 0 auto', position: 'relative' }}
                            >
                              {isRecording ? (
                                <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
                                  <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#fff', display: 'inline-block', animation: 'pulse 1s infinite' }} />
                                  Stop
                                </span>
                              ) : '🎙️'}
                            </Button>
                          )}
                          <Button
                            type="submit"
                            variant="primary"
                            disabled={postingMessage || (!messageDraft.trim() && !attachedFile) || isRecording}
                            style={{ flex: 1 }}
                          >
                            {postingMessage ? t('sending') : t('send')}
                          </Button>
                        </div>
                      </Col>
                    </Row>
                  </Form>
                </Card.Body>
              </Card>
            ) : (
              <Card className="shadow-sm">
                <Card.Body className="text-center text-muted">
                  <p className="mb-0">{t('selectSession')}</p>
                </Card.Body>
              </Card>
            )}
            </div>
            {showCollabPanel && activeCollaboration && (
              <div style={{ width: '420px', flexShrink: 0 }}>
                <CollaborationPanel
                  collaborationId={activeCollaboration.id}
                  phases={activeCollaboration.phases}
                  apiBaseUrl={`${API_BASE}/api/v1`}
                  token={auth.user?.access_token || ''}
                  isCompleted={activeCollaboration.isCompleted}
                />
              </div>
            )}
            </div>
          </Col>
        </Row>
      </Container>

      <Modal show={showCreateModal} onHide={() => setShowCreateModal(false)} centered>
        <Form onSubmit={handleCreateSession}>
          <Modal.Header closeButton>
            <Modal.Title>{t('createModal.title')}</Modal.Title>
          </Modal.Header>
          <Modal.Body>
            {formErrors && <Alert variant="danger">{formErrors}</Alert>}
            <Form.Group className="mb-3">
              <Form.Label>{t('createModal.titleLabel')}</Form.Label>
              <Form.Control
                type="text"
                name="title"
                placeholder={t('createModal.titlePlaceholder')}
                value={sessionForm.title}
                onChange={handleCreateSessionChange}
              />
            </Form.Group>

            <Form.Group className="mb-3">
              <Form.Label>{t('createModal.agentKit')}</Form.Label>
              <Form.Select name="agentKitId" value={sessionForm.agentKitId} onChange={handleCreateSessionChange}>
                <option value="">{t('createModal.agentKitPlaceholder')}</option>
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
              {t('createModal.cancel')}
            </Button>
            <Button type="submit" variant="primary">
              {t('createModal.create')}
            </Button>
          </Modal.Footer>
        </Form>
      </Modal>
    </Layout>
  );
};

export default ChatPage;
