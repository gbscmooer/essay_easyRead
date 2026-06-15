# EssayRead Design System — building with these components

A small, presentational React set extracted from the Easy Essay paper-reading app (academic / CV-ML
reading tools). Apple-leaning, frosted-glass, driven by a 6-seed-token color system.

## Setup — tokens are global, no provider needed

Every component is styled from CSS custom properties (the "seed tokens"). The bound `styles.css` defines
them at `:root` (the **light** preset by default), so components render correctly with **no wrapper or
provider**. For the **dark** preset, set `data-theme="dark"` and `data-mode="dark"` on an ancestor:

    <html data-theme="dark" data-mode="dark"> … </html>

Import components from the design system; `styles.css` is already linked by the design surface:

    import { ConfirmDialog, SkeletonCard, LoadingState } from 'essay-read-frontend'

## Styling idiom — CSS variables, not utility classes

There is **no exposed utility-class vocabulary** — do not assume Tailwind classes are present. Style your
own layout glue with the **seed-token CSS variables**, exactly as the components style themselves:

| Variable | Use |
|---|---|
| `--bg-base` | page background |
| `--bg-surface` | panel / card surface |
| `--bg-elevated` | raised surface (dialogs, popovers) |
| `--border`, `--border-light` | hairline borders |
| `--text-primary`, `--text-secondary`, `--text-muted` | text ladder |
| `--accent`, `--accent-subtle` | brand accent (blue) + tint |
| `--success`, `--warning`, `--error` (each + `-subtle`) | semantic status |
| `--seed-radius` | base corner radius (12px) |
| `--font-ui` | UI font stack (Inter Variable) |
| `--spring-ease` | shared easing curve |

Example — token-driven layout glue wrapping a DS component:

    <div style={{
      background: 'var(--bg-surface)',
      border: '1px solid var(--border)',
      borderRadius: 'var(--seed-radius)',
      padding: 20,
      fontFamily: 'var(--font-ui)',
      color: 'var(--text-primary)',
    }}>
      <SkeletonCard />
    </div>

## Components

- **Skeletons** — `SkeletonLine`, `SkeletonBlock`, `SkeletonCircle`, `SkeletonCard` (paper-card
  placeholder), `SkeletonAnalysis` (full analysis-panel placeholder). Shimmer loading primitives.
- **LoadingSpinner** — ring spinner; `size` is `'sm' | 'md' | 'lg'`.
- **LoadingState** — rich streaming-analysis panel (progress bar, step indicators, per-field checklist);
  driven by `status`, `partialResult`, `progress`.
- **ConfirmDialog** — confirm modal over a frosted backdrop; `variant` `'danger' | 'warning'`; controlled
  by `open`, with `onConfirm` / `onCancel`.
- **ShortcutHelp** — keyboard-shortcuts dialog; controlled by `open` / `onClose`.
- **Tooltip** — hover/focus tooltip (portaled to `<body>`); wraps a trigger via `children`, content via
  `content`, optional `placement` `'top' | 'bottom'`.

Read each component's `<Name>.d.ts` (props) and `<Name>.prompt.md` (usage) before composing.
`ConfirmDialog` and `ShortcutHelp` render `position: fixed` over the viewport — mount them at the app
root and toggle `open`. `Tooltip`'s bubble appears on hover/focus only.
