import { useState, useEffect, useCallback } from 'react';
import { apiJson } from '../api';

export function useTrustProfile() {
  const [trust, setTrust] = useState(null);

  const fetchTrust = useCallback(async () => {
    try {
      const data = await apiJson('/api/v1/safety/trust/agents/luna');
      setTrust(data);
    } catch {
      // Default to restrictive if can't fetch
      setTrust({ trust_score: 0.5, autonomy_tier: 'recommend_only', confidence: 0 });
    }
  }, []);

  useEffect(() => { fetchTrust(); }, [fetchTrust]);

  const canActAutonomously = trust?.autonomy_tier === 'autonomous';
  const needsConfirmation = trust?.autonomy_tier === 'supervised';
  const recommendOnly = trust?.autonomy_tier === 'recommend_only';

  return { trust, canActAutonomously, needsConfirmation, recommendOnly, refreshTrust: fetchTrust };
}
