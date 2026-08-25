#!/usr/bin/env python3
"""
Gmail Alarm, notification edition (Windows).

Watches the Windows notification centre and rings when a mail notification
mentions a sender or subject you care about. Touches no Google account, needs no
password, no App Password, and no OAuth consent screen — it only reads
notifications that have already appeared on this machine.

    py -m pip install winsdk               # one-time
    py notification_watcher.py --list      # see what your notifications look like
    py notification_watcher.py --from datadog --subject URGENT

Needs alarm_listener.py in the same folder — the siren lives there.

Two things must be true or nothing will ever arrive:

  1. Windows must allow it: Settings > Privacy & security > Notifications >
     "Let apps access your notifications" turned on.
  2. Something must actually be raising mail notifications. Gmail in a browser
     only does this while a Gmail tab is open. A desktop client (Thunderbird,
     Outlook) does it whether or not a window is visible, which is the more
     reliable arrangement for an alarm.

Run --list first and read what your machine actually produces. Notification
wording differs between Chrome, Thunderbird and Outlook, and --list is how you
find out what to match on.
"""

import argparse
import os
import sys
import time

try:
    from alarm_listener import (Alarm, build_alarm_wav, find_player,
                                force_max_volume, prevent_sleep)
except ImportError:
    sys.exit("alarm_listener.py must sit in the same folder as this script.")

POLL_SECONDS = 2
ALARM_SECONDS = 45

# Notifications from these apps are considered mail. Substring, case-insensitive.
MAIL_APPS = ("chrome", "mail", "thunderbird", "outlook", "edge", "firefox")


# ---------------------------------------------------------------------------
# Matching — pure, so it can be tested without Windows
# ---------------------------------------------------------------------------
def looks_like_mail(app_name, mail_apps=MAIL_APPS):
    return any(hint in (app_name or "").lower() for hint in mail_apps)


def match_notification(app_name, title, body, senders, subjects, require_both=True,
                       any_app=False):
    """
    Notification text is short and inconsistently structured — Chrome puts the
    sender in the title and the subject in the body, Thunderbird sometimes
    reverses them — so both fields are searched for both kinds of term rather
    than assuming a layout.
    """
    if not any_app and not looks_like_mail(app_name):
        return False

    haystack = "{} {}".format(title or "", body or "").lower()

    sender_ok = not senders or any(s.lower() in haystack for s in senders)
    subject_ok = not subjects or any(s.lower() in haystack for s in subjects)

    if not senders or not subjects:
        return sender_ok and subject_ok
    return (sender_ok and subject_ok) if require_both else (sender_ok or subject_ok)


# ---------------------------------------------------------------------------
# Windows notification listener
# ---------------------------------------------------------------------------
def get_listener():
    """Returns the WinRT listener, or exits with something actionable."""
    if os.name != "nt":
        sys.exit("This one is Windows-only. On macOS or Linux use "
                 "gmail_alarm_local.py instead.")
    try:
        from winsdk.windows.ui.notifications.management import (
            UserNotificationListener, UserNotificationListenerAccessStatus)
        from winsdk.windows.ui.notifications import NotificationKinds
    except ImportError:
        sys.exit("The winsdk package is missing. Install it with:\n"
                 "    py -m pip install winsdk")

    listener = UserNotificationListener.current
    status = listener.request_access_sync()
    if status != UserNotificationListenerAccessStatus.ALLOWED:
        sys.exit("Windows denied access to notifications.\n"
                 "Turn it on: Settings > Privacy & security > Notifications >\n"
                 '"Let apps access your notifications".')
    return listener, NotificationKinds


def read_notification(notification):
    """Flattens one WinRT notification into (app_name, title, body)."""
    app_name = ""
    try:
        app_name = notification.app_info.display_info.display_name or ""
    except Exception:
        pass

    lines = []
    try:
        from winsdk.windows.ui.notifications import KnownNotificationBindings

        binding = notification.notification.visual.get_binding(
            KnownNotificationBindings.get_toast_generic())
        if binding:
            lines = [element.text for element in binding.get_text_elements()]
    except Exception:
        pass

    title = lines[0] if lines else ""
    body = " ".join(lines[1:]) if len(lines) > 1 else ""
    return app_name, title, body


def watch(args):
    listener, notification_kinds = get_listener()

    wav_path = build_alarm_wav(os.path.join(os.path.expanduser("~"), ".gmail-alarm", "siren.wav"))
    player = find_player()
    if not player:
        print("WARNING: no audio player found — run alarm_listener.py --diagnose")

    alarm = Alarm(wav_path, player, ALARM_SECONDS)
    if prevent_sleep():
        print("Idle sleep suppressed while this is running. Keep the lid open.")

    if args.list:
        print("Listing every notification as it arrives. Ctrl+C to stop.\n"
              "Send yourself a test email and see what shows up here.\n")
    else:
        print("Watching notifications. Ctrl+C to stop.")
        print("  senders : {}".format(args.sender or "(any)"))
        print("  subjects: {}".format(args.subject or "(any)"))

    seen = set()

    while True:
        try:
            for notification in listener.get_notifications(notification_kinds.TOAST):
                key = notification.id
                if key in seen:
                    continue
                seen.add(key)

                app_name, title, body = read_notification(notification)

                if args.list:
                    print("[{}] app={!r}\n      title={!r}\n      body={!r}".format(
                        time.strftime("%H:%M:%S"), app_name, title, body))
                    continue

                if match_notification(app_name, title, body, args.sender, args.subject,
                                      require_both=not args.either, any_app=args.any_app):
                    print("\n*** {} — {} | {}  [{}]".format(
                        app_name, title, body, time.strftime("%H:%M:%S")))
                    print("    Enter silences it.")
                    alarm.ring()

            # Ids are only unique while the notification is live; prune so the
            # set can't grow without bound over days of running.
            if len(seen) > 500:
                seen = set(list(seen)[-200:])

            time.sleep(POLL_SECONDS)
        except KeyboardInterrupt:
            print("\nStopped watching.")
            return


def main():
    parser = argparse.ArgumentParser(
        description="Ring this laptop when a mail notification matches.")
    parser.add_argument("--from", dest="sender", action="append", default=[],
                        metavar="TEXT", help="text to look for (repeatable)")
    parser.add_argument("--subject", action="append", default=[], metavar="TEXT",
                        help="subject text to look for (repeatable)")
    parser.add_argument("--either", action="store_true",
                        help="match sender OR subject (default: both must match)")
    parser.add_argument("--any-app", dest="any_app", action="store_true",
                        help="match notifications from any app, not just mail apps")
    parser.add_argument("--list", action="store_true",
                        help="print every notification instead of matching, to see their shape")
    args = parser.parse_args()

    if not args.list and not args.sender and not args.subject:
        parser.error("give --from and/or --subject, or use --list to see what arrives")

    watch(args)


if __name__ == "__main__":
    main()
