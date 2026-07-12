# PromptLab Dashboard — UX Redesign Plan

**Primary user:** 3ie researchers managing extraction projects
**Top priority:** "Is the system improving?" — version-over-version progress, accept rates, what changed
**Scope:** Full redesign — new layout, information architecture, dark mode, responsive design

---

## The Problem Today

The current dashboard was built engineer-first: it shows every metric, every model, every chart, all at once. A 3ie researcher opening it sees a leaderboard of model F1 scores, a cost/quality scatter plot, calibration curves, confusion matrices, and a methodology glossary — but **can't answer the one question that matters: "are we getting better?"**

The audit found 27 UX issues. The top 5:
1. **No version-over-version story** — the dashboard shows current state but not progress over time
2. **Silent failures** — 9 of 11 API calls swallow errors; sections vanish with no explanation
3. **No per-run inspection** — you can see a model has 5 errors but can't see what they were
4. **Hardcoded gate (0.9)** — the most prominent panel ignores the backend's actual threshold
5. **Information overload** — the full metrics table (the most useful view) is hidden behind a `<details>`

---

## Design Principles

1. **Progress over state.** The hero of the dashboard is "did the last optimization cycle improve things?" — not "what's the current F1?"
2. **Action over data.** Every screen should answer "what should I do next?" — review a field, re-run extraction, check a failure
3. **Honest about gaps.** If data is missing or the API failed, say so — never silently show "—"
4. **Mobile-aware.** 3ie researchers check the dashboard on their phones; the sidebar and tables must work on 360px

---

## New Information Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Header: project switcher · dark-mode toggle · [About]   │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  HERO: "Is the system improving?"                       │
│  ┌──────────────────────────────────────────────────┐  │
│  │  3 fields improved this cycle · 2 need review     │  │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ │  │
│  │  │sector   │ │sub-sect │ │authors  │ │country  │ │  │
│  │  │ 84% ↑12 │ │ 64% ↑6  │ │ 68% —   │ │ 78% ↑3  │ │  │
│  │  │ 2 acc   │ │ 1 acc   │ │ plateau │ │ ✓ done  │ │  │
│  │  └─────────┘ └─────────┘ └─────────┘ └─────────┘ │  │
│  └──────────────────────────────────────────────────┘  │
│                                                         │
│  FIELD DETAIL (click a field card above)                │
│  ┌──────────────────────────────────────────────────┐  │
│  │  sector_name                                      │  │
│  │  ┌────────────────────────────────────────────┐  │  │
│  │  │ VERSION PROGRESSION (the hero chart)        │  │  │
│  │  │ v1 ──→ v2 ──→ v3                            │  │  │
│  │  │ 60%    72%    84%  (gate: 90%)              │  │  │
│  │  │ ↑+12   ↑+12   what changed in each version  │  │  │
│  │  └────────────────────────────────────────────┘  │  │
│  │                                                  │  │
│  │  ┌─────────────┐  ┌──────────────────────────┐  │  │
│  │  │ MODEL CARDS │  │ WHAT WENT WRONG           │  │  │
│  │  │ gpt-mini 84%│  │ Top confusion patterns:   │  │  │
│  │  │ claude   82%│  │ Social protection → Health│  │  │
│  │  │ gemini   83%│  │ (4 errors, 50% of total) │  │  │
│  │  │ [click to  │  │                            │  │  │
│  │  │  inspect]  │  │ [See example errors →]     │  │  │
│  │  └─────────────┘  └──────────────────────────┘  │  │
│  └──────────────────────────────────────────────────┘  │
│                                                         │
│  SYSTEM HEALTH (bottom, collapsible)                    │
│  ┌──────────────────────────────────────────────────┐  │
│  │  ● Optimizer: healthy · 12% accept rate (24h)    │  │
│  │  ● Supervisor: cycle 3/5 · extracting sector_name │  │
│  │  ● No errors in the last 24h                      │  │
│  └──────────────────────────────────────────────────┘  │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### What changed from the current layout

