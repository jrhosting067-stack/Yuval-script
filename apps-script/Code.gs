/**
 * Gmail Alarm
 * -----------
 * A time-driven Apps Script that runs every minute, looks for mail matching a
 * sender / subject rule, and fires a loud alarm on your phone until you
 * acknowledge it by opening the email.
 *
 * Everything here runs on free tiers: Apps Script triggers, Gmail, and ntfy.sh
 * (open-source push, no account needed).
 *
 * First run:  select setup()  -> Run  -> approve the OAuth prompt.
 * Test it:    select testAlarm() -> Run (fires the alarm without any email).
 * Stop it:    select teardown() -> Run.
 */

// ---------------------------------------------------------------------------
// CONFIG — edit this block, nothing else.
// ---------------------------------------------------------------------------
const CONFIG = {
  // Who counts as an alarm. Leave a list empty to ignore that criterion.
  senders: ['alerts@example.com'],          // 'boss@work.com', '@pagerduty.com'
  subjectContains: ['URGENT'],              // matched as Gmail subject: terms

  // true  -> sender AND subject must both match
  // false -> sender OR subject is enough
  requireBoth: true,

  // Anything extra you want ANDed into the Gmail search, e.g. 'has:attachment'.
  extraQuery: '',

  // Label applied by the Gmail filter (see gmail/filters.xml). The script also
  // works without the filter — it searches by sender/subject directly — but the
  // label makes it faster and gives you a place to eyeball what fired.
  labelName: 'ALARM',

  // How far back to look on each run. Covers a missed trigger or two.
  lookbackMinutes: 15,

  // --- Alarm channel: ntfy.sh (free, no signup) ---------------------------
  // Install the "ntfy" app (Android/iOS), subscribe to the SAME topic string,
  // and in the app set that topic's notification to your loudest alarm sound
  // and allow it to override Do Not Disturb / enable Critical Alerts on iOS.
  // The topic is a public secret: use a long random string, not "alarm".
  ntfy: {
    enabled: true,
    server: 'https://ntfy.sh',
    topic: 'CHANGE-ME-to-a-long-random-string-8f2b91',
    title: 'WAKE UP',
    // Opens Gmail when the notification is tapped.
    clickUrl: 'https://mail.google.com/mail/u/0/#search/label%3AALARM'
  },

  // --- Backup channel: carrier email-to-SMS gateway (free) ----------------
  // e.g. Verizon 5551234567@vtext.com, AT&T 5551234567@txt.att.net,
  //      T-Mobile 5551234567@tmomail.net. Texts ring through most DND setups.
  sms: {
    enabled: false,
    address: '5551234567@vtext.com'
  },

  // --- Backup channel: Google Calendar event 1 minute out -----------------
  // The Calendar app's notification is another way through to a locked phone.
  calendarPing: {
    enabled: false,
    minutesOut: 1
  },

  // Keep re-firing every minute until you open the email (or we give up).
  escalation: {
    maxRepeats: 15,           // total alarms per email, including the first
    stopAfterMinutes: 30      // hard stop even if never acknowledged
  },

  // Optional: only ever fire during a window (e.g. overnight on-call).
  // Hours are 0-23 in the script's time zone; the window may cross midnight.
  activeHours: {
    enabled: false,
    startHour: 22,
    endHour: 8
  },

  // Where to mail script errors. Blank = the account running the script.
  errorNotifyEmail: '',

  debug: false
};

// ---------------------------------------------------------------------------
// Property keys
// ---------------------------------------------------------------------------
const PROP_SEEN = 'seenMessageIds';
const PROP_ACTIVE = 'activeAlarm';
const PROP_LAST_RUN = 'lastRunEpoch';
const TRIGGER_FN = 'checkForAlarmEmails';

// ---------------------------------------------------------------------------
// Setup / teardown
// ---------------------------------------------------------------------------

