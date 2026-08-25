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
| `laptop/install.sh` | One-command setup for the laptop half (macOS and Linux). |
| `laptop/install.ps1` | The same, for Windows. |
| `laptop/alarm_listener.py` | Runs on the laptop that rings. Standard library Python, nothing to install. |
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

## Keeping the laptop awake

**A sleeping laptop has no network connection and will not ring.** This is the
one real weakness of a laptop as the alarm device — a phone stays reachable with
its screen off, a sleeping laptop does not. Keep it awake for the hours that
matter:

| | Keep it awake | Force volume up |
| --- | --- | --- |
| **macOS** | `caffeinate -s python3 alarm_listener.py your-topic` | automatic |
| **Linux** | `systemd-inhibit --what=sleep python3 alarm_listener.py your-topic` | automatic |
| **Windows** | automatic (idle sleep only — also set Settings → System → Power → Screen and sleep → *Never* when plugged in) | set it by hand |

On Windows the listener asks the OS to suppress idle sleep while it runs, which
needs no admin rights but does not survive closing the lid or choosing Sleep.

The listener forces output volume to 100% on macOS and Linux before each siren.
Windows has no built-in command for that, so set the volume yourself. On every
platform: **unplug the headphones**.

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
