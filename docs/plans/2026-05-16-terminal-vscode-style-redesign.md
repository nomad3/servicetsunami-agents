# Plan: VSCode-style Terminal Panel Redesign

**Date:** 2026-05-16
**Goal:** vertical resize between chat row and terminal, plus side-by-side CLI splits inside the terminal panel — VSCode's terminal-panel model.

---

## 1. ResizableSplit: extend with `direction` prop (recommended)

**Decision: extend `ResizableSplit.js` with `direction="row" | "column"` (default `"row"`) rather than ship a sibling.**

### Why extend, not duplicate

The component is ~390 lines but the *axis-specific* code is small and well-isolated:

- `gridTemplateColumns` → `gridTemplateRows` (one memoised string)
- `clientX` → `clientY` (mousemove + onHandleMouseDown drag deltas)
- `containerWidth` → `containerHeight` (the `getBoundingClientRect()` axis)
- `col-resize` cursor → `row-resize`
- `ArrowLeft/ArrowRight` keyboard → `ArrowUp/ArrowDown`
- `aria-orientation="vertical"` (the *handle* is vertical when splitting rows) → `aria-orientation="horizontal"` (handle bar is horizontal when splitting columns).
- `clampPercentagesToMinPixels(sizes, mins, containerWidth)` → rename second-axis parameter to `containerExtent`.

CSS additions: `.rs-root[data-direction="column"]` toggles grid axis; `.rs-handle[data-direction="column"]` gets `height: 6px; width: auto; cursor: row-resize;` and rotates the `::after` pill.

Mobile fallback already collapses to flex-column — the `column` direction is *already* the stacked-fallback shape.

### Tradeoff vs. sibling `ResizableStack`

- **Extend (chosen):** one component, one set of bugs, one set of tests, axis switched by prop.
- **Sibling:** clean per-component reading, but ~250 lines of duplication; bugs already fixed in `ResizableSplit` (wide-monitor-to-laptop reload clamp, localStorage GC) would silently regress.

Centralise the axis as `const axis = direction === 'column' ? { client: 'clientY', extent: 'height', cursor: 'row-resize', template: 'gridTemplateRows', ariaOrientation: 'horizontal', incKey: 'ArrowDown', decKey: 'ArrowUp' } : { ... }`.

---

## 2. Outer vertical split — chat row over terminal

Wrap chat-row + terminal-panel inside `DashboardControlCenter.js` (lines 562–722) in:

```jsx
<ResizableSplit
  direction="column"
  storageKey="dcc.outerCol.sizes"
  defaultSizes={[60, 40]}
  minSizes={[260, 140]}
>
  <div className="dcc-chat-row"> ... </div>
  <TerminalPanel sessionId={activeSession?.id || null} />
</ResizableSplit>
```

Mounted only when `mode === 'pro'`.

### CSS consequences

- `.dcc-chat-row { height: clamp(...) }` (line 116) — **delete** when wrapped; replace with `height: 100%; min-height: 0`.
- Add `.dcc-outer-col { height: clamp(600px, calc(100vh - 220px), calc(100vh - 200px)); min-height: 0; }` wrapper around the outer ResizableSplit.
- Mobile `.dcc-chat-row { height: auto }` override at line 272 — keep, plus force outer wrapper to `height: auto` under 768 px.
- Line 128 `.dcc-chat-row .ap-card { height: 100% }` stays — chain still intact.

### Why not CSS `resize: vertical`?

No persistence, no min floor, no keyboard a11y, no double-click reset. ResizableSplit already provides all of these.

---

## 3. Multi-pane terminal — design

### Data model

```
terminalGroups: [
  { id: 'tg-1', activeTabKey: 'claude_code' },
  { id: 'tg-2', activeTabKey: 'codex' },
  ...
]
terminalFocusedGroupId: 'tg-1'
```

Persisted via `apControl.terminalGroups` + `apControl.terminalFocusedGroupId`.

### Component tree

```
<TerminalPanel sessionId>
  ├── <header class="tp-header">
  │     [collapse ▾] Terminal [groups: 2]   [split ⊟] [close × — only if >1 group]
  └── <ResizableSplit direction="row" storageKey="dcc.terminalGroups.sizes.N">
        ├── <TerminalGroup id=tg-1 sessionId activeTabKey onActiveChange onFocus />
        ├── <TerminalGroup id=tg-2 ... />
        └── <TerminalGroup id=tg-3 ... />
```

- **N capped at 4** (one slack beyond the 3 known CLI platforms).
- Inner storageKey suffix `.${N}` per pane count — GC sweep at `ResizableSplit.js:80-96` scrubs stale `.N` entries.

### TerminalGroup component (extracted from TerminalCard)

