# Hero landing state for fresh sessions

**Date:** 2026-06-02
**Scope:** `static/index.html` only (frontend; no backend, no Python changes)

## Context

The current empty state in `static/index.html` is a single 13px gray sentence rendered by `renderEmptyState(mode)` (around line 691): *"Let's start a conversation…"* (chat mode) or *"Enter a topic you'd like to learn…"* (learning mode). The bottom input bar stays pinned at `position: fixed; bottom: 0`, and there is nothing on the screen that does the work of a homepage.

We want to replace the empty state on a brand-new session with a hero landing: a large serif greeting, a small outlined icon, the existing input floated to the vertical centre, and four starter chips underneath. This mirrors the reference mockup style but uses the project's existing warm palette (`--bg: #FAF9F7`, `--accent: #CF6833`).

## Goals

- A welcoming landing for a fresh session that feels like a homepage, not an empty container.
- Reuse the existing input, send button, `/skill` popup, and chip styling — one source of truth, no duplicate components.
- Disappears the moment the session is no longer "fresh" (first message sent, mode switched, sidebar session selected, or streaming begins) and does not return unless the user clicks **New Chat**.

## Non-goals

- No animated/sliding transition from hero to chat in v1. The hero appears and disappears via the body class toggle; CSS transitions on existing properties are fine where they happen to apply but no choreographed motion.
- No new backend endpoints. All logic is client-side in `static/index.html`.
- No mobile-first redesign. The hero must not break narrow viewports, but is tuned for the desktop layout the project already targets.
- No internationalisation for the greeting copy — `main` is the English branch ([[project_i18n_bilingual_initiative]]); the `zh` branch can mirror this design separately if desired.

## User-visible behaviour

### When the hero appears

The hero shows when `_isFreshLanding === true`. That flag is initialised true on either of:

1. **Initial page load**, after `/state` hydrates, if **all three** are empty:
   - `_chatMessages.length === 0`
   - `_learningMessages.length === 0`
   - `_learningArtifactsEmpty()` returns true
2. **After `createNewSession()`** resolves and the same three conditions hold (which they always do for a fresh session).

### When the hero disappears

`_isFreshLanding` is set false (and stays false for the remainder of this page load) on either of:

- **Any message submission** — call `exitFreshLanding()` at the top of `handleSend()` (index.html:933), before the network call. This covers both user-typed sends and programmatic sends like the diagnosis `Study ${topic}` path at index.html:864 (which goes through `handleSend`).
- **Mode toggle click** — call `exitFreshLanding()` at the top of `_handleModeToggleClick(targetMode)` (definition near index.html:386, wired via the delegated click handler at index.html:416).

Sidebar interactions (`createNewSession` in sidebar.js:276, session-click in sidebar.js:75) both navigate via `window.location.href`, triggering a full page reload. The reload's hydration path re-evaluates `maybeEnterFreshLanding()`, which gives correct behaviour automatically — no explicit `exitFreshLanding()` needed there.

When the flag flips false, the hero is removed and the normal chat/learning layout is restored. If the active mode is still empty (e.g. user clicked **Learning** without sending anything), the existing `renderEmptyState('learning')` gray-sentence behaviour shows — same as today.

### Hero contents

Centered vertically and horizontally within the visible content area (i.e. respecting sidebar width):

1. **Headline row** — an outlined Material Symbol (`auto_stories`) at ~48px to the left, with the greeting headline immediately to its right.
2. **Greeting headline** — large serif, computed from local time:
   - `06:00–10:59` → *"Morning — what shall we learn today?"*
   - `11:00–16:59` → *"Afternoon — what shall we learn next?"*
   - `17:00–22:59` → *"Evening — what shall we learn tonight?"*
   - `23:00–05:59` → *"Late night — still curious? Let's learn."*
3. **The existing input bar** — the same `#bottomBar` DOM, moved inside the hero container while the hero is active (see Architecture below).
4. **Starter chips** — four chips rendered into the existing `#suggestBtns` slot:
   - `✨ Photosynthesis` (clicking populates input with `Photosynthesis`)
   - `✨ Linear algebra basics` (populates with `Linear algebra basics`)
   - `🔍 Diagnose what I already know` (populates with `Diagnose what I already know`)
   - `/skill Pick a teaching method` (populates with `/skill ` and triggers the existing skill popup via the existing `handleInputChange` path)

Clicking a chip fills the input and focuses it; the user still has to press Enter or click send. No auto-send.

## Architecture

### Approach: CSS state class + targeted DOM move

A single body class `is-fresh-landing` drives all visual changes. Two helper functions, `enterFreshLanding()` and `exitFreshLanding()`, also move the `#bottomBar` element between two anchor points so the input flows naturally inside the hero container without fighting `position: fixed` layout math.

