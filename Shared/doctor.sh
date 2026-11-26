#!/bin/bash
echo "    ███████╗███████╗███╗   ██╗████████╗██╗  ██╗██████╗  █████╗ ██╗     ██╗██╗  ██╗"
echo "    ╚══███╔╝██╔════╝████╗  ██║╚══██╔══╝██║  ██║██╔══██╗██╔══██╗██║     ██║╚██╗██╔╝"
echo "      ███╔╝ █████╗  ██╔██╗ ██║   ██║   ███████║██████╔╝███████║██║     ██║ ╚███╔╝"
echo "     ███╔╝  ██╔══╝  ██║╚██╗██║   ██║   ██╔══██║██╔══██╗██╔══██║██║     ██║ ██╔██╗"
echo "    ███████╗███████╗██║ ╚████║   ██║   ██║  ██║██║  ██║██║  ██║███████╗██║██╔╝ ██╗"
echo "    ╚══════╝╚══════╝╚═╝  ╚═══╝   ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝╚═╝╚═╝  ╚═╝"
echo
# ZenthaMini Doctor — local health check (read-only)
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
if [ "$(basename "$HERE")" = "Shared" ]; then
  ROOT="$(cd "$HERE/.." && pwd)"
  SHARED="$HERE"
else
  ROOT="$HERE"
  SHARED="$HERE/Shared"
fi
echo "=== ZenthaMini Doctor ==="
echo "Root: $ROOT"
echo "Version: $(cat "$ROOT/VERSION" 2>/dev/null || echo unknown)"
echo
echo "[1] Python:"; python3 --version 2>&1
echo "[2] Ports:"; (curl -s http://127.0.0.1:3333/api/version && echo) || echo "  chat_server not running on 3333"
echo "[3] Ollama:"; curl -s http://127.0.0.1:11434/api/tags | head -c 80; echo
echo "[4] Disk:"; df -h "$ROOT" | tail -1
echo "[5] Data dir:"; ls -la "$SHARED/chat_data" 2>/dev/null || echo "  (not created yet)"
echo "[6] Logs (last errors):"; grep -i "error\|traceback" "$SHARED/logs/chat_server.log" 2>/dev/null | tail -5 || echo "  (none)"
echo "[7] Permissions:"; stat -c '%a %n' "$SHARED/chat_data/settings.json" 2>/dev/null || stat -f '%Sp %N' "$SHARED/chat_data/settings.json" 2>/dev/null || echo "  (no settings yet)"
echo "=== Done ==="
