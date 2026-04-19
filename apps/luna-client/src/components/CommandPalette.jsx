import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useVoiceContext } from '../context/VoiceContext';

export default function CommandPalette({ visible, onClose, onSend }) {
  const [query, setQuery] = useState('');
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const { isRecording, transcribing, startRecording, stopRecording } = useVoiceContext();
  const inputRef = useRef(null);

  // Refs keep event handlers seeing current state without re-registering the
  // window listener on every render (avoids stale-closure bugs on PTT toggle).
  const visibleRef = useRef(visible);
  const recordingRef = useRef(isRecording);
  useEffect(() => { visibleRef.current = visible; }, [visible]);
  useEffect(() => { recordingRef.current = isRecording; }, [isRecording]);

  useEffect(() => {
    if (visible) {
      setQuery('');
      setResult(null);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [visible]);

  // Voice event bridge — registered once, reads live state via refs
  useEffect(() => {
    const handleStart = () => {
      if (visibleRef.current) startRecording();
    };
    const handleStop = async () => {
      if (visibleRef.current && recordingRef.current) {
        const transcript = await stopRecording();
        if (transcript) {
          setQuery(transcript);
          submitText(transcript);
        }
      }
    };

    window.addEventListener('luna-voice-start', handleStart);
    window.addEventListener('luna-voice-stop', handleStop);
    return () => {
      window.removeEventListener('luna-voice-start', handleStart);
      window.removeEventListener('luna-voice-stop', handleStop);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [startRecording, stopRecording]);

  // Close on Escape
  useEffect(() => {
    const handleKey = (e) => {
      if (e.key === 'Escape') onClose();
    };
    if (visible) {
      window.addEventListener('keydown', handleKey);
      return () => window.removeEventListener('keydown', handleKey);
    }
  }, [visible, onClose]);

  const submitText = useCallback(async (rawText) => {
    const text = (rawText || '').trim();
    if (!text || transcribing) return;
    setLoading(true);
    setResult(null);

    // Get active app context
    let appContext = '';
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      const app = await invoke('get_active_app');
      if (app.app) appContext = `[User is in ${app.app}${app.title ? ': ' + app.title : ''}] `;
    } catch {}

    if (onSend) {
      onSend(appContext + text);
      setQuery('');
      setLoading(false);
      onClose();
    } else {
      setLoading(false);
    }
  }, [onSend, onClose, transcribing]);

  const handleSubmit = (e) => {
    if (e) e.preventDefault();
    if (loading || transcribing) return;
    submitText(query);
  };

  if (!visible) return null;

  return (
    <div className={`palette-overlay ${isRecording ? 'recording' : ''}`} onClick={onClose}>
      <div className="palette-container" onClick={e => e.stopPropagation()}>
        <form onSubmit={handleSubmit}>
          <input
            ref={inputRef}
            type="text"
            className="palette-input"
            placeholder={isRecording ? 'Listening...' : 'Ask Luna anything...'}
            value={query}
            onChange={e => setQuery(e.target.value)}
            disabled={transcribing}
            autoFocus
          />
        </form>
        {(loading || transcribing) && (
          <div className="palette-status">
            {transcribing ? 'Transcribing audio...' : 'Thinking...'}
          </div>
        )}
        {result && <div className="palette-result">{result}</div>}
        <div className="palette-hint">
          {isRecording ? 'Release keys to send' : 'Enter to send \u00B7 Esc to close'}
        </div>
      </div>
    </div>
  );
}
