#!/usr/bin/env bash
# Make executable: chmod +x sync.sh
set -e

echo "Fetching all remotes..."
git fetch --all

echo "Resetting to origin/main (hard)..."
git reset --hard origin/main

echo "Pulling with ours strategy..."
git pull origin main --strategy-option ours

echo "Staging changes..."
git add .

echo "Committing..."
git commit -m "auto-sync" || echo "Nothing to commit."

echo "Pushing..."
git push

echo "Sync complete."
