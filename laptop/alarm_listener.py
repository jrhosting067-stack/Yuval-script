#!/usr/bin/env python3
"""
Turns this laptop into the alarm bell for the Gmail Alarm script.

The Apps Script side watches Gmail and publishes to an ntfy topic. This script
sits on the laptop, holds an open connection to that topic, and plays a loud
siren through the speakers whenever a message lands — including the every-minute
escalation repeats, which stop as soon as you open the email.

Python 3.7+, standard library only. No pip install, no account, no credentials
stored on this machine.

    python3 alarm_listener.py your-topic-here

Press Enter to silence the current siren. Ctrl+C to quit listening entirely.
"""

import argparse
import array
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import wave

DEFAULT_SERVER = "https://ntfy.sh"
ALARM_SECONDS = 45      # how long one siren burst rings before giving up
READ_TIMEOUT = 90       # ntfy sends keepalives every ~45s; silence means dead socket

SYSTEM = platform.system()


# ---------------------------------------------------------------------------
# The siren
# ---------------------------------------------------------------------------
def build_alarm_wav(path, seconds=2.0, rate=44100):
    """
    Writes a two-tone square-wave siren.

    Square rather than sine, and alternating pitch, because system notification
    sounds are designed to be ignorable and this one shouldn't be.
    """
    frames = array.array("h")
    for i in range(int(rate * seconds)):
        t = i / rate
        freq = 880.0 if int(t * 4) % 2 == 0 else 660.0
        value = 1.0 if math.sin(2 * math.pi * freq * t) >= 0 else -1.0
        # Fade each 250ms slot in and out so the tone changes don't click.
        slot = (t * 4) % 1.0
        envelope = min(1.0, slot * 20, (1.0 - slot) * 20)
        frames.append(int(0.85 * 32767 * value * envelope))

    with wave.open(path, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(frames.tobytes())
    return path


def find_player():
    """Returns a callable that plays a wav file once, blocking, or None."""
    if SYSTEM == "Windows":
        import winsound

        def play_windows(path):
            winsound.PlaySound(path, winsound.SND_FILENAME)

        return play_windows

    if SYSTEM == "Darwin" and shutil.which("afplay"):
        return lambda path: subprocess.run(["afplay", path], check=False)

    for command in (["paplay"], ["aplay", "-q"], ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]):
        if shutil.which(command[0]):
            return lambda path, c=command: subprocess.run(c + [path], check=False)

    return None


def prevent_sleep():
    """
    Ask Windows to stay awake while we're listening.

    macOS and Linux have caffeinate and systemd-inhibit to wrap the process in;
    Windows has no equivalent command, so the process asks the OS directly.
    Needs no admin rights. Note this blocks *idle* sleep only — closing the lid
    or choosing Sleep from the menu still sleeps the machine, and a sleeping
    laptop cannot ring.
    """
    if SYSTEM != "Windows":
        return False
    try:
        import ctypes

        ES_CONTINUOUS = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        return ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED) != 0
    except Exception:
        return False


def force_max_volume():
    """Best effort — a muted laptop is the most common reason an alarm fails."""
    try:
        if SYSTEM == "Darwin":
            subprocess.run(["osascript", "-e", "set volume output volume 100"], check=False)
        elif SYSTEM == "Linux":
            if shutil.which("pactl"):
                subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "100%"], check=False)
                subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"], check=False)
            elif shutil.which("amixer"):
                subprocess.run(["amixer", "-q", "sset", "Master", "100%", "unmute"], check=False)
        # Windows has no built-in CLI for this; see the README.
    except Exception:
        pass


class Alarm:
    def __init__(self, wav_path, player, seconds=ALARM_SECONDS):
        self.wav_path = wav_path
        self.player = player
        self.seconds = seconds
        self.silence = threading.Event()
        self._thread = None
        self._deadline = 0.0

    def ring(self):
        """Starts a siren burst, or extends the one already running."""
        self.silence.clear()
        # Push the deadline out rather than returning early when a burst is
        # already in flight: an escalation arriving just as the previous burst
        # expires must not be swallowed by the dying thread.
        self._deadline = time.time() + self.seconds
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        force_max_volume()
        while time.time() < self._deadline and not self.silence.is_set():
            if self.player:
                self.player(self.wav_path)
            else:
                # No audio player found — fall back to the terminal bell.
                sys.stdout.write("\a")
                sys.stdout.flush()
                time.sleep(1)