/** Creates the label, installs the every-minute trigger, clears old state. */
function setup() {
  if (CONFIG.ntfy.enabled && CONFIG.ntfy.topic.indexOf('CHANGE-ME') === 0) {
    throw new Error('Set CONFIG.ntfy.topic to your own random topic string first.');
  }

  if (!CONFIG.senders.length && !CONFIG.subjectContains.length) {
    throw new Error('Set CONFIG.senders and/or CONFIG.subjectContains — ' +
                    'with both empty every unread email would ring the alarm.');
  }

  if (!GmailApp.getUserLabelByName(CONFIG.labelName)) {
    GmailApp.createLabel(CONFIG.labelName);
  }

  removeTriggers_();
  ScriptApp.newTrigger(TRIGGER_FN).timeBased().everyMinutes(1).create();

  const props = PropertiesService.getScriptProperties();
  props.deleteProperty(PROP_ACTIVE);
  props.setProperty(PROP_SEEN, JSON.stringify([]));
  props.setProperty(PROP_LAST_RUN, String(nowEpoch_()));

  Logger.log('Setup complete. Watching: ' + buildQuery_(nowEpoch_() - 60));
}

/** Removes the trigger. Nothing else runs after this. */
function teardown() {
  removeTriggers_();
  PropertiesService.getScriptProperties().deleteProperty(PROP_ACTIVE);
  Logger.log('Triggers removed.');
}

function removeTriggers_() {
  ScriptApp.getProjectTriggers()
    .filter(function (t) { return t.getHandlerFunction() === TRIGGER_FN; })
    .forEach(function (t) { ScriptApp.deleteTrigger(t); });
}

/** Prints current state — handy when something feels stuck. */
function showStatus() {
  const props = PropertiesService.getScriptProperties();
  const triggers = ScriptApp.getProjectTriggers()
    .filter(function (t) { return t.getHandlerFunction() === TRIGGER_FN; });
  Logger.log([
    'triggers installed: ' + triggers.length,
    'query: ' + buildQuery_(nowEpoch_() - CONFIG.lookbackMinutes * 60),
    'active alarm: ' + (props.getProperty(PROP_ACTIVE) || 'none'),
    'last run: ' + (props.getProperty(PROP_LAST_RUN) || 'never'),
    'seen ids: ' + JSON.parse(props.getProperty(PROP_SEEN) || '[]').length
  ].join('\n'));
}

/** Fires the alarm right now so you can check volume and DND settings. */
function testAlarm() {
  fireAlarm_({
    subject: 'Gmail Alarm test',
    from: 'test@localhost',
    permalink: CONFIG.ntfy.clickUrl,
    id: 'test'
  }, 1);
  Logger.log('Test alarm sent.');
}

// ---------------------------------------------------------------------------
// Main loop — this is what the every-minute trigger calls.
// ---------------------------------------------------------------------------
function checkForAlarmEmails() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) return;   // previous run still going; skip this minute

  try {
    const props = PropertiesService.getScriptProperties();
    const now = nowEpoch_();

    escalateActiveAlarm_(props, now);

    if (!withinActiveHours_(new Date())) {
      props.setProperty(PROP_LAST_RUN, String(now));
      return;
    }

    const since = now - CONFIG.lookbackMinutes * 60;
    const threads = GmailApp.search(buildQuery_(since), 0, 20);
    if (!threads.length) {
      props.setProperty(PROP_LAST_RUN, String(now));
      return;
    }

    const seen = JSON.parse(props.getProperty(PROP_SEEN) || '[]');
    const label = GmailApp.getUserLabelByName(CONFIG.labelName);
    let newest = null;

    GmailApp.getMessagesForThreads(threads).forEach(function (messages) {
      messages.forEach(function (message) {
        const id = message.getId();
        if (seen.indexOf(id) !== -1) return;
        if (message.getDate().getTime() / 1000 < since) return;
        if (!message.isUnread()) return;          // already dealt with by hand
        if (!matches_(message)) return;

        seen.push(id);
        const hit = {
          id: id,
          subject: message.getSubject(),
          from: message.getFrom(),
          permalink: 'https://mail.google.com/mail/u/0/#all/' + message.getThread().getId()
        };
        if (label) message.getThread().addLabel(label);
        if (!newest || message.getDate() > newest.date) {
          newest = { hit: hit, date: message.getDate() };
        }
      });
    });

    props.setProperty(PROP_SEEN, JSON.stringify(seen.slice(-300)));
    props.setProperty(PROP_LAST_RUN, String(now));

    if (newest) {
      fireAlarm_(newest.hit, 1);
      props.setProperty(PROP_ACTIVE, JSON.stringify({
        messageId: newest.hit.id,
        subject: newest.hit.subject,
        from: newest.hit.from,
        permalink: newest.hit.permalink,
        firstFiredAt: now,
        lastFiredAt: now,
        count: 1
      }));
    }
  } catch (err) {
    reportError_(err);
    throw err;
  } finally {
    lock.releaseLock();
  }
}

