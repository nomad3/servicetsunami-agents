import React, { useState, useEffect, useRef, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import LunaAvatar from './luna/LunaAvatar';
import MemoryPanel from './MemoryPanel';
import { useLunaStream } from '../hooks/useLunaStream';
import { apiJson } from '../api';

export default function ChatInterface({ handoff }) {
  const [sessions, setSessions] = useState([]);
  const [activeSession, setActiveSession] = useState(null);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [memoryOpen, setMemoryOpen] = useState(false);
  const [emotion, setEmotion] = useState(null);
  const emotionTimer = useRef(null);
  const messagesEnd = useRef(null);
  const activeSessionRef = useRef(null); // guards async callbacks
  const { send, streaming, chunks } = useLunaStream();

  // Load sessions on mount
  useEffect(() => {
    apiJson('/api/v1/chat/sessions').then(data => {
      setSessions(data);
      if (data.length > 0) selectSession(data[0].id);
    }).catch(() => {});
  }, []);

  const selectSession = useCallback(async (id) => {
    setActiveSession(id);
    activeSessionRef.current = id;
    const msgs = await apiJson(`/api/v1/chat/sessions/${id}/messages`);
    // Only apply if still the active session
    if (activeSessionRef.current === id) {
      setMessages(msgs);
    }
  }, []);

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
      const API_BASE = import.meta.env.VITE_API_BASE_URL || '';
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

  const handleSend = async () => {
    if (!input.trim() || !activeSession || streaming) return;
    const text = input;
    const targetSession = activeSession;
    setInput('');

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
          <LunaAvatar state={effectiveState} mood="calm" size="lg" animated />
          <span className="luna-status">{effectiveState === 'thinking' ? 'Thinking...' : 'Luna'}</span>
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
          <button type="submit" className="luna-btn send-btn" disabled={streaming || !input.trim()}>
            {streaming ? '...' : 'Send'}
          </button>
        </form>
      </main>
    </div>
  );
}
