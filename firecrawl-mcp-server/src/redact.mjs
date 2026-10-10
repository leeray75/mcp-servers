// Secret redaction and a tiny leveled JSON logger.

const ORDER = { debug: 10, info: 20, warn: 30, error: 40 };

export function makeRedactor(secrets) {
  const base = secrets.filter((s) => typeof s === 'string' && s.length > 0);
  // Also match the JSON-escaped form (what a secret looks like inside a serialised log line).
  const list = [...new Set(base.flatMap((s) => [s, JSON.stringify(s).slice(1, -1)]))];
  return (text) => {
    let out = String(text);
    for (const s of list) out = out.split(s).join('[REDACTED]');
    return out;
  };
}

export function makeLogger({
  level = 'info',
  redact = (s) => s,
  write = (line) => process.stdout.write(line + '\n'),
} = {}) {
  const min = ORDER[level] ?? ORDER.info;
  const log = (lvl, msg, fields = {}) => {
    if (ORDER[lvl] < min) return;
    write(redact(JSON.stringify({ ts: new Date().toISOString(), level: lvl, msg, ...fields })));
  };
  return {
    debug: (m, f) => log('debug', m, f),
    info: (m, f) => log('info', m, f),
    warn: (m, f) => log('warn', m, f),
    error: (m, f) => log('error', m, f),
  };
}
