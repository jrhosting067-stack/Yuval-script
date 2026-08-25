# Gmail Alarm

Wakes you up when a specific email arrives. A Gmail filter tags the mail, an
Apps Script trigger checks every minute, and your laptop sounds a siren —
repeatedly — until you open the email.

Everything runs on free tiers: Gmail, Apps Script triggers, and
[ntfy.sh](https://ntfy.sh) (open source, no account, no payment).

```
email arrives ──▶ Gmail filter ──▶ label: ALARM
                                      │
              every-minute trigger ──▶ Code.gs searches for unread matches
                                      │
                                      ├─▶ ntfy topic ──▶ alarm_listener.py ──▶ 🔔 laptop
                                      ├─▶ (optional) SMS via carrier gateway
                                      └─▶ (optional) Calendar popup
                                      │
                     you open the email ──▶ alarm stops
```

The work is split across two machines. Gmail matching and the every-minute
escalation happen on Google's servers, so they keep working no matter what your
laptop is doing. The laptop runs one small listener whose only job is to make
noise. The only thing shared between them is the ntfy topic string — no Gmail
credentials ever touch the laptop, which also means the Apps Script half can be
set up from any browser, anywhere.

## Files

| Path | What it is |
| --- | --- |
| `tools/setup.html` | Fill in two boxes, get the finished `Code.gs` to paste into Apps Script. Open it locally or publish it. |
| `laptop/install.sh` | One-command setup for the laptop half (macOS and Linux). |
| `laptop/install.ps1` | The same, for Windows. |
| `laptop/alarm_listener.py` | Runs on the laptop that rings. Standard library Python, nothing to install. |
| `laptop/gmail_alarm_local.py` | Alternative: checks Gmail over IMAP from the laptop, with no Google Apps Script at all. |
| `laptop/notification_watcher.py` | Alternative: watches Windows notifications, so it needs no mail credentials whatsoever. |
| `apps-script/Code.gs` | The Gmail-side script. All configuration lives in the `CONFIG` block at the top. |
| `apps-script/appsscript.json` | Manifest — timezone and OAuth scopes. |
| `gmail/filters.xml` | Importable Gmail filter that applies the `ALARM` label. |

## Setup (about 10 minutes)

### 1. Laptop: prove it can make noise

First pick a topic name, long and random — `yuval-alarm-8f2b91c4d7`, not
`alarm`. Topics on the public ntfy server are unauthenticated, so anyone who
guesses the name can ring your laptop. This string is the only secret in the
system, and both halves of the setup need it.

Then, on the laptop that should ring — one command does the whole laptop half
(downloads the listener, plays a test siren, optionally sets up autostart, and
prints the topic to paste into Apps Script). It generates a random topic for
you, so you can skip picking one.

**Windows** (PowerShell):

```powershell
irm https://raw.githubusercontent.com/jrhosting067-stack/Yuval-script/HEAD/laptop/install.ps1 | iex
```

**macOS / Linux:**

```bash
curl -fsSL https://raw.githubusercontent.com/jrhosting067-stack/Yuval-script/HEAD/laptop/install.sh | bash
```

To do it by hand instead:

```bash
python3 alarm_listener.py your-topic-here --test   # siren now, then exit
python3 alarm_listener.py your-topic-here          # start listening
```

Press **Enter** to silence a siren, Ctrl+C to stop listening.

Do the `--test` run before anything else, at the volume you'd sleep through. If
that doesn't wake you, nothing downstream will. Then leave the listener running
and read [Keeping the laptop awake](#keeping-the-laptop-awake) — that's the part
that actually decides whether this works at 3am.

Python 3.7+ is all it needs. Linux has it; macOS prompts to install Command Line
Tools the first time you run `python3`; Windows usually doesn't have it, so
`winget install Python.Python.3.12` and then use `py` instead of `python3`.

### 2. Gmail: import the filter

1. Edit `gmail/filters.xml` — replace `alerts@example.com` and `URGENT` with
   your real sender and subject text.
2. Gmail → **Settings** → **Filters and Blocked Addresses** → **Import
   filters** → choose the file → **Open file** → **Create filters**.

The filter labels, stars, marks important, and never sends to spam. It
deliberately does **not** mark as read or archive — the script uses "still
unread" as the signal that you haven't seen it yet.

This step is optional: `Code.gs` matches on sender and subject by itself, so the
filter is an optimization and a place to eyeball what fired. Skip it if you're
setting up from a machine where downloading and editing a file is a nuisance.

### 3. Apps Script: install the script

Browser work — do it from any machine, not necessarily the one that rings.

Open `tools/setup.html` in a browser to skip the editing: type the sender and
subject, and it assembles the finished `Code.gs` with your topic already in
place, ready to copy in one click. The steps below are the same thing by hand.

1. Go to [script.google.com](https://script.google.com) → **New project**.
2. Paste `apps-script/Code.gs` over the default `Code.gs`.
3. Project Settings → tick **Show "appsscript.json" manifest file**, then paste
   `apps-script/appsscript.json` over it. Set `timeZone` to yours.
4. Edit the `CONFIG` block:
   - `senders` / `subjectContains` — what to watch for.
   - `requireBoth: true` means sender AND subject; `false` means either one.
   - `ntfy.topic` — the topic string from step 1.
5. Select **`setup`** in the function dropdown → **Run**. Approve the OAuth
   prompt (it's your own script, so Google shows the "unverified app" warning —
   *Advanced* → *Go to project (unsafe)*).
6. Select **`testAlarm`** → **Run**. Your laptop should ring.

That's it. The trigger is now running every minute.

## Doing it without Apps Script

If the Google side can't be set up — the OAuth consent screen won't complete, or
the account can't be verified — the laptop can check Gmail itself over IMAP.
That drops Apps Script, the consent screen, and the ntfy relay: one script, one
machine.

```bash
python3 gmail_alarm_local.py --setup   # credentials and rules
python3 gmail_alarm_local.py           # start watching
python3 gmail_alarm_local.py --once    # check once and exit
```

It needs `alarm_listener.py` beside it (the siren lives there) and an
[App Password](https://myaccount.google.com/apppasswords), which requires
2-Step Verification on the account — Gmail stopped accepting ordinary passwords
over IMAP in 2022, so there is no way around that one. The password is stored in
`~/.gmail-alarm/local-config.json`, readable by anyone with your login on this
machine; revoke it from the same Google page whenever you like.

Mail is fetched with `BODY.PEEK` and the mailbox opened read-only, so checking
never marks anything as read — opening the email is still what stops the alarm.

The trade-off against the Apps Script version: matching only happens while the
laptop is awake and this is running. The Apps Script version keeps watching from
Google's servers even when the laptop is off, and rings the moment it comes
back. Here, an email that arrives while the machine is asleep is found on the
next check after it wakes.

## Doing it with no credentials at all

Windows keeps every notification in an API that local programs can read with
your permission, so the alarm can watch the notification instead of the mailbox.
No Google account, no password, no App Password, no consent screen.

```powershell
py -m pip install winsdk
py notification_watcher.py --list                          # see what yours look like
py notification_watcher.py --from datadog --subject URGENT
```

Run `--list` first. Notification wording differs between Chrome, Thunderbird and
Outlook, and it is the only way to know what text you actually have to match on.

Two things must be true or nothing ever arrives:

- Settings → Privacy & security → Notifications → **Let apps access your
  notifications** must be on.
- Something must be raising mail notifications in the first place. **Gmail in a
  browser only notifies while a Gmail tab is open** — close the tab and the
  alarm goes deaf. A desktop client (Thunderbird, Outlook) notifies regardless,
  which is the more reliable arrangement, and signing in to one of those uses
  Google's normal login rather than the consent screen that blocks a personal
  Apps Script.

This is the least reliable of the three approaches — it depends on notification
text that no one guarantees the shape of — but it is the only one that needs
nothing from Google at all.

## Keeping the laptop awake

**A sleeping laptop has no network connection and will not ring.** This is the
one real weakness of a laptop as the alarm device — a phone stays reachable with
its screen off, a sleeping laptop does not. Keep it awake for the hours that
matter:

| | Keep it awake | Force volume up |
| --- | --- | --- |
| **macOS** | `caffeinate -s python3 alarm_listener.py your-topic` | automatic |
| **Linux** | `systemd-inhibit --what=sleep python3 alarm_listener.py your-topic` | automatic |
| **Windows** | automatic (idle sleep only — also set Settings → System → Power → Screen and sleep → *Never* when plugged in) | automatic |

On Windows the listener asks the OS to suppress idle sleep while it runs, which
needs no admin rights but does not survive closing the lid or choosing Sleep.

The listener forces output volume to 100% before each siren on all three
platforms — on Windows by pressing the volume-up media key, which also unmutes.
On every platform: **unplug the headphones**.

**No sound?** Run `python3 alarm_listener.py --diagnose`. It tests each layer
separately — whether a player was found, whether a bare system beep works,
whether the siren file plays — and says which one is failing.

Closing the lid sleeps most laptops regardless of the above — on macOS that
can't be prevented without an external display or third-party tooling, so leave
the lid open.

Because this failure mode is the one thing the Gmail side can't engineer around,
consider turning on the `sms` backup in `CONFIG` (see [Reference](#reference)).
It costs nothing and reaches a phone that's still awake.

**Autostart**, so you don't have to remember: a Login Item wrapping the
`caffeinate` line (macOS), a user systemd service (Linux), or a Task Scheduler
task set to *Run whether user is logged on or not* (Windows).

## How it decides to ring

1. Gmail search, minute-exact via `after:<epoch>`, over the last
   `lookbackMinutes` (default 15) so a skipped run doesn't lose an alarm.
2. A second check in JavaScript against the raw `From` and `Subject` — Gmail's
   search stems words and ignores punctuation, which is too loose for something
   that wakes you at 3am.
3. Message IDs already handled are remembered (last 300) so the same email
   never rings twice as a new alarm.

On the laptop side, dropped connections are handled: the listener reconnects
with backoff, then asks ntfy for anything published while it was offline, and
dedupes by message id so a replayed alarm doesn't ring twice.

## How it stops

The alarm re-fires every minute until **any** of these:

- you **open the email** (it stops being unread) — the normal case;
- you remove the `ALARM` label, or trash the message;
- `escalation.maxRepeats` (default 15) alarms have fired;
- `escalation.stopAfterMinutes` (default 30) have passed.

Pressing Enter on the laptop silences the *current* siren only. The next
escalation a minute later will ring again — opening the email is what actually
ends it.

## Reference

**Listener flags:**

| Flag | Does |
| --- | --- |
| `--test` | Ring once and exit. |
| `--seconds N` | How long one siren burst rings (default 45). |
| `--server URL` | Point at a self-hosted ntfy instead of ntfy.sh. |

The topic can come from the `NTFY_TOPIC` environment variable instead of the
command line, which is tidier for autostart.

**Apps Script functions you run by hand** (editor → function dropdown → Run):

| Function | Does |
| --- | --- |
| `setup()` | Creates the label, installs the every-minute trigger, resets state. |
| `testAlarm()` | Fires an alarm right now, with no email involved. |
| `showStatus()` | Logs the trigger count, live search query, and active alarm. |
| `teardown()` | Removes the trigger. Nothing runs after this. |

**Optional backup channels** (both off by default, both free):

- `sms` — mails your carrier's email-to-SMS gateway, e.g.
  `5551234567@vtext.com` (Verizon), `@txt.att.net` (AT&T),
  `@tmomail.net` (T-Mobile). Worth enabling as insurance against the laptop
  being asleep.
- `calendarPing` — creates a Calendar event one minute out with a popup
  reminder, so the Calendar app notifies you too.

Both fire once, on the first alarm only — they're safety nets, not second alarm
clocks.

**`activeHours`** — off by default. Turn it on to only ring during a window
(e.g. `startHour: 22, endHour: 8` for overnight on-call). The window may cross
midnight.

## Ringing a phone as well, or instead

The Apps Script half doesn't care what's listening, so a phone can subscribe to
the same topic alongside the laptop — useful as a hedge against the laptop
sleeping.

1. Install **ntfy** from the Play Store or App Store.
2. **+** → subscribe to the same topic string.
3. Open the subscribed topic → its settings:
   - **Android**: set the notification sound to your loudest alarm tone, and in
     Android's Do Not Disturb settings allow ntfy to override DND.
   - **iOS**: enable notifications for ntfy, allow **Critical Alerts** and
     **Time Sensitive** notifications, and add ntfy as an allowed app in your
     sleep Focus.

Test it independently of everything else:

```bash
curl -H "Priority: max" -H "Title: WAKE UP" -d "test" https://ntfy.sh/your-topic-here
```

There's also a zero-install option for a second machine: open
[ntfy.sh/app](https://ntfy.sh/app) in a browser tab and subscribe. The tab has
to stay open, browsers throttle background tabs, and you get a short
notification ding rather than a sustained siren — fine as a backup, not as the
thing you bet a flight on.

## Things worth knowing

- **Apps Script quota.** Consumer Gmail accounts get 90 minutes of total
  trigger runtime per day. At 1440 runs/day that's 3.7 seconds per run, and an
  idle run (one Gmail search, no match) is well under a second — but if you see
  quota errors in the executions log, change `everyMinutes(1)` to
  `everyMinutes(5)` in `setup()` and re-run it.
- **Latency.** Worst case is about a minute of trigger interval plus Gmail's own
  delivery time. Apps Script also fires minute triggers with a little jitter, so
  treat this as "within a couple of minutes", not "instant".
- **Errors aren't silent.** A failed run emails you a stack trace, at most once
  per hour.
- **The ntfy topic is the only secret.** Anyone who knows it can ring your
  laptop. Keep it random, and don't paste it into a public repo. If it leaks,
  pick a new topic in both `CONFIG` and the listener — or self-host ntfy and
  turn on auth.
