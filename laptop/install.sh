#!/usr/bin/env bash
#
# One-command setup for the laptop half of Gmail Alarm (macOS and Linux).
#
#   curl -fsSL https://raw.githubusercontent.com/jrhosting067-stack/Yuval-script/HEAD/laptop/install.sh | bash
#
# Downloads the listener, invents a random topic if you don't supply one, plays
# a test siren, and optionally installs an autostart service that keeps the
# machine awake. Prints the one line you need to paste into Apps Script.
#
# Usage: install.sh [topic] [--autostart|--no-autostart]

set -euo pipefail

RAW_URL="https://raw.githubusercontent.com/jrhosting067-stack/Yuval-script/HEAD/laptop/alarm_listener.py"
INSTALL_DIR="$HOME/.gmail-alarm"
TOPIC=""
AUTOSTART="ask"

for arg in "$@"; do
  case "$arg" in
    --autostart)    AUTOSTART="yes" ;;
    --no-autostart) AUTOSTART="no" ;;
    -*)             echo "Unknown option: $arg" >&2; exit 2 ;;
    *)              TOPIC="$arg" ;;
  esac
done

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

# --- Python ----------------------------------------------------------------
PYTHON=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,7) else 1)' 2>/dev/null; then
    PYTHON="$candidate"
    break
  fi
done

if [ -z "$PYTHON" ]; then
  echo "Python 3.7+ is required but wasn't found." >&2
  case "$(uname -s)" in
    Darwin) echo "Install it by running: xcode-select --install" >&2 ;;
    *)      echo "Install it with your package manager, e.g. sudo apt install python3" >&2 ;;
  esac
  exit 1
fi

# --- Topic -----------------------------------------------------------------
# The topic is the only secret in the system, so a generated one beats whatever
# a human would type. Public ntfy topics are unauthenticated: anyone who knows
# or guesses the string can ring this laptop.
if [ -z "$TOPIC" ]; then
  if command -v openssl >/dev/null 2>&1; then
    TOPIC="gmail-alarm-$(openssl rand -hex 6)"
  else
    TOPIC="gmail-alarm-$($PYTHON -c 'import secrets; print(secrets.token_hex(6))')"
  fi
  GENERATED=1
fi

# --- Download --------------------------------------------------------------
say "Installing to $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
if ! curl -fsSL "$RAW_URL" -o "$INSTALL_DIR/alarm_listener.py"; then
  echo "Download failed. Check your connection and try again." >&2
  exit 1
fi
chmod +x "$INSTALL_DIR/alarm_listener.py"
printf '%s\n' "$TOPIC" > "$INSTALL_DIR/topic"
chmod 600 "$INSTALL_DIR/topic"

# --- Test siren ------------------------------------------------------------
say "Testing the siren — turn the volume up. Press Enter to silence it."
"$PYTHON" "$INSTALL_DIR/alarm_listener.py" "$TOPIC" --test --seconds 6 || true

# --- Autostart -------------------------------------------------------------
if [ "$AUTOSTART" = "ask" ]; then
  # Test /dev/tty rather than stdin: the documented install path pipes this
  # script into bash, so stdin is the pipe even though a terminal is right there.
  if [ -r /dev/tty ]; then
    printf '\nStart listening automatically at login, and keep this machine awake? [Y/n] ' >/dev/tty
    read -r reply </dev/tty || reply=""
    case "$reply" in [Nn]*) AUTOSTART="no" ;; *) AUTOSTART="yes" ;; esac
  else
    # Genuinely non-interactive; don't install services behind the user's back.
    AUTOSTART="no"
  fi
fi

RUN_CMD="$PYTHON $INSTALL_DIR/alarm_listener.py $TOPIC"

if [ "$AUTOSTART" = "yes" ]; then
  case "$(uname -s)" in
    Darwin)
      PLIST="$HOME/Library/LaunchAgents/com.gmail-alarm.listener.plist"
      mkdir -p "$(dirname "$PLIST")"
      cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.gmail-alarm.listener</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/caffeinate</string>
    <string>-s</string>
    <string>$PYTHON</string>
    <string>$INSTALL_DIR/alarm_listener.py</string>
    <string>$TOPIC</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardErrorPath</key><string>$INSTALL_DIR/listener.log</string>
  <key>StandardOutPath</key><string>$INSTALL_DIR/listener.log</string>
</dict>
</plist>
PLIST_EOF
      launchctl unload "$PLIST" 2>/dev/null || true
      launchctl load "$PLIST"
      say "Autostart installed. Stop it with: launchctl unload $PLIST"
      ;;
    Linux)
      UNIT="$HOME/.config/systemd/user/gmail-alarm.service"
      mkdir -p "$(dirname "$UNIT")"
      cat > "$UNIT" <<UNIT_EOF
[Unit]
Description=Gmail Alarm listener
After=network-online.target

[Service]
ExecStart=/usr/bin/env systemd-inhibit --what=sleep --why="Gmail Alarm" $RUN_CMD
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
UNIT_EOF
      systemctl --user daemon-reload
      systemctl --user enable --now gmail-alarm.service
      say "Autostart installed. Stop it with: systemctl --user disable --now gmail-alarm.service"
      ;;
    *)
      echo "Autostart isn't supported on $(uname -s); start it by hand instead." >&2
      AUTOSTART="no"
      ;;
  esac
fi

# --- What's left -----------------------------------------------------------
say "Laptop side done."
if [ "${GENERATED:-0}" = "1" ]; then
  echo "Your topic (generated, keep it private):"
else
  echo "Your topic:"
fi
echo
echo "    $TOPIC"
echo
echo "Now do the Gmail side at https://script.google.com:"
echo "  1. New project, paste in apps-script/Code.gs from the repo."
echo "  2. Set senders / subjectContains to what should wake you, and set:"
echo
echo "         topic: '$TOPIC'"
echo
echo "  3. Run setup(), approve the permission prompt, then run testAlarm()."
echo

if [ "$AUTOSTART" != "yes" ]; then
  echo "Start listening (leave it running):"
  echo
  case "$(uname -s)" in
    Darwin) echo "    caffeinate -s $RUN_CMD" ;;
    Linux)  echo "    systemd-inhibit --what=sleep $RUN_CMD" ;;
    *)      echo "    $RUN_CMD" ;;
  esac
  echo
fi