```
<TerminalGroup>
  ├── <div class="tg-tabs">    ← strip from TerminalCard:143-157
  ├── <div class="tg-stream">  ← <pre> from TerminalCard:158-173
  └── auto-scroll + activeStream useEffects from TerminalCard:99-103
```

`SessionEventsContext` is shared — each group runs its own `useMemo` filter on the same `events` array. Tab strip auto-discovers via `streams.map` (same as today). The auto-open-on-first-chunk logic moves up to `TerminalPanel`.

### Header controls

Match `ChatGroupsPane` pattern (DashboardControlCenter.js:96-131):

- **Collapse ▾/▸** — existing `tc-header` behaviour; persist `apControl.terminalOpen`.
- **Split ⊟ (`FaColumns`)** — disabled when N ≥ 4. New group has `activeTabKey: null`.
- **Close × (`FaTimes`)** — visible when N > 1. Closes focused group; focus shifts to left neighbour.

### Focus indicator

`.tg-card-focused` gets `box-shadow: inset 0 0 0 2px var(--ap-primary)` (matches `.dcc-thread-card-focused`). PointerDown-capture shifts focus.

---

## 4. Event routing per group

Today: TerminalCard:42-80 builds `streams` per-platform from `events`. Each `<TerminalGroup>` runs the same memo, shares `events` via `useSessionEvents()`. No event duplication — context delivers once, two memos run independently. If profiling shows the duplicate memo is hot, lift `streamsByPlatform` into `TerminalPanel` and pass to each group.

---

## 5. Composition with existing center-pane editor groups

- Chat row's nested ResizableSplit: `dcc.editorGroups.sizes.${n}`
- Outer column split: `dcc.outerCol.sizes`
- Terminal panel inner split: `dcc.terminalGroups.sizes.${n}`

All three independent localStorage namespaces. GC sweep is per-prefix so no cross-stomping.

---

## 6. Header controls — concrete

| Slot | Element | Behaviour |
|---|---|---|
| 1 | `FaChevronDown` / `FaChevronRight` | Collapse/expand panel. Persist `apControl.terminalOpen`. |
| 2 | `FaTerminal` + "Terminal" | Static title |
| 3 | Status badge | `● live` / `⟳ reconnecting` (from TerminalCard:123-130) |
| 4 | Group count + line count | `2 groups · 415 lines` |
| 5 | `FaColumns` button | Split. Disabled if N ≥ 4 or panel collapsed. |
| 6 | `FaTimes` button | Close focused group. Visible if N > 1. |

Reuse `.dcc-thread-iconbtn` class (DashboardControlCenter.css:193).

---

## 7. Mobile / narrow viewport

ResizableSplit already collapses below 992 px (CSS:119-129).

- **Outer column-split:** drag handle disappears. Both panes get `height: auto`.
- **Inner row-split:** stacking groups vertically is worse than tabs. **Mitigation:** pass `disabled={isMobile}` to inner ResizableSplit AND short-circuit `TerminalPanel` to render only `groups[focusedIndex]` below 992 px.
- **Min-pane size:** `minSizes={[260, 140]}` on outer split — **140 px terminal floor** prevents zero-height drag.

---

## 8. Migration story

- No saved state: outer defaults `[60, 40]`, single terminal group, back-compatible.
- Existing `apControl.terminalOpen` etc. keep working.
- `TerminalCard.js` → thin shim: `export { default } from './TerminalPanel'` with `@deprecated` JSDoc.
- No localStorage migration — new keys are net-new namespaces.

---

## 9. Concrete file-edit list

| File | Change | Phase |
|---|---|---|
| `apps/web/src/dashboard/ResizableSplit.js` | Add `direction` prop. Axis abstraction. Update `clampPercentagesToMinPixels` to take generic `containerExtent`. | A |
| `apps/web/src/dashboard/ResizableSplit.css` | Add `[data-direction="column"]` selectors: handle `height: 6px; width: auto; cursor: row-resize`. | A |
| `apps/web/src/dashboard/__tests__/ResizableSplit.test.js` | New tests for column-direction drag math, vertical aria-orientation, ArrowUp/Down nudge. | A |
| `apps/web/src/pages/DashboardControlCenter.js` | Wrap chat-row + TerminalPanel in outer `<ResizableSplit direction="column">`. Replace `<TerminalCard>` with `<TerminalPanel>`. | A→B |
| `apps/web/src/pages/DashboardControlCenter.css` | Remove `.dcc-chat-row height: clamp()`. Add `.dcc-outer-col` wrapper height clamp. Tighten mobile rules. | A |
| `apps/web/src/dashboard/TerminalGroup.js` | **New.** Per-CLI tab strip + auto-scroll `<pre>` extracted from TerminalCard. | B |
| `apps/web/src/dashboard/TerminalGroup.css` | **New.** Per-group focus border + tab strip styles. | B |
| `apps/web/src/dashboard/TerminalPanel.js` | **New.** Wraps groups in `<ResizableSplit direction="row">`, owns collapse + header controls + focused-group state + mobile fallback. | B |
| `apps/web/src/dashboard/TerminalPanel.css` | **New.** Header strip, button cluster. | B |
| `apps/web/src/dashboard/TerminalCard.js` | Convert to `export { default } from './TerminalPanel'` shim. Remove next release. | B |

