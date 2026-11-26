# ZenthaMini End-User Tutorial

## Welcome to ZenthaMini! 🧠✨

ZenthaMini is a **portable, private AI chat platform** that runs completely from a USB drive. No installation required, works on any computer, and your data never leaves your device.

---

## 📋 Table of Contents

1. [What is ZenthaMini?](#what-is-zenthamini)
2. [System Requirements](#system-requirements)
3. [Installation](#installation)
   - [Windows](#windows)
   - [macOS](#macos)
   - [Linux](#linux)
   - [Android](#android)
4. [First Run](#first-run)
5. [Using ZenthaMini](#using-zenthamini)
6. [Switching Models](#switching-models)
7. [Troubleshooting](#troubleshooting)
8. [Uninstallation](#uninstallation)

---

## 1. What is ZenthaMini?

ZenthaMini is a **zero-dependency, portable AI assistant** that:

- ✅ Runs directly from a USB drive (no admin rights needed)
- ✅ Works on Windows, macOS, Linux, and Android
- ✅ Keeps all your data private (nothing sent to the cloud)
- ✅ Supports multiple AI models (Gemma, Llama, Mistral, etc.)
- ✅ Has a beautiful web UI you access via your browser
- ✅ Can work offline after setup

**Think of it as: "AI in your pocket, on your terms."**

---

## 2. System Requirements

| Platform | RAM Minimum | Storage | USB Port |
|----------|-------------|---------|----------|
| Windows  | 4 GB        | 5 GB free | USB 3.0 recommended |
| macOS    | 4 GB        | 5 GB free | USB 3.0 recommended |
| Linux    | 2 GB        | 5 GB free | USB 3.0 recommended |
| Android  | 2 GB        | 5 GB free | OTG cable required |

**Important:** The first model you download will be ~4-5 GB. Make sure your USB drive has enough space!

---

## 3. Installation

### 🥇 Windows

1. **Download the one-click installer:**
   - Save `ZenthaMini_Windows.bat` to your USB drive root

2. **Run the installer:**
   - Double-click `ZenthaMini_Windows.bat`
   - If Windows warns about security, click "More info" → "Run anyway"

3. **Follow the prompts:**
   - Select model number `1` for Gemma 2B (recommended)
   - Wait for download and Ollama installation
   - Let it start the AI engine

4. **Start chatting:**
   - Once complete, a browser will open automatically
   - Or visit: `http://localhost:3333`

---

### 🍎 macOS

1. **Download the installer:**
   - Save `ZenthaMini_macOS.command` to your USB drive
   - Right-click → "Open" (may need to allow from anywhere in Security settings)

2. **Grant permissions:**
   - First run may ask to allow the script to run
   - Click "Open" if prompted

3. **Follow the prompts:**
   - Select model number `1` for Gemma 2B (recommended)
   - Wait for download and Ollama installation

4. **Start chatting:**
   - Safari/Chrome will open to `http://localhost:3333`
   - Or manually navigate there

---

### 🐧 Linux

1. **Make executable and run:**
   ```bash
   chmod +X ZenthaMini_Linux.sh
   ./ZenthaMini_Linux.sh
   ```

2. **Follow the prompts:**
   - Select model number `1` for Gemma 2B (recommended)
   - The installer handles everything automatically

3. **Start chatting:**
   - Browser opens to `http://localhost:3333`
   - Or: `xdg-open http://localhost:3333`

---

### 🤖 Android

**Requires:** OTG cable + Android phone/tablet with Termux

1. **Install Termux** from F-Droid (not Play Store):
   - Download: https://f-droid.org/en/packages/com.termux/

2. **Copy ZenthaMini to your device:**
   - Connect USB drive via OTG
   - Open Termux
   - `pkg install git -y`
   - `git clone https://github.com/your-repo/zenthamini.git` (or copy files manually)

3. **Run the Android installer:**
   ```bash
   cd zenthamini
   bash Android/install.sh
   ```
   - This compiles Llama.cpp natively (takes 10-30 min)
   - Select model `1` for Gemma 2B

4. **Start chatting:**
   - Run: `bash Android/start.sh`
   - The AI engine opens in your browser

---

## 4. First Run

After installation completes:

1. **Wait for the browser to open** (or open it manually)
2. **You'll see the ZenthaMini chat interface**
3. **Type your first message** and press Enter
4. **The AI will respond** using your selected model

**First-time setup may take 1-2 minutes** while the model loads into memory.

---

## 5. Using ZenthaMini

### Basic Chat

| Action | How |
|--------|-----|
| Send message | Type in the input box ↓ + press ✓ or Enter |
| New chat | Click "New Chat" button |
| Save chat | Automatic - your history is saved locally |
| Load previous | Recent chats appear in the sidebar |

### Settings

Click the **gear icon** ⚙️ to access:

- **Model selection** - switch between installed models
- **Memory** - adjust context length (tokens)
- **Temperature** - creativity level (0.0 = focused, 1.0 = creative)
- **Theme** - light/dark mode
- **Privacy** - view what data is stored locally

### Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl` + `/` | Open command palette |
| `Esc` | Focus back on input |
| `Ctrl` + `L` | Clear current chat |
| `Ctrl` + `Shift` + `P` | Toggle preview mode |

---

## 6. Switching Models

To use a different AI model:

1. Click the **model dropdown** in the sidebar
2. Select from your installed models
3. Wait a moment for the switch
4. Start chatting with the new model!

**Available models** (depending on what you installed):
- `Gemma 2B` - Fast, efficient, high quality
- `Llama 3 8B` - More powerful, needs more RAM
- `Mistral 7B` - Balanced performance
- *Custom models* - if you added your own

---

## 7. Troubleshooting

### Common Issues

| Problem | Solution |
|---------|--------|
| **Browser doesn't open** | Visit `http://localhost:3333` manually |
| "Connection refused" | Wait 10-15s after installation, ensure server is running |
| Model download stalls | Check USB has 5+ GB free space |
| Ollama not found | Re-run installer, ensure curl/wget works |
| "Model too large for RAM" | Choose a smaller model (3B instead of 7B) |
| Linux: "command not found" | Install dependencies: `sudo apt install curl tar` |
| macOS: "unable to execute" | Right-click → "Open" → "Open" again in dialog |
| Android: "Termux not found" | Install Termux from F-Droid first |

### If Something Goes Wrong

- **Check the log:** `cat Shared/chat_server.log`
- **Restart server:** Kill any running processes and re-run `bash start.sh` (or `.command`/`.bat`)
- **Re-download model:** Delete the model file in `Shared/models/` and re-run installer
- **Reset everything:** Run `bash uninstall.sh` (Android) or delete the USB contents and re-copy

---

## 8. Uninstallation

### Windows/macOS/Linux

 Simply **delete the ZenthaMini folder** from your USB drive.
- All data is stored on the USB, so removal is complete uninstallation
- No registry entries, no system folders affected

### Android

```bash
bash Android/uninstall.sh
```

This will:
- Remove compiled Llama.cpp engine
- Clear model files
- Clean up Termux data
- Return device to pre-install state

---

## 🎯 Quick Start Checklist

- [ ] USB drive formatted (exFAT, minimum 16GB)
- [ ] ZenthaMini files copied to USB root
- [ ] Run installer for your platform
- [ ] Select model `1` (Gemma 2B) on first run
- [ ] Wait for download + engine start (2-5 min)
- [ ] Open browser to `http://localhost:3333`
- [ ] Start chatting! 💬

---

## 📞 Need Help?

- **Check the logs:** `Shared/chat_server.log`
- **Run doctor:** `bash doctor.sh` (Linux/macOS) or double-click (Windows)
- **Visit:** https://zenthralix.vercel.app (documentation)
- **Community:** Search "ZenthaMini" on your favorite forum

---

**Happy chatting! Your AI, your privacy, your USB. 🚀**