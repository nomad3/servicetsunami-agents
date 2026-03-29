import React, { useState, useEffect, useRef, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import LunaAvatar from './luna/LunaAvatar';
import { useLunaStream } from '../hooks/useLunaStream';
import { apiJson } from '../api';

const PRESENCE_POLL_INTERVAL = 15000; // 15s

export default function ChatInterface() {
  const [sessions, setSessions] = useState([]);
  const [activeSession, setActiveSession] = useState(null);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [emotion, setEmotion] = useState(null);
  const [presence, setPresence] = useState(null);
  const emotionTimer = useRef(null);
  const messagesEnd = useRef(null);
  const { send, streaming, chunks } = useLunaStream();

  // Poll presence for handoff detection
  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const snap = await apiJson('/api/v1/presence/');
        if (!cancelled) setPresence(snap);
      } catch {}
    };
    poll();
    const id = setInterval(poll, PRESENCE_POLL_INTERVAL);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  // Load sessions on mount
  useEffect(() => {
    apiJson('/api/v1/chat/sessions').then(data => {
      setSessions(data);
      if (data.length > 0) selectSession(data[0].id);
    }).catch(() => {});
  }, []);

  const selectSession = useCallback(async (id) => {
    setActiveSession(id);
    const msgs = await apiJson(`/api/v1/chat/sessions/${id}/messages`);
    setMessages(msgs);
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
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      const base64 = await invoke('capture_screenshot');
      setInput('[Screenshot captured]');
    } catch (err) {
      console.error('Screenshot failed:', err);
    }
  };

  const handleSend = async () => {
    if (!input.trim() || !activeSession || streaming) return;
    const text = input;
    setInput('');

    // Show user message immediately (optimistic)
    const tempId = `temp-${Date.now()}`;
    setMessages(prev => [...prev, { id: tempId, role: 'user', content: text }]);

    await send(activeSession, text, {
      onUserSaved: (msg) => {
        // Replace optimistic message with server-persisted one
        setMessages(prev => prev.map(m => m.id === tempId ? msg : m));
      },
      onDone: (msg) => {
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
        </div>

        {/* Handoff banner */}
        {presence?.state === 'handoff' && (
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