**Why move `#bottomBar` rather than re-position it with fixed coordinates?** Because the bar must sit close to the headline at vertical centre, and getting two independently `position: fixed` elements to stay together across viewport sizes, sidebar-collapse transitions, and font loading is brittle. A flex-column inside `#heroLanding` solves it for free.

The textarea, send button, skill popup, and all existing handlers remain attached to the same DOM nodes throughout — no event re-wiring, no input duplication.

### DOM additions

Inside `<main id="mainContent">`, before `<div id="chatArea">`, add:

```html
<section id="heroLanding" aria-label="Start a new session">
  <div class="hero-inner">
    <div class="hero-headline-row">
      <span class="hero-mark material-symbols-outlined" aria-hidden="true">auto_stories</span>
      <h1 class="hero-headline" id="heroHeadline"></h1>
    </div>
    <!-- #bottomBar gets appended here when is-fresh-landing is active -->
  </div>
</section>
```

`#heroLanding` is `display: none` by default; CSS reveals it under `body.is-fresh-landing`.

### CSS additions

```css
/* Hero landing ──────────────────────────────────────────────────────────── */
#heroLanding { display: none; }

body.is-fresh-landing #chatArea,
body.is-fresh-landing #reviewBanner { display: none; }

body.is-fresh-landing #heroLanding {
  display: flex;
  flex: 1;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  padding: 32px;
  width: 100%;
}

.hero-inner {
  display: flex;
  flex-direction: column;
  gap: 28px;
  width: 100%;
  max-width: 720px;
}

.hero-headline-row {
  display: flex;
  align-items: center;
  gap: 18px;
}

.hero-mark.material-symbols-outlined {
  font-size: 48px;
  color: var(--text);
  font-variation-settings: 'wght' 300, 'FILL' 0;
  flex-shrink: 0;
}

.hero-headline {
  font-family: 'EB Garamond', 'Source Serif Pro', Georgia, 'Times New Roman', serif;
  font-size: clamp(34px, 5vw, 58px);
  font-weight: 500;
  line-height: 1.1;
  letter-spacing: -0.012em;
  color: var(--text);
  margin: 0;
}

/* When the bottom bar lives inside the hero, drop its fixed positioning */
body.is-fresh-landing #bottomBar {
  position: static;
  left: auto; right: auto; bottom: auto;
  background: transparent;
  border-top: none;
  padding: 0;
  width: 100%;
}

body.is-fresh-landing #bottomBar .hint-row {
  margin-top: 12px;
  justify-content: flex-start;
}
```

Note: `#bottomBar`'s default rule already includes `transition: left 0.2s`, which becomes a no-op once it's `position: static`. No conflict.

### Font

Add an EB Garamond link in `<head>` alongside the existing Inter and Material Symbols links:

```html
<link href="https://fonts.googleapis.com/css2?family=EB+Garamond:wght@400;500;600&display=swap" rel="stylesheet"/>
```

System-serif fallback (`Georgia`, `Times New Roman`) means the hero still looks coherent if Google Fonts is blocked.

### JavaScript additions

Add these near the existing render helpers (around line 691, where `renderEmptyState` lives):

```js
let _isFreshLanding = false;

function computeHeroGreeting() {
  const h = new Date().getHours();
  if (h >= 6  && h < 11) return 'Morning — what shall we learn today?';
  if (h >= 11 && h < 17) return 'Afternoon — what shall we learn next?';
  if (h >= 17 && h < 23) return 'Evening — what shall we learn tonight?';
  return "Late night — still curious? Let's learn.";
}

function renderHeroStarterChips() {
  const chips = [
    { label: '✨ Photosynthesis',                 fill: 'Photosynthesis' },
    { label: '✨ Linear algebra basics',          fill: 'Linear algebra basics' },
    { label: '🔍 Diagnose what I already know',   fill: 'Diagnose what I already know' },
    { label: '/skill  Pick a teaching method',    fill: '/skill ' },
  ];
  const slot = document.getElementById('suggestBtns');
  slot.innerHTML = chips.map((c, i) =>
    `<button class="hint-chip" data-hero-chip="${i}">${escapeHtml(c.label)}</button>`
  ).join('');
  slot.querySelectorAll('[data-hero-chip]').forEach(btn => {
    btn.addEventListener('click', () => {
      const c = chips[Number(btn.dataset.heroChip)];
      const input = document.getElementById('chatInput');
      input.value = c.fill;
      input.focus();
      // /skill chip: trigger the existing skill popup
      if (c.fill.startsWith('/skill')) handleInputChange({ target: input });
    });
  });
}

function enterFreshLanding() {
  if (_isFreshLanding) return;
  _isFreshLanding = true;
  document.body.classList.add('is-fresh-landing');
  document.getElementById('heroHeadline').textContent = computeHeroGreeting();
  const hero = document.querySelector('#heroLanding .hero-inner');
  const bar  = document.getElementById('bottomBar');
  if (bar.parentElement !== hero) hero.appendChild(bar);
  renderHeroStarterChips();
  document.getElementById('chatInput').focus();
}

function exitFreshLanding() {
  if (!_isFreshLanding) return;
  _isFreshLanding = false;
  document.body.classList.remove('is-fresh-landing');
  const bar = document.getElementById('bottomBar');
  if (bar.parentElement !== document.body) document.body.appendChild(bar);
  document.getElementById('suggestBtns').innerHTML = '';
}

function maybeEnterFreshLanding() {
  if (_chatMessages.length === 0 &&
      _learningMessages.length === 0 &&
      _learningArtifactsEmpty()) {
    enterFreshLanding();
  } else {
    exitFreshLanding();
  }
}
```

