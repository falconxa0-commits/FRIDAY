#!/bin/bash
# Friday AI Assistant — Linux/Mac installer
# Usage: bash install.sh
set -e

echo "◆ Installing Friday AI Assistant..."
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "✗ Python 3 is required but not installed."
    echo "  Install from https://python.org or your package manager."
    exit 1
fi

# Check pip
if ! command -v pip3 &> /dev/null; then
    echo "✗ pip3 is required but not installed."
    exit 1
fi

echo "[1/4] Installing Python dependencies…"
pip3 install -r requirements.txt
echo ""

echo "[2/4] Creating .env file…"
if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        cp .env.example .env
        echo "  Created .env from .env.example"
    else
        echo "GLM_API_KEY=" > .env
        echo "  Created empty .env"
    fi
else
    echo "  .env already exists — leaving it alone"
fi
echo ""

echo "[3/4] Installing Friday console script…"
pip3 install -e .
echo ""

echo "[4/4] Verifying install…"
if command -v friday &> /dev/null; then
    echo "  ✓ friday command is available"
else
    echo "  (friday command may not be on PATH until you restart your shell)"
    echo "  You can also run: python3 -m cli"
fi
echo ""

echo "═══════════════════════════════════════════════════════"
echo "  ✓ Friday installed successfully!"
echo "═══════════════════════════════════════════════════════"
echo ""
echo "Next steps:"
echo "  1. Get a free Z.ai GLM key at: https://open.bigmodel.cn"
echo "  2. Edit .env and set GLM_API_KEY=your-real-key"
echo "  3. Run:  friday"
echo ""
echo "Optional:"
echo "  • Start the web dashboard:  uvicorn api.main:app --port 8000"
echo "  • Then open http://localhost:8000"
echo ""