| Current | New | Why |
|---------|-----|-----|
| Field nav sidebar (left, always visible) | Field cards in the hero (top, click to expand) | Researchers think field-first, not nav-first; cards show progress at a glance |
| Leaderboard bar chart + cost scatter (top) | Version progression chart (top of field detail) | The #1 question is "is it improving?" not "which model is cheapest?" |
| Full metrics table hidden in `<details>` | Model cards with click-to-inspect | The table is too dense; cards show what matters, clicking opens per-run detail |
| Methodology panel at bottom | Contextual tooltips + a "Help interpreting this" link | The glossary is useful but shouldn't be a wall of text at the bottom |
| SupervisorStatusBar + LiveActivity (separate panels) | System Health (single collapsible panel) | Researchers don't care about the supervisor's internals — just "is it healthy?" |
| No per-run inspection | "What went wrong?" panel with clickable error examples | The #1 missing feature — researchers need to see what the model gets wrong |

---

## Component Plan (new + modified)

### New components

| Component | Purpose | Data source |
|-----------|---------|-------------|
| `ImprovementHero` | Top-of-page field cards showing baseline→current accuracy, # accepted iterations, status (improving/plateaued/done) | `stageStatus` per field + `iterations` (accepted count) |
| `VersionProgressionChart` | Line chart of accuracy per prompt version, with gate reference line and accepted/rejected markers | `runVersions` + `stageStatus.gate_threshold` |
| `ErrorInspection` | "What went wrong?" — shows top confusion patterns + clickable examples of wrong predictions | New backend endpoint: `GET /runs?field=…&model=…&correct=false&limit=10` |
| `SystemHealth` | Single collapsible panel: optimizer health (accept/failure rate), supervisor status, recent errors | `/api/activity` (already has `optimizer_health`) |
| `DarkModeToggle` | Switch between light/dark, persisted in localStorage | CSS variables only |
| `ErrorBoundary` | Catches API failures and shows "Data unavailable — [Retry]" instead of silently hiding | Wrap all API-consuming components |
| `SkeletonLoader` | Shimmer placeholder preserving layout while data loads | CSS animation, no data |

### Modified components

| Component | Changes |
|-----------|---------|
| `App.tsx` | Remove sidebar layout → hero + field detail. Remove hardcoded `GATE = 0.9`. Remove silent `.catch(() => {})` → wrap in `ErrorBoundary`. Remove wizard modal. Add dark mode state. |
| `FieldOverview` | → becomes `ImprovementHero` (field cards, not a nav list). Use `stageStatus.gate_threshold` not hardcoded 0.9. Show Δ from previous version. |
| `ModelCard` | Simplify: collapsed view shows accuracy + accepted version count + cost. Expanded shows iteration chart (with gate line + accepted markers) + "See errors" button that opens `ErrorInspection`. Remove the hidden `<details>` for honesty score — show it inline. |
| `ModelComparisonTable` | Make it the default view (not hidden). Simplify to 5 columns: model, accuracy, Δ from baseline, cost/1k, status. Full columns in a "show more" toggle. |
| `AggregateCharts` | Demote: move below the model cards as a secondary view. Add gate reference line to bar chart. Fix scatter label overlap. |
| `IterationChart` | Add gate threshold reference line. Add green/red markers for accepted/rejected iterations. Use CSS variables for colors. |
| `ConfusionMatrix` | Add row/col totals. Add "normalize" toggle. Don't show "Loading…" for null — show skeleton. |
| `VersionProgressionTable` | Show Δ per version (not just first→latest). Don't hide when <2 versions — show "Only baseline so far." Use `gate_threshold` not hardcoded 0.9. |
| `Methodology` | Move to a slide-in panel triggered by a "?" button, not a bottom-of-page collapsible. Add clickable citation links. |
| `LiveActivity` | Merge into `SystemHealth`. Only poll when panel is expanded. Add `document.hidden` check to stop polling when tab is hidden. |
| `SupervisorStatusBar` | Merge into `SystemHealth`. Remove N+1 polling — use a single `/api/activity` call. |
| `Walkthrough` | Only start after data loads. Add "don't show again" persistence. Update tour targets to new layout. |
| `WorkSavedChart` | Show "Not enough data yet" instead of returning null. `useMemo` the curve computation. Name the best model in the callout. |

