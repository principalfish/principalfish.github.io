#!/usr/bin/env bash
set -euo pipefail

if ! command -v npm >/dev/null 2>&1; then
	echo "npm is required to build frontend assets before serving." >&2
	exit 1
fi

if [[ ! -d node_modules ]]; then
	echo "Installing frontend tool dependencies..."
	npm install
fi

echo "Building vendored frontend assets..."
npm run vendor:d3

WATCH_FILES=(
	electionmaps/electionmaps.js
	uselectionmaps/uselectionmaps.js
	electionmapslogic/app.js
	electionmapslogic/state.js
	electionmapslogic/utils.js
	electionmapslogic/dom.js
	electionmapslogic/files.js
	electionmapslogic/shell-loader.js
	electionmapslogic/shell.html
	electionmapslogic/features/predict.js
	electionmapslogic/features/predict-controller.js
	electionmapslogic/features/predict-view.js
	electionmapslogic/features/polltracker.js
	electionmapslogic/features/polltracker-view.js
	electionmapslogic/features/postcode.js
	electionmapslogic/features/senate-baseline.js
	electionmapslogic/features/senate-chamber.js
	electionmapslogic/features/senate-forecast-controller.js
	electionmapslogic/features/senate-forecast-view.js
	electionmapslogic/mobile-sidebar.js
	electionmapslogic/mobile-sidebar.css
	electionmapslogic/maps.css
	site/styles.css
	site/topbar.js
	site/topbar.css
)

get_checksums() {
	sha256sum "${WATCH_FILES[@]}" 2>/dev/null || true
}

LAST_CHECKSUMS="$(get_checksums)"

echo "Minifying electionmaps JS/CSS..."
npm run minify:electionmaps

PORT="${PORT:-8000}"
echo "Starting static server on http://127.0.0.1:${PORT}"
python3 -m http.server "${PORT}" &
SERVER_PID=$!
sleep 1
if ! kill -0 "$SERVER_PID" 2>/dev/null; then
	echo "Error: static server failed to start (is port ${PORT} already in use?)" >&2
	exit 1
fi

trap 'echo "Stopping server..."; kill "$SERVER_PID" 2>/dev/null; exit 0' INT TERM

echo "Watching for changes: ${WATCH_FILES[*]}"

while kill -0 "$SERVER_PID" 2>/dev/null; do
	sleep 1
	CURRENT_CHECKSUMS="$(get_checksums)"
	if [[ "$CURRENT_CHECKSUMS" != "$LAST_CHECKSUMS" ]]; then
		echo "Changes detected, rebuilding..."
		# Keep the pre-build snapshot so edits during this attempt trigger another build.
		LAST_CHECKSUMS="$CURRENT_CHECKSUMS"
		npm run minify:electionmaps && echo "Rebuild complete." || echo "Rebuild failed — fix errors and save again."
	fi
done