### Wire-up

Four touch points, all in `static/index.html`. No changes to `static/sidebar.js`.

1. **State hydration completion.** The IIFE around index.html:1442 calls `renderActiveView(_sessionMode)` after populating `_chatMessages`, `_learningMessages`, and `_learningArtifacts` from `/state`. Append `maybeEnterFreshLanding();` on the line right after `renderActiveView(_sessionMode);`.
2. **`handleSend()` entry point** (index.html:933). Call `exitFreshLanding();` as the first line of the function body, before any state reads or network calls.
3. **`_handleModeToggleClick(targetMode)`** (definition near index.html:386; receives the clicked mode from the delegated handler at index.html:416). Call `exitFreshLanding();` as the first line.
4. **`renderEmptyState(mode)`** (index.html:691). At the top of the function, `if (_isFreshLanding) return;` — the hero owns the empty state and the function's normal gray-sentence rendering should not run while the hero is active.

Sidebar interactions (`createNewSession` in sidebar.js:276, session-click in sidebar.js:75) both navigate via `window.location.href`, triggering a full page reload. The reload's hydration path re-runs `maybeEnterFreshLanding()`, which gives correct behaviour automatically — no `exitFreshLanding()` call needed from sidebar.js.

## Edge cases

- **Sidebar collapsed.** `#mainContent` already adapts via `body.sidebar-collapsed #mainContent { margin-left: 0 }`. The hero is a flex child of `#mainContent`, so it inherits the new width automatically. No extra CSS needed.
- **Skill popup positioning.** The popup is `position: absolute; bottom: 100%` relative to `.input-outer`. In the hero, the input is inside the centred flex column, so the popup still opens upward above the input. Works without changes.
- **Font load delay.** While EB Garamond is loading, the headline falls back to Georgia (acceptable). After load, it swaps in. Use `display=swap` in the Google Fonts URL (already in the link above).
- **`reviewBanner` race.** If a review banner becomes relevant on a fresh session (rare), it would be hidden by the hero. Acceptable — the banner appears on subsequent loads after the hero is dismissed.
- **Programmatic sends.** The diagnosis `Study ${topic}` path at index.html:864 goes through `handleSend(...)`, so the `exitFreshLanding()` call at the top of `handleSend()` covers it.
- **No greeting recompute on the hour.** If the user lingers on the hero past a time-of-day boundary, the headline does not auto-update. Acceptable — recomputing on a timer for an edge case adds complexity for no real value.

## Testing

This is a UI change. Verification is manual; the project already runs from `./run` (development log; see CLAUDE.md if present).

Manual checks:

1. Load the app on an empty fresh session → hero appears, headline matches current local time bucket, input is centred, four chips show below.
2. Type and send a message → hero collapses; input snaps back to the bottom-pinned bar; chat renders normally.
3. Click **New Chat** → hero reappears.
4. From hero, click **Learning** in top nav → hero disappears; the small gray *"Enter a topic…"* empty-state text appears in its place. Switching back to **Chat** does *not* restore the hero (only **New Chat** does).
5. Click each starter chip → input fills with the expected text, focus moves to input. Send button still works.
6. Click the `/skill` chip → input fills with `/skill ` and the existing skill popup opens.
7. Collapse the sidebar → hero stays centred in the new wider area; no horizontal shift jank beyond the existing `transition: 0.2s`.
8. Open the app at 06:00, 12:00, 19:00, 02:00 (or fake `Date.prototype.getHours` in devtools) → each bucket shows the correct greeting.
9. Block Google Fonts in devtools → headline still readable in Georgia fallback.
10. Confirm `/state` hydration on an existing in-progress session does NOT show the hero (regression check).

## Out of scope

- Animated hero-to-chat transition (slide/fade).
- Mobile-specific layout tuning beyond `clamp()` on the headline.
- Personalising the greeting with the user's name (no auth/identity surface exists today).
- Mode-specific hero copy (we use one greeting regardless of mode).
- Greeting localisation for the `zh` branch.
