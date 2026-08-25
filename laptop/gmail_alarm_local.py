#!/usr/bin/env python3
"""
Gmail Alarm, local edition.

Checks Gmail directly from this laptop over IMAP and rings when a matching
email arrives. No Apps Script, no OAuth consent screen, no relay in the middle —
useful when the Google side can't be set up, and simpler anyway when the laptop
has to be awake to make noise regardless.

    python3 gmail_alarm_local.py --setup     # ask for credentials and rules
    python3 gmail_alarm_local.py             # start watching
    python3 gmail_alarm_local.py --once      # check once and exit, for testing

Needs alarm_listener.py in the same folder — the siren lives there.

Credentials: Gmail no longer accepts your normal password over IMAP. You need an
App Password from https://myaccount.google.com/apppasswords (requires 2-Step
Verification on the account). That password is stored in a file on this machine,
so treat it like a house key: it grants read access to your mail. Revoke it from
that same page at any time.
"""

import argparse
import email
import email.header
import getpass
import imaplib
import json
import os
import stat
import sys
import threading
import time

try:
    from alarm_listener import (Alarm, build_alarm_wav, find_player,
                                force_max_volume, prevent_sleep)
except ImportError:
    sys.exit("alarm_listener.py must sit in the same folder as this script.\n"
             "Download it from the repo's laptop/ folder and try again.")

IMAP_HOST = "imap.gmail.com"
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".gmail-alarm", "local-config.json")
POLL_SECONDS = 60
ALARM_SECONDS = 45


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def load_config():
    if not os.path.exists(CONFIG_PATH):
        sys.exit("No config yet. Run:  python3 gmail_alarm_local.py --setup")
    with open(CONFIG_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def save_config(config):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2)
    try:
        os.chmod(CONFIG_PATH, stat.S_IRUSR | stat.S_IWUSR)   # 0600, POSIX only
    except Exception:
        pass


def setup_wizard():
    print("Gmail Alarm — local setup\n")
    print("You need an App Password, not your normal Gmail password:")
    print("  https://myaccount.google.com/apppasswords")
    print("(Requires 2-Step Verification. Google stopped accepting normal")
    print("passwords over IMAP in 2022, so there's no way around this one.)\n")

    address = input("Your Gmail address: ").strip()
    password = getpass.getpass("App Password (hidden; spaces are fine): ")
    password = password.replace(" ", "")

    print("\nWhat should wake you? Leave either blank to ignore it.")
    senders = input("Sender(s), comma separated: ").strip()
    subjects = input("Word(s) in the subject, comma separated: ").strip()

    senders = [s.strip() for s in senders.split(",") if s.strip()]
    subjects = [s.strip() for s in subjects.split(",") if s.strip()]

    if not senders and not subjects:
        sys.exit("\nAt least one of the two is required — with both empty, "
                 "every unread email would ring the alarm.")

    require_both = True
    if senders and subjects:
        answer = input("Must BOTH match? [Y/n]: ").strip().lower()
        require_both = not answer.startswith("n")

    config = {
        "email": address,
        "app_password": password,
        "senders": senders,
        "subjectContains": subjects,
        "requireBoth": require_both,
    }
    save_config(config)

    print("\nSaved to " + CONFIG_PATH)
    if os.name == "nt":
        print("It sits in your user profile; anyone with your Windows login can read it.")
    print("\nChecking the credentials work...")
    try:
        connection = connect(config)
        connection.logout()
        print("Login OK.\n\nStart watching with:  python3 gmail_alarm_local.py")
    except Exception as err:
        print("Login FAILED: {}".format(err))
        print("\nThe usual causes, in order of likelihood:")
        print("  - the App Password was mistyped (it's 16 characters)")
        print("  - you used your normal password instead of an App Password")
        print("  - IMAP is switched off: Gmail > Settings > Forwarding and POP/IMAP")


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------
def decode_header_value(raw):
    """Turns '=?UTF-8?B?...?=' and friends into ordinary text."""
    if not raw:
        return ""
    parts = []
    for chunk, encoding in email.header.decode_header(raw):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(encoding or "utf-8", errors="replace"))
        else:
            parts.append(chunk)
    return "".join(parts)