def watch_for_enter(alarm):
    """Enter silences the current burst. Runs forever in a daemon thread."""
    for _ in sys.stdin:
        alarm.silence.set()
        print("  (silenced — still listening)")


# ---------------------------------------------------------------------------
# The ntfy subscription
# ---------------------------------------------------------------------------
class SeenIds:
    """Remembers the last N message ids so a replay never rings twice."""

    def __init__(self, limit=200):
        self.limit = limit
        self.order = []
        self.members = set()

    def add_if_new(self, message_id):
        if not message_id:
            return True                 # no id to dedupe on; let it through
        if message_id in self.members:
            return False
        self.order.append(message_id)
        self.members.add(message_id)
        if len(self.order) > self.limit:
            self.members.discard(self.order.pop(0))
        return True


def stream(server, topic, on_message):
    """
    Holds an open connection to ntfy's newline-delimited JSON stream and
    reconnects with backoff whenever it drops (laptop sleep, wifi change,
    server blip).

    On reconnect it asks for the messages published during the outage, since
    those are exactly the alarms you'd otherwise sleep through. ntfy may resend
    one you already have, so ids are deduped rather than trusting the replay
    window to be exact.
    """
    base = "{}/{}/json".format(server.rstrip("/"), topic)
    seen = SeenIds()
    backoff = 1
    disconnected_at = None

    while True:
        url = base
        if disconnected_at:
            gap = min(int(time.time() - disconnected_at) + 10, 300)
            url = "{}?since={}s".format(base, gap)

        try:
            request = urllib.request.Request(url, headers={"User-Agent": "gmail-alarm-listener"})
            with urllib.request.urlopen(request, timeout=READ_TIMEOUT) as response:
                print("Listening on {} — waiting for alarms.".format(url))
                backoff = 1
                disconnected_at = None
                for line in response:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line.decode("utf-8"))
                    except ValueError:
                        continue
                    if event.get("event") != "message":
                        continue
                    if seen.add_if_new(event.get("id")):
                        on_message(event)
        except KeyboardInterrupt:
            raise
        except Exception as err:
            print("Connection lost ({}). Reconnecting in {}s.".format(err, backoff))

        if disconnected_at is None:
            disconnected_at = time.time()
        time.sleep(backoff)
        backoff = min(backoff * 2, 60)


def main():
    parser = argparse.ArgumentParser(description="Ring this laptop when the Gmail alarm fires.")
    parser.add_argument("topic", nargs="?", default=os.environ.get("NTFY_TOPIC"),
                        help="ntfy topic (or set NTFY_TOPIC)")
    parser.add_argument("--server", default=os.environ.get("NTFY_SERVER", DEFAULT_SERVER))
    parser.add_argument("--seconds", type=int, default=ALARM_SECONDS,
                        help="how long one siren burst rings (default %(default)s)")
    parser.add_argument("--test", action="store_true", help="ring once and exit")
    args = parser.parse_args()

    if not args.topic:
        parser.error("no topic given — pass it as an argument or set NTFY_TOPIC")

    wav_path = build_alarm_wav(os.path.join(tempfile.gettempdir(), "gmail-alarm.wav"))
    player = find_player()
    if not player:
        print("WARNING: no audio player found — falling back to the terminal bell, "
              "which will not wake you. See the README.")

    alarm = Alarm(wav_path, player, args.seconds)

    if prevent_sleep():
        print("Idle sleep suppressed while this is running. Keep the lid open.")

    if args.test:
        print("Ringing for {}s...".format(args.seconds))
        alarm.ring()
        alarm._thread.join()
        return

    threading.Thread(target=watch_for_enter, args=(alarm,), daemon=True).start()

    def on_message(event):
        title = event.get("title") or "ALARM"
        body = (event.get("message") or "").replace("\n", " | ")
        print("\n*** {} — {}  [{}]".format(title, body, time.strftime("%H:%M:%S")))
        print("    Enter to silence.")
        alarm.ring()

    try:
        stream(args.server, args.topic, on_message)
    except KeyboardInterrupt:
        print("\nStopped listening.")


if __name__ == "__main__":
    main()
