/**
 * Bridges "point at an agent + voice" → spawn an agent_task on that agent.
 * Listens for two custom window events:
 *   - 'luna-podium-target-agent' { detail: { agentId } } — fired by the
 *     scene when the gesture cursor lands on an avatar.
 *   - 'luna-podium-voice-text' { detail: { text } } — fired by useVoice
 *     when the user has finished speaking.
 *
 * When both are fresh (<5s apart), POST /agent_tasks with the spoken text
 * as the description. Emits 'luna-podium-task-spawned' for visual feedback.
 */
import { useEffect, useRef } from 'react';
import { apiJson } from '../api';

const PAIRING_WINDOW_MS = 5000;

export function useDispatchOnPoint() {
  const lastTargetRef = useRef({ agentId: null, ts: 0 });
  const lastVoiceRef = useRef({ text: null, ts: 0 });

  useEffect(() => {
    const dispatchIfReady = async () => {
      const now = Date.now();
      const target = lastTargetRef.current;
      const voice = lastVoiceRef.current;
      if (!target.agentId || !voice.text) return;
      if (Math.abs(target.ts - voice.ts) > PAIRING_WINDOW_MS) return;
      // Consume both
      lastTargetRef.current = { agentId: null, ts: 0 };
      lastVoiceRef.current = { text: null, ts: 0 };

      try {
        const task = await apiJson('/api/v1/tasks', {
          method: 'POST',
          body: JSON.stringify({
            agent_id: target.agentId,
            description: voice.text,
            priority: 'normal',
            source: 'podium_dispatch',
          }),
        });
        window.dispatchEvent(
          new CustomEvent('luna-podium-task-spawned', {
            detail: { agentId: target.agentId, task },
          }),
        );
      } catch (e) {
        window.dispatchEvent(
          new CustomEvent('luna-podium-dispatch-failed', {
            detail: { agentId: target.agentId, error: String(e) },
          }),
        );
      }
    };

    const handleTarget = (e) => {
      lastTargetRef.current = { agentId: e.detail?.agentId, ts: Date.now() };
      dispatchIfReady();
    };
    const handleVoice = (e) => {
      lastVoiceRef.current = { text: e.detail?.text, ts: Date.now() };
      dispatchIfReady();
    };
    window.addEventListener('luna-podium-target-agent', handleTarget);
    window.addEventListener('luna-podium-voice-text', handleVoice);
    return () => {
      window.removeEventListener('luna-podium-target-agent', handleTarget);
      window.removeEventListener('luna-podium-voice-text', handleVoice);
    };
  }, []);
}