def matches(from_header, subject_header, config):
    """
    Same two-stage approach as the Apps Script: IMAP does a rough search
    server-side, and this re-checks the raw headers, because IMAP SEARCH is
    substring-ish and case-folded in ways that vary between servers.
    """
    sender = (from_header or "").lower()
    subject = (subject_header or "").lower()

    senders = config.get("senders") or []
    subjects = config.get("subjectContains") or []

    sender_ok = not senders or any(s.lower() in sender for s in senders)
    subject_ok = not subjects or any(s.lower() in subject for s in subjects)

    if not senders or not subjects:
        return sender_ok and subject_ok
    return (sender_ok and subject_ok) if config.get("requireBoth", True) else (sender_ok or subject_ok)


def build_search(config):
    """
    IMAP SEARCH can't OR across many terms without deep nesting, so this asks
    only for UNSEEN and filters locally. An inbox's unread count is small enough
    that the difference doesn't matter, and it keeps the matching in one place.
    """
    return "UNSEEN"


# ---------------------------------------------------------------------------
# IMAP
# ---------------------------------------------------------------------------
def connect(config):
    connection = imaplib.IMAP4_SSL(IMAP_HOST, 993)
    connection.login(config["email"], config["app_password"])
    return connection


def fetch_unread(connection, config):
    """Returns [(uid, from, subject)] for unread mail, without marking it read."""
    connection.select("INBOX", readonly=True)
    status, data = connection.search(None, build_search(config))
    if status != "OK" or not data or not data[0]:
        return []

    results = []
    for num in data[0].split():
        # BODY.PEEK, not BODY: fetching with BODY would set the \Seen flag and
        # silently acknowledge the alarm we're about to raise.
        status, payload = connection.fetch(
            num, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT MESSAGE-ID)])")
        if status != "OK" or not payload or not isinstance(payload[0], tuple):
            continue
        headers = email.message_from_bytes(payload[0][1])
        message_id = headers.get("Message-ID") or num.decode()
        results.append((
            message_id,
            decode_header_value(headers.get("From")),
            decode_header_value(headers.get("Subject")),
        ))
    return results


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def watch_for_enter(alarm):
    for _ in sys.stdin:
        alarm.silence.set()
        print("  (silenced — still watching)")


def run(config, once=False):
    wav_path = build_alarm_wav(os.path.join(os.path.expanduser("~"), ".gmail-alarm", "siren.wav"))
    player = find_player()
    if not player:
        print("WARNING: no audio player found — run alarm_listener.py --diagnose")

    alarm = Alarm(wav_path, player, ALARM_SECONDS)
    threading.Thread(target=watch_for_enter, args=(alarm,), daemon=True).start()
    if prevent_sleep():
        print("Idle sleep suppressed while this is running. Keep the lid open.")

    seen = set()
    active = None          # message id currently ringing
    backoff = 5

    while True:
        try:
            connection = connect(config)
            print("Watching {} — checking every {}s.".format(config["email"], POLL_SECONDS))
            backoff = 5

            while True:
                unread = fetch_unread(connection, config)
                unread_ids = {mid for mid, _, _ in unread}

                # Opening the email clears UNSEEN, which is the acknowledgement.
                if active and active not in unread_ids:
                    print("Acknowledged — alarm off.")
                    active = None
                    alarm.silence.set()

                for message_id, sender, subject in unread:
                    if message_id in seen or not matches(sender, subject, config):
                        continue
                    seen.add(message_id)
                    active = message_id
                    print("\n*** {} — {}  [{}]".format(sender, subject, time.strftime("%H:%M:%S")))
                    print("    Open the email to stop it. Enter silences this round.")
                    alarm.ring()

                if active:
                    alarm.ring()      # still unread: keep it ringing

                if once:
                    print("Checked {} unread message(s). Exiting (--once).".format(len(unread)))
                    connection.logout()
                    return

                time.sleep(POLL_SECONDS)

        except KeyboardInterrupt:
            print("\nStopped watching.")
            return
        except Exception as err:
            print("Connection problem ({}). Retrying in {}s.".format(err, backoff))
            if once:
                return
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)


def main():
    parser = argparse.ArgumentParser(description="Ring this laptop when a matching Gmail arrives.")
    parser.add_argument("--setup", action="store_true", help="ask for credentials and rules, then exit")
    parser.add_argument("--once", action="store_true", help="check once and exit")
    parser.add_argument("--show", action="store_true", help="print the current rules (not the password)")
    args = parser.parse_args()

    if args.setup:
        setup_wizard()
        return

    config = load_config()

    if args.show:
        redacted = dict(config, app_password="(set)" if config.get("app_password") else "(missing)")
        print(json.dumps(redacted, indent=2))
        return

    run(config, once=args.once)


if __name__ == "__main__":
    main()
