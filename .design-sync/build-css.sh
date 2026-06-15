#!/usr/bin/env bash
# Build the design-system stylesheet for /design-sync (cfg.cssEntry).
#
# Compiles frontend/src/styles/globals.css with the app's own Tailwind config,
# scoped to the curated components, then prepends the :root token layer
# (.design-sync/tokens-root.css). Output: frontend/ds-styles.css (gitignored,
# regenerated here — this is cfg.cssEntry).
#
# Run from the repo root:  bash .design-sync/build-css.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/frontend"

CACHE="$ROOT/.design-sync/.cache"
mkdir -p "$CACHE"

# Scope to the curated components so the sheet stays tight (the [data-theme]
# override blocks in globals.css ship regardless).
CONTENT='./src/components/{Skeleton,Tooltip,ConfirmDialog,LoadingSpinner,LoadingState,ShortcutHelp}.tsx'

node_modules/.bin/tailwindcss \
  -c tailwind.config.js \
  -i src/styles/globals.css \
  --content "$CONTENT" \
  -o "$CACHE/compiled.css"

# Prepend the :root token layer (lifts the light preset out of [data-theme]).
cat "$ROOT/.design-sync/tokens-root.css" "$CACHE/compiled.css" > "$ROOT/frontend/ds-styles.css"

echo "✓ wrote frontend/ds-styles.css ($(wc -c < "$ROOT/frontend/ds-styles.css" | tr -d ' ') bytes)"
