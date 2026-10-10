import { test } from 'node:test';
import assert from 'node:assert/strict';
import { loadConfig, ConfigError } from '../src/config.mjs';

const TOKEN = 'a'.repeat(40);
const base = (extra = {}) => ({ MCP_AUTH_TOKEN: TOKEN, ...extra });

test('defaults', () => {
  const c = loadConfig(base());
  assert.equal(c.firecrawlApiUrl, 'http://api:3002');
  assert.equal(c.firecrawlApiKey, undefined);
  assert.equal(c.host, '0.0.0.0');
  assert.equal(c.port, 8000);
  assert.equal(c.publicPort, 8768);
  assert.equal(c.childPort, 3000);
  assert.equal(c.maxRequestBytes, 1048576);
  assert.equal(c.childStartTimeoutSeconds, 30);
  assert.equal(c.logLevel, 'info');
  assert.equal(c.apiFilterEnabled, true);
  assert.equal(c.apiFilterPort, 3001);
});

test('API_FILTER_ENABLED parsing', () => {
  for (const [v, want] of [['true', true], ['TRUE', true], ['1', true], ['false', false], ['False', false], ['0', false], ['', true]]) {
    assert.equal(loadConfig(base({ API_FILTER_ENABLED: v })).apiFilterEnabled, want, v);
  }
  assert.equal(loadConfig(base({ API_FILTER_PORT: '4001' })).apiFilterPort, 4001);
});

test('overrides are honoured', () => {
  const c = loadConfig(base({
    FIRECRAWL_API_URL: 'https://fc.example.com', FIRECRAWL_API_KEY: 'k', HOST: '127.0.0.1', PORT: '9000',
    PUBLIC_PORT: '9001', CHILD_PORT: '9002', MAX_REQUEST_BYTES: '10', CHILD_START_TIMEOUT_SECONDS: '5',
    LOG_LEVEL: 'DEBUG',
  }));
  assert.equal(c.firecrawlApiKey, 'k');
  assert.equal(c.port, 9000);
  assert.equal(c.publicPort, 9001);
  assert.equal(c.childPort, 9002);
  assert.equal(c.maxRequestBytes, 10);
  assert.equal(c.logLevel, 'debug');
});

const failures = [
  ['short token', { MCP_AUTH_TOKEN: 'short' }, /at least 32/],
  ['placeholder token', { MCP_AUTH_TOKEN: 'replace-me-' + 'x'.repeat(40) }, /placeholder/],
  ['bad url', { FIRECRAWL_API_URL: 'not a url' }, /FIRECRAWL_API_URL/],
  ['ftp url', { FIRECRAWL_API_URL: 'ftp://x' }, /http or https/],
  ['url creds', { FIRECRAWL_API_URL: 'http://user:pw@api:3002' }, /credentials/],
  ['url user only', { FIRECRAWL_API_URL: 'http://user@api:3002' }, /credentials/],
  ['bad level', { LOG_LEVEL: 'loud' }, /LOG_LEVEL/],
  ['non-numeric port', { PORT: 'abc' }, /PORT must be an integer/],
  ['port zero', { CHILD_PORT: '0' }, /CHILD_PORT must be between/],
  ['port too big', { PUBLIC_PORT: '70000' }, /PUBLIC_PORT/],
  ['bad child port', { CHILD_PORT: '-1' }, /CHILD_PORT/],
  ['bad max bytes', { MAX_REQUEST_BYTES: '0' }, /MAX_REQUEST_BYTES/],
  ['bad filter flag', { API_FILTER_ENABLED: 'yes' }, /API_FILTER_ENABLED must be true or false/],
  ['filter port non-numeric', { API_FILTER_PORT: 'x' }, /API_FILTER_PORT must be an integer/],
  ['filter port zero', { API_FILTER_PORT: '0' }, /API_FILTER_PORT must be between/],
  ['filter port too big', { API_FILTER_PORT: '65536' }, /API_FILTER_PORT must be between/],
  ['filter port clashes with child', { API_FILTER_PORT: '3000' }, /API_FILTER_PORT must differ from CHILD_PORT/],
  ['filter port clashes with custom child', { CHILD_PORT: '3001' }, /API_FILTER_PORT must differ from CHILD_PORT/],
  ['bad timeout', { CHILD_START_TIMEOUT_SECONDS: '0' }, /CHILD_START_TIMEOUT_SECONDS/],
];

for (const [name, extra, re] of failures) {
  test(`rejects ${name}`, () => {
    const env = name === 'missing token' || name.includes('token') ? extra : base(extra);
    assert.throws(() => loadConfig(env), (e) => e instanceof ConfigError && re.test(e.message));
  });
}

test('errors never contain secret values', () => {
  const secret = 'S3CRET-' + 'z'.repeat(10);
  for (const env of [
    { MCP_AUTH_TOKEN: secret },
    { MCP_AUTH_TOKEN: 'replace-me-' + secret },
    base({ FIRECRAWL_API_URL: `http://u:${secret}@api` }),
    base({ FIRECRAWL_API_KEY: secret, PORT: 'bad' }),
    base({ LOG_LEVEL: secret }),
  ]) {
    try {
      loadConfig(env);
      assert.fail('expected throw');
    } catch (e) {
      assert.ok(e instanceof ConfigError);
      assert.ok(!e.message.includes(secret));
    }
  }
});

test('token is optional: unset or empty disables auth', () => {
  assert.equal(loadConfig({}).authToken, undefined);
  assert.equal(loadConfig({ MCP_AUTH_TOKEN: '' }).authToken, undefined);
  assert.equal(loadConfig(base()).authToken, TOKEN);
});