/**
 * Keeps ringing an unacknowledged alarm. Acknowledging = opening the email
 * (it stops being unread), or removing the ALARM label, or deleting it.
 */
function escalateActiveAlarm_(props, now) {
  const raw = props.getProperty(PROP_ACTIVE);
  if (!raw) return;

  const alarm = JSON.parse(raw);

  if (isAcknowledged_(alarm.messageId)) {
    props.deleteProperty(PROP_ACTIVE);
    log_('Alarm acknowledged: ' + alarm.subject);
    return;
  }

  const minutesUp = (now - alarm.firstFiredAt) / 60;
  if (alarm.count >= CONFIG.escalation.maxRepeats ||
      minutesUp >= CONFIG.escalation.stopAfterMinutes) {
    props.deleteProperty(PROP_ACTIVE);
    log_('Alarm gave up after ' + alarm.count + ' tries: ' + alarm.subject);
    return;
  }

  alarm.count += 1;
  alarm.lastFiredAt = now;
  fireAlarm_(alarm, alarm.count);
  props.setProperty(PROP_ACTIVE, JSON.stringify(alarm));
}

function isAcknowledged_(messageId) {
  if (messageId === 'test') return true;
  try {
    const message = GmailApp.getMessageById(messageId);
    if (!message) return true;
    if (!message.isUnread()) return true;
    if (message.isInTrash()) return true;
    const label = GmailApp.getUserLabelByName(CONFIG.labelName);
    if (label) {
      const stillLabelled = message.getThread().getLabels().some(function (l) {
        return l.getName() === CONFIG.labelName;
      });
      if (!stillLabelled) return true;
    }
    return false;
  } catch (err) {
    return true;   // message vanished — nothing left to ring about
  }
}

// ---------------------------------------------------------------------------
// Matching
// ---------------------------------------------------------------------------

