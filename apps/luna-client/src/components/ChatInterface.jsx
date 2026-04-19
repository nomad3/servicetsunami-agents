import React, { useState, useEffect, useRef, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import MemoryPanel from './MemoryPanel';
import LunaAvatar from './luna/LunaAvatar';
import VoiceInput from './VoiceInput';
import { useLunaStream } from '../hooks/useLunaStream';
import { apiJson, API_BASE } from '../api';

export default function ChatInterface({ handoff, requestAction }) {
  const [sessions, setSessions] = useState([]);
  const [activeSession, setActiveSession] = useState(null);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [memoryOpen, setMemoryOpen] = useState(false);
  const [emotion, setEmotion] = useState(null);
  const [copiedId, setCopiedId] = useState(null);
  const emotionTimer = useRef(null);
  const messagesEnd = useRef(null);
  const activeSessionRef = useRef(null); // guards async callbacks
  const { send, streaming, chunks } = useLunaStream();

  const handleCopy = async (text, msgId) => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
    }
    setCopiedId(msgId);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const selectSession = useCallback(async (id) => {
    setActiveSession(id);
    activeSessionRef.current = id;
    
    // Notify App of session change to drive event bridge
    window.dispatchEvent(new CustomEvent('luna-session-change', { detail: id }));

    const msgs = await apiJson(`/api/v1/chat/sessions/${id}/messages`);
    // Only apply if still the active session
    if (activeSessionRef.current === id) {
      setMessages(msgs);
    }
  }, []);

  // Load sessions on mount
  useEffect(() => {
    apiJson('/api/v1/chat/sessions')
      .then(data => {
        setSessions(data);
        if (data.length > 0) selectSession(data[0].id);
      })
      .catch(err => {
        console.error('[Luna] Failed to load sessions:', err);
      });
  }, [selectSession]);

  useEffect(() => {
    messagesEnd.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, chunks]);

  const applyEmotion = (em) => {
    if (!em) return;
    setEmotion(em);
    clearTimeout(emotionTimer.current);
    emotionTimer.current = setTimeout(() => setEmotion(null), 10000);
  };

  const handleScreenshot = async () => {
    if (!activeSession) return;
    // Gate through trust approval if supervised
    if (requestAction) {
      const approved = await requestAction({
        type: 'screenshot',
        description: 'Capture a screenshot of your screen and send it to Luna for analysis.',
      });
      if (!approved) return;
    }
    const targetSession = activeSession;
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      const base64 = await invoke('capture_screenshot');

      const byteChars = atob(base64);
      const byteArray = new Uint8Array(byteChars.length);
      for (let i = 0; i < byteChars.length; i++) byteArray[i] = byteChars.charCodeAt(i);
      const blob = new Blob([byteArray], { type: 'image/png' });

      const formData = new FormData();
      formData.append('file', blob, `screenshot-${Date.now()}.png`);
      formData.append('content', input.trim() || 'What do you see in this screenshot?');

      const token = localStorage.getItem('luna_token');
      const tempId = `temp-${Date.now()}`;
      setMessages(prev => [...prev, { id: tempId, role: 'user', content: '[Screenshot sent]' }]);
      setInput('');

      const res = await fetch(`${API_BASE}/api/v1/chat/sessions/${targetSession}/messages/upload`, {
        method: 'POST',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: formData,
      });
      if (!res.ok) throw new Error(`Upload failed: ${res.status}`);
      const data = await res.json();
      // Only update if still on the same session
      if (activeSessionRef.current === targetSession) {
        setMessages(prev => [
          ...prev.map(m => m.id === tempId ? data.user_message : m),
          data.assistant_message,
        ]);
        applyEmotion(data.assistant_message?.emotion || data.assistant_message?.context?.emotion);
      }
    } catch (err) {
      console.error('Screenshot failed:', err);
    }
  };

  const handleSend = async (overrideText) => {
    const text = typeof overrideText === 'string' ? overrideText : input;
    if (!text.trim() || streaming) return;
    
    let targetSession = activeSession;
    setInput('');

    // Auto-create session if none active
    if (!targetSession) {
      try {
        const session = await apiJson('/api/v1/chat/sessions', {
          method: 'POST',
          body: JSON.stringify({ title: 'Luna Chat' }),
        });
        setSessions(prev => [session, ...prev]);
        targetSession = session.id;
        setActiveSession(session.id);
        activeSessionRef.current = session.id;
        window.dispatchEvent(new CustomEvent('luna-session-change', { detail: session.id }));
      } catch (err) {
        console.error('[Luna] Failed to auto-create session:', err);
        return;
      }
    }

    const tempId = `temp-${Date.now()}`;
    setMessages(prev => [...prev, { id: tempId, role: 'user', content: text }]);

    await send(targetSession, text, {
      onUserSaved: (msg) => {
        if (activeSessionRef.current !== targetSession) return;
        setMessages(prev => prev.map(m => m.id === tempId ? msg : m));
      },
      onDone: (msg) => {
        if (activeSessionRef.current !== targetSession) return;
        setMessages(prev => [...prev, msg]);
        applyEmotion(msg.emotion || msg.context?.emotion);
      },
      onError: (err) => console.error('Stream error:', err),
    });
  };

  const createSession = async () => {
    try {
      const session = await apiJson('/api/v1/chat/sessions', {
        method: 'POST',
        body: JSON.stringify({ title: 'Luna Chat' }),
      });
      setSessions(prev => [session, ...prev]);
      selectSession(session.id);
    } catch {}
  };

  const handleVoiceTranscript = (text) => {
    handleSend(text);
  };

  const effectiveState = emotion || (streaming ? 'thinking' : 'idle');

  return (
    <div className="chat-layout">
      {/* Sidebar */}
      <aside className="chat-sidebar">
        <button className="luna-btn sidebar-new" onClick={createSession}>+ New Chat</button>
        <div className="session-list">
          {sessions.map(s => (
            <div
              key={s.id}
              className={`session-item ${s.id === activeSession ? 'active' : ''}`}
              onClick={() => selectSession(s.id)}
            >
              {s.title || 'Untitled'}
            </div>
          ))}
        </div>
      </aside>

      {/* Main chat */}
      <main className="chat-main">
        {/* Luna header */}
        <div className="luna-header">
          <div className="luna-identity">
            <LunaAvatar state={effectiveState} size="sm" />
            <span className="luna-status">{effectiveState === 'thinking' ? 'Thinking...' : 'Luna'}</span>
          </div>
          <button
            className="luna-btn luna-btn-sm memory-toggle"
            onClick={() => setMemoryOpen(!memoryOpen)}
            title="Memory"
          >
            {'\uD83E\uDDE0'}
          </button>
        </div>

        {/* Handoff banner */}
        {handoff && (
          <div className="handoff-banner">Continuing from another device...</div>
        )}

        {/* Messages */}
        <div className="messages-area">
          {messages.length === 0 && !streaming && (
            <div className="chat-welcome">
              <h2>Luna OS Spatial Workstation</h2>
              <p>Type a message below to start your first raid or explore the Knowledge Nebula.</p>
              <div className="welcome-tips">
                <div className="tip"><code>Cmd+Shift+L</code> to toggle Spatial HUD</div>
                <div className="tip"><code>WASD</code> to fly through memory stars</div>
                <div className="tip"><code>Tab</code> to view Shared Blackboard</div>
              </div>
            </div>
          )}
          {messages.map(msg => (
            <div key={msg.id} className={`message message-${msg.role}`}>
              {msg.role === 'assistant' && msg.context?.recalled_entity_names?.length > 0 && (
                <div className="memory-context">
                  {msg.context.recalled_entity_names.map((name, i) => (
                    <span key={i} className="memory-tag">{name}</span>
                  ))}
                </div>
              )}
              {msg.role === 'assistant' ? (
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
              ) : (
                <p>{msg.content}</p>
              )}
              {msg.role === 'assistant' && (
                <button
                  className="luna-btn luna-btn-sm copy-btn"
                  onClick={() => handleCopy(msg.content, msg.id)}
                  title="Copy response"
                  style={{ marginTop: 4, fontSize: '0.75rem', opacity: 0.6 }}
                >
                  {copiedId === msg.id ? 'Copied!' : 'Copy'}
                </button>
              )}
            </div>
          ))}
          {streaming && chunks && (
            <div className="message message-assistant streaming">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{chunks}</ReactMarkdown>
            </div>
          )}
          <div ref={messagesEnd} />
        </div>

        {/* Memory panel */}
        <MemoryPanel visible={memoryOpen} onClose={() => setMemoryOpen(false)} />

        {/* Input */}
        <form className="chat-input-form" onSubmit={e => { e.preventDefault(); handleSend(); }}>
          <input
            type="text"
            className="luna-input chat-input"
            placeholder="Message Luna..."
            value={input}
            onChange={e => setInput(e.target.value)}
            disabled={streaming}
          />
          <button type="button" className="luna-btn screenshot-btn" onClick={handleScreenshot} title="Capture screenshot">
            {'\uD83D\uDCF7'}
          </button>
          {window.__TAURI_INTERNALS__ && (
            <VoiceInput onTranscript={handleVoiceTranscript} disabled={streaming} />
          )}
          <button type="submit" className="luna-btn send-btn" disabled={streaming || !input.trim()}>
            {streaming ? '...' : 'Send'}
          </button>
        </form>
      </main>
    </div>
  );
}
