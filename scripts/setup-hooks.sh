#!/bin/bash
# Setup git hooks for the repository

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "Setting up git hooks..."

# Configure git to use .githooks directory
git config core.hooksPath .githooks

# Make hooks executable
chmod +x "$PROJECT_ROOT/.githooks/"*

echo "Git hooks configured successfully!"
echo "Hooks location: .githooks/"