/** Gmail search string. `after:` takes epoch seconds, so this is minute-exact. */
function buildQuery_(sinceEpoch) {
  const parts = ['is:unread', 'after:' + sinceEpoch];

  const senderClause = CONFIG.senders.length
    ? '(' + CONFIG.senders.map(function (s) { return 'from:' + s; }).join(' OR ') + ')'
    : '';
  const subjectClause = CONFIG.subjectContains.length
    ? '(' + CONFIG.subjectContains.map(function (s) {
        return 'subject:"' + s.replace(/"/g, '') + '"';
      }).join(' OR ') + ')'
    : '';

  if (senderClause && subjectClause) {
    parts.push(CONFIG.requireBoth
      ? senderClause + ' ' + subjectClause
      : '(' + senderClause + ' OR ' + subjectClause + ')');
  } else if (senderClause || subjectClause) {
    parts.push(senderClause || subjectClause);
  }

  if (CONFIG.extraQuery) parts.push(CONFIG.extraQuery);
  return parts.join(' ');
}

/**
 * Second pass in JS. Gmail's search is fuzzy — it stems words and ignores
 * punctuation — so re-check the raw header text before waking someone up.
 */
function matches_(message) {
  const from = message.getFrom().toLowerCase();
  const subject = (message.getSubject() || '').toLowerCase();

  const senderOk = !CONFIG.senders.length || CONFIG.senders.some(function (s) {
    return from.indexOf(s.toLowerCase()) !== -1;
  });
  const subjectOk = !CONFIG.subjectContains.length || CONFIG.subjectContains.some(function (s) {
    return subject.indexOf(s.toLowerCase()) !== -1;
  });

  if (!CONFIG.senders.length || !CONFIG.subjectContains.length) {
    return senderOk && subjectOk;
  }
  return CONFIG.requireBoth ? (senderOk && subjectOk) : (senderOk || subjectOk);
}

function withinActiveHours_(date) {
  if (!CONFIG.activeHours.enabled) return true;
  const hour = Number(Utilities.formatDate(date, Session.getScriptTimeZone(), 'H'));
  const start = CONFIG.activeHours.startHour;
  const end = CONFIG.activeHours.endHour;
  return start <= end ? (hour >= start && hour < end) : (hour >= start || hour < end);
}

// ---------------------------------------------------------------------------
// Alarm channels
// ---------------------------------------------------------------------------
function fireAlarm_(hit, attempt) {
  const body = hit.from + '\n' + hit.subject;

  if (CONFIG.ntfy.enabled) {
    try {
      sendNtfy_(hit, attempt, body);
    } catch (err) {
      reportError_(err);
    }
  }

  // Backups fire once, on the first alarm only — no point texting yourself 15 times.
  if (attempt === 1 && CONFIG.sms.enabled) {
    try {
      GmailApp.sendEmail(CONFIG.sms.address, CONFIG.ntfy.title, body);
    } catch (err) {
      reportError_(err);
    }
  }

  if (attempt === 1 && CONFIG.calendarPing.enabled) {
    try {
      createCalendarPing_(hit);
    } catch (err) {
      reportError_(err);
    }
  }

  log_('Alarm #' + attempt + ': ' + hit.subject);
}

function sendNtfy_(hit, attempt, body) {
  const response = UrlFetchApp.fetch(CONFIG.ntfy.server + '/' + CONFIG.ntfy.topic, {
    method: 'post',
    payload: body + (attempt > 1 ? '\n(alarm ' + attempt + ')' : ''),
    headers: {
      Title: CONFIG.ntfy.title + (attempt > 1 ? ' (' + attempt + ')' : ''),
      Priority: 'max',
      Tags: 'rotating_light',
      Click: hit.permalink || CONFIG.ntfy.clickUrl
    },
    muteHttpExceptions: true
  });
  const code = response.getResponseCode();
  if (code >= 300) {
    throw new Error('ntfy returned ' + code + ': ' + response.getContentText());
  }
}

function createCalendarPing_(hit) {
  const start = new Date(Date.now() + CONFIG.calendarPing.minutesOut * 60 * 1000);
  const end = new Date(start.getTime() + 5 * 60 * 1000);
  const event = CalendarApp.getDefaultCalendar()
    .createEvent(CONFIG.ntfy.title + ': ' + hit.subject, start, end, { description: hit.permalink });
  event.removeAllReminders();
  event.addPopupReminder(0);
}

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------
function nowEpoch_() {
  return Math.floor(Date.now() / 1000);
}

function log_(message) {
  if (CONFIG.debug) Logger.log(message);
}

/** Mails you at most one error report per hour, so a broken run isn't silent. */
function reportError_(err) {
  Logger.log('ERROR: ' + err);
  const props = PropertiesService.getScriptProperties();
  const last = Number(props.getProperty('lastErrorMailEpoch') || 0);
  const now = nowEpoch_();
  if (now - last < 3600) return;
  props.setProperty('lastErrorMailEpoch', String(now));
  try {
    GmailApp.sendEmail(
      CONFIG.errorNotifyEmail || Session.getEffectiveUser().getEmail(),
      'Gmail Alarm error',
      String(err && err.stack ? err.stack : err)
    );
  } catch (ignored) {}
}
