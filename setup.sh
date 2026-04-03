#!/usr/bin/env bash
set -euo pipefail

echo "Setting up Reel Intel optional transcript dependencies..."

if command -v brew >/dev/null 2>&1; then
  echo "Installing yt-dlp via brew..."
  brew list yt-dlp >/dev/null 2>&1 || brew install yt-dlp
else
  echo "Homebrew not found. Install yt-dlp manually for transcript mode."
fi

if ! command -v whisper >/dev/null 2>&1; then
  cat <<'MSG'

Local whisper CLI is not installed.
Transcript mode still works if OPENAI_API_KEY is set.

Optional local install options:
- pipx install openai-whisper
- or install whisper in a venv and expose CLI in PATH

MSG
fi

echo "Done. You can now run:"
echo "python3 tools/reel-intel/reel_intel.py <reel-url> --try-transcript"
