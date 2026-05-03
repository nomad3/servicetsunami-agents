// Default gesture bindings — Apple-trackpad-mirrored + Luna extensions.
// User can override any of these in /settings/gestures.

export const DEFAULT_BINDINGS = [
  {
    id: 'd-3up',
    gesture: { pose: 'three', motion: { kind: 'swipe', direction: 'up' } },
    action: { kind: 'nav_hud' },
    scope: 'global',
    enabled: true,
    user_recorded: false,
  },
  {
    id: 'd-3dn',
    gesture: { pose: 'three', motion: { kind: 'swipe', direction: 'down' } },
    action: { kind: 'nav_chat' },
    scope: 'global',
    enabled: true,
    user_recorded: false,
  },
  {
    id: 'd-3lt',
    gesture: { pose: 'three', motion: { kind: 'swipe', direction: 'left' } },
    action: { kind: 'agent_prev' },
    scope: 'global',
    enabled: true,
    user_recorded: false,
  },
  {
    id: 'd-3rt',
    gesture: { pose: 'three', motion: { kind: 'swipe', direction: 'right' } },
    action: { kind: 'agent_next' },
    scope: 'global',
    enabled: true,
    user_recorded: false,
  },
  {
    id: 'd-4in',
    gesture: { pose: 'four', motion: { kind: 'pinch', direction: 'in' } },
    action: { kind: 'nav_command_palette' },
    scope: 'global',
    enabled: true,
    user_recorded: false,
  },
  {
    id: 'd-fist',
    gesture: { pose: 'fist' },
    action: { kind: 'dismiss' },
    scope: 'global',
    enabled: true,
    user_recorded: false,
  },
  {
    // Note: classify() collapses "5 fingers extended" geometry to OpenPalm,
    // so the default memory_record gesture is open_palm + tap (a quick
    // pinch-and-release while the palm is open).
    id: 'd-palm-tap',
    gesture: { pose: 'open_palm', motion: { kind: 'tap' } },
    action: { kind: 'memory_record' },
    scope: 'global',
    enabled: true,
    user_recorded: false,
  },
  {
    id: 'd-2up',
    gesture: { pose: 'peace', motion: { kind: 'swipe', direction: 'up' } },
    action: { kind: 'scroll_up' },
    scope: 'chat_only',
    enabled: true,
    user_recorded: false,
  },
  {
    id: 'd-2dn',
    gesture: { pose: 'peace', motion: { kind: 'swipe', direction: 'down' } },
    action: { kind: 'scroll_down' },
    scope: 'chat_only',
    enabled: true,
    user_recorded: false,
  },
];
