#!/usr/bin/env bash
# Install Wendao on the server and check that it can search the course workspace.
set -euo pipefail

APP_DIR="${APP_DIR:-/srv/mle-course-helper/wendao}"
WORKSPACE="${WORKSPACE:-/srv/mle-course-helper/workspace}"
VENV_DIR="${VENV_DIR:-$APP_DIR/.venv}"

cd "$APP_DIR"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required. Install it with: curl -LsSf https://astral.sh/uv/install.sh | sh"
  exit 1
fi

# Install the exact versions pinned in uv.lock, the teacher tools, and gunicorn for the systemd service.
UV_PROJECT_ENVIRONMENT="$VENV_DIR" uv sync --frozen --no-dev --extra teacher --extra deploy

"$VENV_DIR/bin/wendao" ask --workspace "$WORKSPACE" --search-only "What is convex hull?"

echo "Wendao is installed in $VENV_DIR"
echo "Next: copy deploy/env.example to /etc/mle-course-helper.env and install the systemd service."
