#!/usr/bin/env bash
# Upload Wendao, a course workspace, and the built course website to the server.
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 USER@SERVER /path/to/MLE4217_5219_book [/path/to/workspace]"
  echo "The workspace defaults to examples/mle4217_5219 in this repository."
  exit 2
fi

REMOTE="$1"
BOOK_REPO="$2"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE="${3:-$REPO_DIR/examples/mle4217_5219}"

for folder in "$BOOK_REPO" "$WORKSPACE"; do
  if [[ ! -d "$folder" ]]; then
    echo "Folder not found: $folder"
    exit 1
  fi
done

echo "Building book..."
(cd "$BOOK_REPO" && make web)

echo "Syncing Wendao to $REMOTE:/srv/mle-course-helper/wendao/"
rsync -az --delete \
  --exclude .git --exclude .env --exclude .venv --exclude __pycache__ --exclude '*.pyc' \
  "$REPO_DIR/" "$REMOTE:/srv/mle-course-helper/wendao/"

echo "Syncing the workspace to $REMOTE:/srv/mle-course-helper/workspace/"
rsync -az --delete \
  --exclude .env --exclude build/reports --exclude usage.db --exclude .wendao-secret \
  "$WORKSPACE/" "$REMOTE:/srv/mle-course-helper/workspace/"

echo "Syncing book HTML to $REMOTE:/srv/mle-course-helper/book/"
rsync -az --delete \
  "$BOOK_REPO/_build/html/" "$REMOTE:/srv/mle-course-helper/book/"

echo "Done. On the server, run:"
echo "  cd /srv/mle-course-helper/wendao && bash deploy/install_backend.sh"
echo "  sudo systemctl restart mle-course-helper"
