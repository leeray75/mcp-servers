import { test } from 'node:test';
import assert from 'node:assert/strict';
import { makeRedactor, makeLogger } from '../src/redact.mjs';

test('redactor removes token and api key everywhere', () => {
  const r = makeRedactor(['tok-123', 'fc-key', undefined, '']);
  assert.equal(r('a tok-123 b fc-key c tok-123'), 'a [REDACTED] b [REDACTED] c [REDACTED]');
  assert.equal(r('nothing here'), 'nothing here');
});

test('logger honours level and redacts', () => {
  const lines = [];
  const log = makeLogger({ level: 'warn', redact: makeRedactor(['sek']), write: (l) => lines.push(l) });
  log.debug('d');
  log.info('i');
  log.warn('w sek', { x: 'sek' });
  log.error('e');
  assert.equal(lines.length, 2);
  assert.ok(!lines.join('').includes('sek'));
  assert.equal(JSON.parse(lines[1]).level, 'error');
});

test('logger defaults write to stdout without throwing', () => {
  const log = makeLogger({ level: 'error' });
  log.debug('not printed');
  log.info('not printed');
  log.warn('not printed');
});

test('redacts JSON-escaped forms of secrets in log lines', () => {
  const secret = 'ab"cd\ef';
  const lines = [];
  const log = makeLogger({ level: 'info', redact: makeRedactor([secret]), write: (l) => lines.push(l) });
  log.info('leak', { value: secret });
  assert.ok(!lines[0].includes('ab\\"cd'));
  assert.ok(!lines[0].includes(secret));
  assert.ok(lines[0].includes('[REDACTED]'));
});
