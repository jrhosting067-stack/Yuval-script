# Gmail Alarm

Wakes you up when a specific email arrives. A Gmail filter tags the mail, an
Apps Script trigger checks every minute, and a max-priority push notification
rings your phone — repeatedly — until you open the email.

Everything runs on free tiers: Gmail, Apps Script triggers, and
[ntfy.sh](https://ntfy.sh) (open source, no account, no payment).

```
email arrives ──▶ Gmail filter ──▶ label: ALARM
                                      │
              every-minute trigger ──▶ Code.gs searches for unread matches
                                      │
                                      ├─▶ ntfy topic ──┬─▶ 🔔 phone (ntfy app)
                                      │                └─▶ 🔔 laptop (alarm_listener.py)
                                      ├─▶ (optional) SMS via carrier gateway
                                      └─▶ (optional) Calendar popup
                                      │
                     you open the email ──▶ alarm stops
```

## Files

| Path | What it is |
| --- | --- |
| `apps-script/Code.gs` | The script. All configuration lives in the `CONFIG` block at the top. |
| `apps-script/appsscript.json` | Manifest — timezone and OAuth scopes. |
| `gmail/filters.xml` | Importable Gmail filter that applies the `ALARM` label. |
| `laptop/alarm_listener.py` | Runs on a laptop and sounds a siren through its speakers. Only needed if the laptop is your alarm device. |

## Setup (about 10 minutes)

**Alarm device.** Steps 1 and 3 below assume a phone. If the laptop itself
should ring, see [Ringing a laptop](#ringing-a-laptop-instead-of-a-phone) and do
that instead of step 1 — steps 2 and 3 are the same either way.

### 1. Phone: install ntfy

1. Install **ntfy** from the Play Store or App Store.
2. Pick a topic name that is long and random — `yuval-alarm-8f2b91c4d7` , not
   `alarm`. Topics on the public server are unauthenticated: anyone who guesses
   the name can read messages sent to it, and can ring your phone.
3. In the app: **+** → subscribe to that topic.
4. Open the subscribed topic → its settings, and turn it into a real alarm:
   - **Android**: set the notification sound to your loudest alarm tone, and in
     Android's Do Not Disturb settings allow ntfy to override DND.
   - **iOS**: enable notifications for ntfy, allow **Critical Alerts** and
     **Time Sensitive** notifications, and add ntfy as an allowed app in your
     sleep Focus.

Test the phone side before touching the script: from any terminal,

```bash
curl -H "Priority: max" -H "Title: WAKE UP" -d "test" https://ntfy.sh/your-topic-here
```

If that doesn't wake you, the script won't either — fix the phone settings
first.

### 2. Gmail: import the filter

1. Edit `gmail/filters.xml` — replace `alerts@example.com` and `URGENT` with
   your real sender and subject text.
2. Gmail → **Settings** → **Filters and Blocked Addresses** → **Import
   filters** → choose the file → **Open file** → **Create filters**.

The filter labels, stars, marks important, and never sends to spam. It
deliberately does **not** mark as read or archive — the script uses "still
unread" as the signal that you haven't seen it yet.

### 3. Apps Script: install the script

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
6. Select **`testAlarm`** → **Run**. Your phone should ring.

That's it. The trigger is now running every minute.

## How it decides to ring

1. Gmail search, minute-exact via `after:<epoch>`, over the last
   `lookbackMinutes` (default 15) so a skipped run doesn't lose an alarm.
2. A second check in JavaScript against the raw `From` and `Subject` — Gmail's
   search stems words and ignores punctuation, which is too loose for something
   that wakes you at 3am.
3. Message IDs already handled are remembered (last 300) so the same email
   never rings twice as a new alarm.

## How it stops

The alarm re-fires every minute until **any** of these:

- you **open the email** (it stops being unread) — the normal case;
- you remove the `ALARM` label, or trash the message;
- `escalation.maxRepeats` (default 15) alarms have fired;
- `escalation.stopAfterMinutes` (default 30) have passed.

## Reference

**Functions you run by hand** (Apps Script editor → function dropdown → Run):

| Function | Does |
| --- | --- |
| `setup()` | Creates the label, installs the every-minute trigger, resets state. |
| `testAlarm()` | Fires an alarm right now, with no email involved. |
| `showStatus()` | Logs the trigger count, live search query, and active alarm. |
| `teardown()` | Removes the trigger. Nothing runs after this. |

**Optional backup channels** (both off by default, both free):

- `sms` — mails your carrier's email-to-SMS gateway, e.g.
  `5551234567@vtext.com` (Verizon), `@txt.att.net` (AT&T),
  `@tmomail.net` (T-Mobile). Texts get through most DND configurations.
- `calendarPing` — creates a Calendar event one minute out with a popup
  reminder, so the Calendar app notifies you too.

Both fire once, on the first alarm only.

**`activeHours`** — off by default. Turn it on to only ring during a window
(e.g. `startHour: 22, endHour: 8` for overnight on-call). The window may cross
midnight.

## Ringing a laptop instead of a phone

Apps Script runs on Google's servers, so it can't make a laptop make noise. The
laptop needs something local listening. `laptop/alarm_listener.py` holds an open
connection to the same ntfy topic and plays a loud two-tone siren through the
speakers whenever a message arrives — including the every-minute escalation
repeats, which stop when you open the email.

Python 3.7+, standard library only. Nothing to install, and no Gmail credentials
ever touch the laptop — it only ever sees the ntfy topic.

```bash
python3 alarm_listener.py your-topic-here     # start listening
python3 alarm_listener.py your-topic --test   # ring right now, then exit
```

Press **Enter** to silence the current siren, Ctrl+C to stop listening. Run
`--test` first and make sure it's genuinely loud enough to wake you.

### The part that will actually catch you out

**A sleeping laptop has no network connection and will not ring.** This is the
one real weakness of using a laptop instead of a phone — a phone stays reachable
when its screen is off, a sleeping laptop does not. Keep it awake for the hours
that matter:

| | Keep it awake | Force volume up |
| --- | --- | --- |
| **macOS** | `caffeinate -s python3 alarm_listener.py your-topic` | automatic |
| **Linux** | `systemd-inhibit --what=sleep python3 alarm_listener.py your-topic` | automatic |
| **Windows** | Settings → System → Power → Screen and sleep → *Never* (plugged in) | set it by hand |

The script forces output volume to 100% on macOS and Linux before each siren.
Windows has no built-in command for that, so set the volume yourself. On every
platform: **unplug the headphones**.

Closing the lid sleeps most laptops regardless of the above — on macOS that
can't be prevented without an external display or third-party tooling, so leave
the lid open.

### Other things to know

- **Python.** Linux has it. macOS prompts to install Command Line Tools the
  first time you run `python3`. Windows usually doesn't have it —
  `winget install Python.Python.3.12`, then use `py` instead of `python3`.
- **Autostart** so you don't have to remember: a Login Item wrapping the
  `caffeinate` line (macOS), a user systemd service (Linux), or a Task Scheduler
  task set to *Run whether user is logged on or not* (Windows).
- **Dropped connections** are handled — it reconnects with backoff, then asks
  ntfy for anything published while it was offline, and dedupes by message id so
  a replayed alarm doesn't ring twice.
- **Zero-install alternative**: open [ntfy.sh/app](https://ntfy.sh/app) in a
  browser tab and subscribe to the topic. It works, but the tab has to stay
  open, browsers throttle background tabs, and you get a short notification
  ding rather than a sustained siren. Fine as a backup, not as the thing you
  bet a flight on.

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
  phone. Keep it random, and don't paste it into a public repo. If it leaks,
  pick a new topic in both the app and `CONFIG` — or self-host ntfy and turn on
  auth.
