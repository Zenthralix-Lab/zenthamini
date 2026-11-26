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
#  ZenthaMini - Fast Web Chat (Linux)
# ===================================================

echo "==================================================="
echo "    ZenthaMini - Fast Web Chat Mode (Linux)"
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

# ---- ZenthaTech engine switcher (multi-OS): aktifkan runtime Linux ----
# Shared/lib/ollama dipakai bersama Linux & Mac. Bila isinya engine macOS
# (Mach-O), ganti dengan engine Linux dari Shared/.engines/linux.
ZT_LIB="$SHARED_DIR/lib/ollama"
if [ ! -e "$ZT_LIB/llama-server" ] || ! file "$ZT_LIB/llama-server" 2>/dev/null | grep -q "ELF"; then
    if [ -d "$SHARED_DIR/.engines/linux" ]; then
        if [ -e "$ZT_LIB" ] && [ ! -L "$ZT_LIB" ]; then
            rm -rf "$ZT_LIB"
        elif [ -L "$ZT_LIB" ]; then
            rm -f "$ZT_LIB"
        fi
        ln -s "../.engines/linux" "$ZT_LIB" && echo "[switch] Engine Linux diaktifkan (symlink)."
    else
        echo "[switch] Engine Linux belum ada. Jalankan: bash Linux/install.sh"
    fi
fi
# ---------------------------------------------------------------------

# Check if the portable Linux engine is downloaded
if [ ! -x "$SHARED_DIR/bin/ollama-linux" ] || [ ! -f "$SHARED_DIR/lib/ollama/llama-server" ]; then
    echo "==================================================="
    echo "  ERROR: Linux AI Engine Not Found or Incomplete!"
    echo "==================================================="
    echo ""
    echo "  It looks like the AI engine hasn't been fully set up yet."
    echo "  Please run 'bash install.sh' in this Linux"
    echo "  folder first to download the full Ollama runtime."
    echo ""
    read -n 1 -s -r -p "Press any key to continue..."
    echo ""
    exit 1
fi

# Check if Ollama is already running
if curl -s http://127.0.0.1:11434/api/tags > /dev/null 2>&1; then
    echo "[OK] Ollama engine is already running!"
else
    echo "Starting offline Linux AI Engine..."
    HOME="$OLLAMA_RUNTIME" "$SHARED_DIR/bin/ollama-linux" serve &
    OLLAMA_PID=$!
    
    echo "Waiting for engine to initialize..."
    for i in $(seq 1 60); do
        if curl -s http://127.0.0.1:11434/api/tags | grep -q '"models"'; then
            echo "[OK] Engine is online!"
            break
        fi
        sleep 1
    done
    if ! curl -s http://127.0.0.1:11434/api/tags | grep -q '"models"'; then
        echo "ERROR: Engine did not become ready in 60 seconds."
        if [ -n "$OLLAMA_PID" ]; then
            kill "$OLLAMA_PID" 2>/dev/null
        fi
        exit 1
    fi
fi

echo ""
echo "==================================================="
echo "  AI ENGINE IS RUNNING"
echo "  Chat UI will open automatically."
echo "  Press Ctrl+C to shut down."
echo "==================================================="
echo ""


# Launch Python chat server using system Python
if command -v python3 &> /dev/null; then
    python3 "$SHARED_DIR/chat_server.py"
elif command -v python &> /dev/null; then
    python "$SHARED_DIR/chat_server.py"
else
    echo "ERROR: Python not found. Please install python3 via your package manager."
    exit 1
fi

# Cleanup
if [ -n "$OLLAMA_PID" ]; then
    kill -9 $OLLAMA_PID 2>/dev/null
fi
echo "Goodbye!"