### Removed components

| Component | Why |
|-----------|-----|
| `NewProjectWizard` + all wizard steps | User requested removal — focus on existing project dashboard |
| `PromptLineage` | Dead code (never imported) |
| `Step2ExclusionCriteria` | Dead code (never imported) |

### Backend changes needed

| Change | Why |
|--------|-----|
| `GET /api/projects/{slug}/fields/{field}/runs?model=…&correct=false&limit=10` | Per-run error inspection — the #1 missing feature. Backend already has `GET /runs` but it returns ALL runs; add query params for filtering by correctness + pagination. |
| `stageStatus` already returns `opt_status`, `opt_reason`, `n_candidates`, `n_accepted`, `judge_disagreement` | These are already in the API response but the frontend doesn't show them — just wire them up. |

---

## Implementation Phases

### Phase 1: Fix the critical bugs (no layout change)
1. Replace hardcoded `GATE = 0.9` with `stageStatus.gate_threshold` everywhere
2. Replace silent `.catch(() => {})` with `ErrorBoundary` + retry button
3. Add `SkeletonLoader` for all async data
4. Remove the wizard modal (and all wizard components)
5. Delete dead code (`PromptLineage`, `Step2ExclusionCriteria`)

### Phase 2: The "is it improving?" story
6. Build `ImprovementHero` (field cards with Δ from baseline + accepted iteration count)
7. Build `VersionProgressionChart` (accuracy per version with gate line + accepted markers)
8. Surface `opt_status` / `opt_reason` / `judge_disagreement` in `ModelCard`
9. Wire `ModelComparisonTable` as the default view (un-hide it)

### Phase 3: Error inspection
10. Add backend endpoint: `GET /runs?field=…&model=…&correct=false&limit=10`
11. Build `ErrorInspection` panel: top confusion patterns + clickable wrong predictions
12. Wire "See errors" button in `ModelCard` → opens `ErrorInspection`

### Phase 4: System health + polish
13. Merge `LiveActivity` + `SupervisorStatusBar` → `SystemHealth` (single panel, poll on expand only)
14. Add `document.hidden` check to stop polling when tab is hidden
15. Add dark mode (CSS variables + toggle)
16. Add responsive design (mobile nav, responsive tables, responsive wizard removal)

### Phase 5: Accessibility
17. Add `Skip to content` link
18. Add `aria-live` regions for job status changes
19. Fix keyboard navigation (Space key on ModelCard, focus styles on all buttons)
20. Fix color contrast (warn color on small text fails AA)
21. Add `aria-current="page"` to active field card

---

## Design System Updates

### Dark mode
- Add `:root[data-theme="dark"]` CSS variable overrides
- Replace all hardcoded hex colors with CSS variables
- Persist preference in `localStorage["promptlab_theme"]`
- Default to system preference (`prefers-color-scheme`)

### Responsive breakpoints
- **Mobile (<640px):** Field cards stack vertically, model table becomes cards, sidebar removed, charts full-width
- **Tablet (640-900px):** 2-column field cards, charts side-by-side, table scrolls
- **Desktop (>900px):** Current 3-column layout (hero cards → field detail → system health)

### Typography hierarchy
- H1 (page title): 1.5rem, 600 weight
- H2 (field name): 1.25rem, 600 weight
- Field card accuracy: 2rem, 700 weight (the number that matters most)
- Body: 1rem, 400 weight
- Muted/meta: 0.875rem, 400 weight, `--text-muted`
- Monospace (model IDs, log): JetBrains Mono 0.85rem

---

## Success Metrics

| Metric | Current | Target |
|--------|---------|--------|
| Time to answer "is it improving?" | ~30s (scan leaderboard, find field, check versions) | <3s (hero cards show Δ immediately) |
| Time to see what a model gets wrong | Impossible (no per-run view) | <10s (click model → "See errors") |
| API failures visible to user | 0% (all silent) | 100% (ErrorBoundary + retry) |
| Mobile usability | Unusable (sidebar + tables overflow) | Functional (stacked cards, responsive tables) |
| Accessibility (WCAG AA) | Fails (contrast, keyboard, color-only) | Passes |
