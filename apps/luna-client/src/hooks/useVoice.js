import { useState, useRef, useCallback, useEffect } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';
import { API_BASE } from '../api';

// Build a WAV (PCM 16-bit) container from Float32 mono samples.
// Whisper/transcription APIs reject raw PCM — they need a proper RIFF header.
function encodeWav(samples, sampleRate, channels) {
  const numSamples = samples.length;
  const bytesPerSample = 2; // PCM 16-bit
  const blockAlign = channels * bytesPerSample;
  const byteRate = sampleRate * blockAlign;
  const dataSize = numSamples * bytesPerSample;

  const buffer = new ArrayBuffer(44 + dataSize);
  const view = new DataView(buffer);

  const writeStr = (off, s) => {
    for (let i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i));
  };

  writeStr(0, 'RIFF');
  view.setUint32(4, 36 + dataSize, true);
  writeStr(8, 'WAVE');
  writeStr(12, 'fmt ');
  view.setUint32(16, 16, true); // fmt chunk size
  view.setUint16(20, 1, true);  // PCM
  view.setUint16(22, channels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, byteRate, true);
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, 16, true); // bits per sample
  writeStr(36, 'data');
  view.setUint32(40, dataSize, true);

  // Convert Float32 [-1,1] to Int16 PCM
  let offset = 44;
  for (let i = 0; i < numSamples; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    offset += 2;
  }

  return new Blob([view], { type: 'audio/wav' });
}

export function useVoice() {
  const [isRecording, setIsRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [error, setError] = useState(null);
  const chunksRef = useRef([]);
  const unlistenRef = useRef(null);
  const configRef = useRef({ sampleRate: 48000, channels: 1 });
  const recordingRef = useRef(false);

  // Keep ref in sync so external handlers (global shortcut) see current state
  useEffect(() => {
    recordingRef.current = isRecording;
  }, [isRecording]);

  const startRecording = useCallback(async () => {
    if (recordingRef.current) return; // already recording
    try {
      setError(null);
      chunksRef.current = [];

      // Subscribe BEFORE starting capture to avoid dropping initial chunks
      unlistenRef.current = await listen('audio-chunk', (event) => {
        chunksRef.current.push(event.payload);
      });

      // start_audio_capture returns { sample_rate, channels }
      const cfg = await invoke('start_audio_capture');
      if (cfg && typeof cfg.sample_rate === 'number') {
        configRef.current = {
          sampleRate: cfg.sample_rate,
          channels: cfg.channels || 1,
        };
      }
      recordingRef.current = true;
      setIsRecording(true);
    } catch (err) {
      console.error('[Luna Voice] Start failed:', err);
      if (unlistenRef.current) {
        unlistenRef.current();
        unlistenRef.current = null;
      }
      setError('Failed to access microphone');
    }
  }, []);

  const stopRecording = useCallback(async () => {
    if (!recordingRef.current) return null;
    try {
      recordingRef.current = false;
      setIsRecording(false);
      await invoke('stop_audio_capture');

      if (unlistenRef.current) {
        unlistenRef.current();
        unlistenRef.current = null;
      }

      if (chunksRef.current.length === 0) return null;

      setTranscribing(true);

      // Decode base64 chunks back to Float32 samples
      const parts = [];
      let totalLen = 0;
      for (const b64 of chunksRef.current) {
        const bin = atob(b64);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        const floatData = new Float32Array(bytes.buffer, bytes.byteOffset, bytes.byteLength / 4);
        parts.push(floatData);
        totalLen += floatData.length;
      }

      const combined = new Float32Array(totalLen);
      let offset = 0;
      for (const p of parts) {
        combined.set(p, offset);
        offset += p.length;
      }

      const { sampleRate, channels } = configRef.current;
      const wavBlob = encodeWav(combined, sampleRate, channels);

      const formData = new FormData();
      formData.append('file', wavBlob, 'voice-input.wav');

      const token = localStorage.getItem('luna_token');
      const res = await fetch(`${API_BASE}/api/v1/media/transcribe`, {
        method: 'POST',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: formData,
      });

      if (!res.ok) throw new Error(`Transcription failed (${res.status})`);
      const data = await res.json();
      return data.transcript;
    } catch (err) {
      console.error('[Luna Voice] Stop/Transcribe failed:', err);
      setError('Transcription failed');
      return null;
    } finally {
      setTranscribing(false);
      chunksRef.current = [];
    }
  }, []);

  // Cleanup on unmount: stop capture and remove listener
  useEffect(() => {
    return () => {
      if (unlistenRef.current) {
        unlistenRef.current();
        unlistenRef.current = null;
      }
      if (recordingRef.current) {
        invoke('stop_audio_capture').catch(() => {});
        recordingRef.current = false;
      }
    };
  }, []);

  return {
    isRecording,
    transcribing,
    error,
    startRecording,
    stopRecording,
  };
}