---

## 10. Test plan

1. Drag outer vertical divider down — chat shrinks, terminal grows; total height bounded.
2. Reload after drag — sizes restored from `dcc.outerCol.sizes`.
3. Double-click outer divider — resets to `[60, 40]`.
4. Keyboard: ArrowDown on focused outer divider — terminal grows 2% (5% with Shift).
5. Click split-column in terminal header — second group appears, no active tab.
6. Run `claude_code` in group 1, pick `codex` in group 2 — both stream live independently.
7. Close focused group when N=2 — sole group fills width.
8. Reload after split — groups + active tabs + sizes restored.
9. Navigate to `/integrations` and back — layout unchanged.
10. Viewport <992 px — outer splits to vertical stack; terminal renders only focused group.
11. Pro→Simple toggle — terminal unmounts; chat row regains standalone height.
12. Stale-key GC — after 1→3→1 group cycle, `localStorage` has only `dcc.terminalGroups.sizes.1`.

---

## 11. Risks

1. **Height-chain collapse on `.dcc-chat-row`.** Clamp at CSS:116 is single source of truth. Mitigation: new `.dcc-outer-col` wrapper with its own clamp; explicit chain `.dcc-outer-col → .rs-root → .rs-pane → .dcc-chat-row → .ap-card` all `height: 100%`.

2. **Terminal `<pre>` overflow.** TerminalGroup inherits `.tc-stream { flex:1; overflow-y:auto }`. TerminalPanel outer wrapper must be `overflow: hidden` to clip grid-rounding.

3. **Mobile terminal at 0 height.** `minSizes[1]=140` enforced during drag (ResizableSplit:247) AND on reload hydration (lines 178-192). Port enforcement to column-direction.

4. **`useViewportIsMobile` 992 px.** Same threshold OK for column direction.

5. **GC sweep on multiple prefixes.** Add test confirming `dcc.terminalGroups.sizes.3` survives editor-groups remount.

6. **Outer split unmounts on Pro↔Simple toggle.** Sizes persisted — last-used split restored.

7. **Auto-open behaviour** (TerminalCard:83-88). Moves to TerminalPanel. Chunks for a platform no group is showing → open panel + chunk lands in focused group's tab strip.

---

## 12. Sequencing

### Phase A — Vertical resize only (low risk)

1. Extend `ResizableSplit.js` with `direction` prop + axis abstraction.
2. `ResizableSplit.css` column-direction handle styles.
3. Tests for column-direction.
4. Wrap chat-row + existing `<TerminalCard>` in outer column ResizableSplit.
5. Adjust `DashboardControlCenter.css` height chain.

**Outcome:** drag chat/terminal divider; sizes persist; single-tab terminal unchanged.

### Phase B — Multi-pane terminal (builds on A)

1. Extract `TerminalGroup.js`.
2. Build `TerminalPanel.js`.
3. Convert `TerminalCard.js` to deprecated shim.
4. Wire `apControl.terminalGroups` + `apControl.terminalFocusedGroupId`.
5. Mobile single-group fallback.

**Outcome:** VSCode-style multi-pane terminal with split/close + per-group event routing.

Phase A is independently shippable. A→B is the sane order.

---

## Headline tradeoffs

1. **Extend ResizableSplit over sibling.** Saves ~250 lines duplication; centralises bug fixes.
2. **Outer wrapper height clamp** must move from `.dcc-chat-row` to new `.dcc-outer-col`. Most likely regression site.
3. **Mobile inner split is not useful** — render only focused group below 992 px.
4. **Cap N at 4** — easy to lift later.
5. **Phase A is independently valuable** — ~150-line diff, ship first.
6. **TerminalCard becomes one-line shim** — preserves imports through release boundary.

---

## Critical files

- `apps/web/src/dashboard/ResizableSplit.js`
- `apps/web/src/dashboard/ResizableSplit.css`
- `apps/web/src/dashboard/TerminalCard.js`
- `apps/web/src/pages/DashboardControlCenter.js`
- `apps/web/src/pages/DashboardControlCenter.css`
