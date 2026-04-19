import React from 'react';
import { useVoiceContext } from '../context/VoiceContext';

export default function VoiceInput({ onTranscript, disabled }) {
  const { isRecording, transcribing, startRecording, stopRecording } = useVoiceContext();

  const handlePointerDown = () => {
    if (disabled || transcribing) return;
    startRecording();
  };

  const handlePointerUp = async () => {
    if (!isRecording) return;
    const transcript = await stopRecording();
    if (transcript) {
      onTranscript(transcript);
    }
  };

  // pointercancel fires when the OS takes over the pointer (drag off, scroll,
  // context menu) — without this, holding + dragging away leaves the mic stuck on.
  const handlePointerCancel = async () => {
    if (isRecording) {
      await stopRecording();
    }
  };

  return (
    <div className="voice-input-container">
      <button
        type="button"
        className={`luna-btn mic-btn ${isRecording ? 'recording' : ''} ${transcribing ? 'transcribing' : ''}`}
        onPointerDown={handlePointerDown}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerCancel}
        onPointerLeave={handlePointerCancel}
        disabled={disabled || transcribing}
        title="Hold to speak"
      >
        {transcribing ? '...' : isRecording ? 'Listening' : '\uD83C\uDFA4'}
      </button>
      {isRecording && <div className="recording-wave" aria-hidden="true" />}
    </div>
  );
}
