#!/bin/bash
echo "    ███████╗███████╗███╗   ██╗████████╗██╗  ██╗██████╗  █████╗ ██╗     ██╗██╗  ██╗"
echo "    ╚══███╔╝██╔════╝████╗  ██║╚══██╔══╝██║  ██║██╔══██╗██╔══██╗██║     ██║╚██╗██╔╝"
echo "      ███╔╝ █████╗  ██╔██╗ ██║   ██║   ███████║██████╔╝███████║██║     ██║ ╚███╔╝"
echo "     ███╔╝  ██╔══╝  ██║╚██╗██║   ██║   ██╔══██║██╔══██╗██╔══██║██║     ██║ ██╔██╗"
echo "    ███████╗███████╗██║ ╚████║   ██║   ██║  ██║██║  ██║██║  ██║███████╗██║██╔╝ ██╗"
echo "    ╚══════╝╚══════╝╚═╝  ╚═══╝   ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝╚═╝╚═╝  ╚═╝"
echo
#     ███████╗███████╗███╗   ██╗████████╗██╗  ██╗██████╗  █████╗ ██╗     ██╗██╗  ██╗
#     ╚══███╔╝██╔════╝████╗  ██║╚══██╔══╝██║  ██║██╔══██╗██╔══██╗██║     ██║╚██╗██╔╝
#       ███╔╝ █████╗  ██╔██╗ ██║   ██║   ███████║██████╔╝███████║██║     ██║ ╚███╔╝
#      ███╔╝  ██╔══╝  ██║╚██╗██║   ██║   ██╔══██║██╔══██╗██╔══██║██║     ██║ ██╔██╗
#     ███████╗███████╗██║ ╚████║   ██║   ██║  ██║██║  ██║██║  ██║███████╗██║██╔╝ ██╗
#     ╚══════╝╚══════╝╚═╝  ╚═══╝   ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝╚═╝╚═╝  ╚═╝

# ===================================================
#  ZenthaMini - Fast Web Chat (Mac)
# ===================================================

echo "==================================================="
echo "    ZenthaMini - Fast Web Chat Mode (Mac)"
echo "==================================================="
echo ""
echo "  Launches the AI engine + browser chat UI."
echo "  All chats auto-save to the USB drive."
echo ""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
USB_ROOT="$(dirname "$SCRIPT_DIR")"
SHARED_DIR="$USB_ROOT/Shared"
OLLAMA_RUNTIME="$SHARED_DIR/.ollama-runtime"
mkdir -p "$OLLAMA_RUNTIME"

# ---- Full portability: keep EVERYTHING on the USB ----
export OLLAMA_MODELS="$SHARED_DIR/models/ollama_data"
export OLLAMA_HOME="$OLLAMA_RUNTIME"
export OLLAMA_TMPDIR="$OLLAMA_RUNTIME/tmp"
export OLLAMA_ORIGINS="*"
export OLLAMA_HOST="127.0.0.1:11434"
mkdir -p "$OLLAMA_RUNTIME/tmp"
# -------------------------------------------------------

# ---- ZenthaTech engine switcher (multi-OS): aktifkan runtime macOS ----
# Shared/lib/ollama dipakai bersama Linux & Mac. Bila isinya engine Linux
# (ELF), ganti dengan engine macOS yang distaging di Shared/.engines/macos.
ZT_LIB="$SHARED_DIR/lib/ollama"
ZT_MAC_SRC="$SHARED_DIR/.engines/macos"
if [ -d "$ZT_MAC_SRC" ] && { [ ! -f "$ZT_LIB/llama-server" ] || ! file "$ZT_LIB/llama-server" 2>/dev/null | grep -q "Mach-O"; }; then
    if [ -f "$ZT_LIB/llama-server" ] && file "$ZT_LIB/llama-server" 2>/dev/null | grep -q "ELF" && [ ! -d "$SHARED_DIR/.engines/linux" ]; then
        mkdir -p "$SHARED_DIR/.engines/linux"
        cp -R "$ZT_LIB/." "$SHARED_DIR/.engines/linux/" 2>/dev/null && echo "[switch] Engine Linux dicadangkan ke .engines/linux"
    fi
    rm -rf "$ZT_LIB"
    cp -R "$ZT_MAC_SRC" "$ZT_LIB" && chmod +x "$ZT_LIB"/* 2>/dev/null
    echo "[switch] Engine macOS diaktifkan (Shared/lib/ollama)."
fi
# ---------------------------------------------------------------------

# Check if the portable Mac engine is downloaded
if [ ! -x "$SHARED_DIR/bin/ollama-darwin" ] || [ ! -f "$SHARED_DIR/lib/ollama/llama-server" ]; then
    echo "==================================================="
    echo "  ERROR: Mac AI Engine Not Found or Incomplete!"
    echo "==================================================="
    echo ""
    echo "  It looks like the portable Ollama runtime is missing"
    echo "  ollama-darwin or lib/ollama/llama-server."
    echo ""
    echo "  Please double-click 'install.command' in this Mac folder"
    echo "  first to safely download the full runtime."
    echo ""
    read -n 1 -s -r -p "Press any key to continue..."
    exit 1
fi

# Check if Ollama is already running
if curl -s http://127.0.0.1:11434/api/tags > /dev/null 2>&1; then
    echo "[OK] Ollama engine is already running!"
else
    echo "Starting offline Mac AI Engine..."
    HOME="$OLLAMA_RUNTIME" "$SHARED_DIR/bin/ollama-darwin" serve &
    OLLAMA_PID=$!
    
    echo "Waiting for engine to initialize..."
    OLLAMA_READY=false
    for i in $(seq 1 60); do
        if curl -s http://127.0.0.1:11434/api/tags | grep -q '"models"'; then
            OLLAMA_READY=true
            break
        fi
        sleep 1
    done
    if [ "$OLLAMA_READY" != true ]; then
        echo "ERROR: Ollama did not become ready within 60 seconds."
        if [ -n "$OLLAMA_PID" ]; then
            kill "$OLLAMA_PID" 2>/dev/null
        fi
        read -n 1 -s -r -p "Press any key to continue..."
        exit 1
    fi
    echo "[OK] Engine is online!"
fi

echo ""
echo "==================================================="
echo "  AI ENGINE IS RUNNING"
echo "  Chat UI will open automatically."
echo "  Press Ctrl+C to shut down."
echo "==================================================="
echo ""


# Launch Python chat server using system Python (comes pre-installed on Mac)
if command -v python3 &> /dev/null; then
    python3 "$SHARED_DIR/chat_server.py"
elif command -v python &> /dev/null; then
    python "$SHARED_DIR/chat_server.py"
else
    echo "ERROR: Python not found. Please type 'brew install python' in terminal."
    exit 1
fi

# Cleanup
if [ -n "$OLLAMA_PID" ]; then
    kill -9 $OLLAMA_PID 2>/dev/null
fi
echo "Goodbye!"
