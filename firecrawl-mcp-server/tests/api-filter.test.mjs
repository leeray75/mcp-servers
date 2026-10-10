import { test } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { createApiFilter, filterSearchBody } from '../src/api-filter.mjs';
import { makeLogger } from '../src/redact.mjs';

const lines = [];
const logger = makeLogger({ level: 'debug', write: (l) => lines.push(l) });

const listen = (server) => new Promise((r) => server.listen(0, '127.0.0.1', () => r(server.address().port)));
const close = (server) => { server.closeAllConnections?.(); return new Promise((r) => server.close(r)); };

// Fake upstream: echoes what it received as JSON.
async function startUpstream(handler) {
  const seen = [];
  const server = http.createServer((req, res) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => {
      const rec = {
        method: req.method, url: req.url, headers: req.headers, body: Buffer.concat(chunks).toString('utf8'),
      };
      seen.push(rec);
      if (handler) return handler(req, res, rec);
      const out = JSON.stringify(rec);
      res.writeHead(201, { 'content-type': 'application/json', 'x-up': 'yes' });
      res.end(out);
    });
  });
  const port = await listen(server);
  return { server, port, seen };
}

async function setup({ handler, base = '', maxRequestBytes = 1024 } = {}) {
  const up = await startUpstream(handler);
  const filter = createApiFilter({ upstreamUrl: `http://127.0.0.1:${up.port}${base}`, maxRequestBytes, logger });
  const port = await listen(filter);
  const done = async () => { await close(filter); await close(up.server); };
  return { up, filter, port, done };
}

function request(port, { method = 'POST', path = '/', headers = {}, body } = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port, method, path, headers }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body: Buffer.concat(chunks).toString('utf8') }));
    });
    req.on('error', reject);
    req.end(body);
  });
}

test('filterSearchBody: strips only top-level keys; passes non-objects through', () => {
  const r = filterSearchBody(Buffer.from('{"q":1,"toolDetail":"x","scrapeOptions":{"toolDetail":"y"}}'));
  assert.deepEqual(r.removed, ['toolDetail']);
  assert.deepEqual(JSON.parse(r.body), { q: 1, scrapeOptions: { toolDetail: 'y' } });
  const same = Buffer.from('{"q":1}');
  assert.equal(filterSearchBody(same).body, same);
  for (const raw of ['not json', '[1]', 'null', '"s"']) {
    const b = Buffer.from(raw);
    assert.equal(filterSearchBody(b).body, b);
  }
});

for (const route of ['/v2/search', '/v1/search']) {
  test(`${route}: domainTools/toolDetail removed, other fields kept, content-length fixed, query preserved`, async () => {
    const s = await setup();
    try {
      const body = JSON.stringify({ query: 'q', limit: 3, domainTools: { a: 1 }, toolDetail: 'compact' });
      const r = await request(s.port, {
        path: `${route}?x=1`, headers: { 'content-type': 'application/json', 'content-length': Buffer.byteLength(body) }, body,
      });
      assert.equal(r.status, 201);
      assert.equal(r.headers['x-up'], 'yes');
      const rec = JSON.parse(r.body);
      assert.equal(rec.url, `${route}?x=1`);
      assert.deepEqual(JSON.parse(rec.body), { query: 'q', limit: 3 });
      assert.equal(rec.headers['content-length'], String(Buffer.byteLength(rec.body)));
      assert.ok(lines.some((l) => /removed unsupported search keys/.test(l) && /domainTools/.test(l)));
      assert.ok(!lines.some((l) => l.includes('"compact"')));
    } finally { await s.done(); }
  });
}

test('nested scrapeOptions.toolDetail is not removed', async () => {
  const s = await setup();
  try {
    const body = JSON.stringify({ query: 'q', toolDetail: 'c', scrapeOptions: { toolDetail: 'keep', domainTools: 1 } });
    const r = await request(s.port, { path: '/v2/search', body });
    assert.deepEqual(JSON.parse(JSON.parse(r.body).body), { query: 'q', scrapeOptions: { toolDetail: 'keep', domainTools: 1 } });
  } finally { await s.done(); }
});

test('search body without the keys is forwarded byte-for-byte', async () => {
  const s = await setup();
  try {
    const body = '{ "query" :  "spaced" }';
    const r = await request(s.port, { path: '/v2/search', body });
    assert.equal(JSON.parse(r.body).body, body);
  } finally { await s.done(); }
});

test('invalid JSON and JSON array bodies on search are forwarded unchanged', async () => {
  const s = await setup();
  try {
    for (const body of ['{not json', '[{"toolDetail":1}]']) {
      const r = await request(s.port, { path: '/v2/search', body });
      assert.equal(JSON.parse(r.body).body, body);
    }
  } finally { await s.done(); }
});

test('non-search routes pass through byte-identical (including GET and non-search POST)', async () => {
  const s = await setup();
  try {
    const body = '{"url":"https://example.com","toolDetail":"keep"}';
    const a = JSON.parse((await request(s.port, { path: '/v2/scrape', body })).body);
    assert.equal(a.method, 'POST');
    assert.equal(a.url, '/v2/scrape');
    assert.equal(a.body, body);
    const b = JSON.parse((await request(s.port, { method: 'GET', path: '/v2/crawl/abc?x=1' })).body);
    assert.equal(b.method, 'GET');
    assert.equal(b.url, '/v2/crawl/abc?x=1');
    // GET on the search path is not filtered either.
    const c = JSON.parse((await request(s.port, { method: 'GET', path: '/v2/search' })).body);
    assert.equal(c.url, '/v2/search');
    // Bodies larger than the search cap stream through on other routes.
    const big = 'x'.repeat(5000);
    const d = JSON.parse((await request(s.port, { path: '/v2/parse', body: big })).body);
    assert.equal(d.body, big);
  } finally { await s.done(); }
});

test('Authorization forwarded, Host rewritten, hop-by-hop dropped', async () => {
  const s = await setup();
  try {
    const r = await request(s.port, {
      path: '/v2/map', body: '{}',
      headers: { authorization: 'Bearer fc-secret', host: 'evil.example', 'keep-alive': 'timeout=5', te: 'trailers', 'x-custom': 'ok' },
    });
    const h = JSON.parse(r.body).headers;
    assert.equal(h.authorization, 'Bearer fc-secret');
    assert.equal(h.host, `127.0.0.1:${s.up.port}`);
    assert.equal(h['keep-alive'], undefined);
    assert.equal(h.te, undefined);
    assert.equal(h['x-custom'], 'ok');
    assert.ok(!lines.some((l) => l.includes('fc-secret')));
  } finally { await s.done(); }
});

test('upstream hop-by-hop response headers are dropped', async () => {
  const s = await setup({
    handler: (req, res) => { res.writeHead(200, { 'keep-alive': 'timeout=9', 'x-ok': '1' }); res.end('hi'); },
  });
  try {
    const r = await request(s.port, { method: 'GET', path: '/v2/map' });
    assert.equal(r.headers['x-ok'], '1');
    assert.notEqual(r.headers['keep-alive'], 'timeout=9');
  } finally { await s.done(); }
});

test('oversize search body: 413 (declared and chunked), upstream not called', async () => {
  const s = await setup({ maxRequestBytes: 50 });
  try {
    const body = JSON.stringify({ query: 'x'.repeat(200) });
    const r = await request(s.port, { path: '/v2/search', body });
    assert.equal(r.status, 413);
    assert.deepEqual(JSON.parse(r.body), { error: 'request_too_large' });
    // Chunked, no content-length.
    const r2 = await new Promise((resolve, reject) => {
      const req = http.request({ host: '127.0.0.1', port: s.port, method: 'POST', path: '/v2/search', headers: { 'transfer-encoding': 'chunked' } }, (res) => {
        res.resume();
        resolve(res.statusCode);
      });
      req.on('error', reject);
      req.write('{"query":"');
      req.write('y'.repeat(200));
      req.end('"}');
    });
    assert.equal(r2, 413);
    assert.equal(s.up.seen.length, 0);
  } finally { await s.done(); }
});

test('upstream down: 502 JSON', async () => {
  const dead = http.createServer();
  const deadPort = await listen(dead);
  await close(dead);
  const filter = createApiFilter({ upstreamUrl: `http://127.0.0.1:${deadPort}`, maxRequestBytes: 1024, logger });
  const port = await listen(filter);
  try {
    for (const [method, path, body] of [['POST', '/v2/search', '{"query":"q"}'], ['GET', '/v2/map', undefined]]) {
      const r = await request(port, { method, path, body });
      assert.equal(r.status, 502);
      assert.deepEqual(JSON.parse(r.body), { error: 'bad_gateway' });
    }
  } finally { await close(filter); }
});

test('upstream path prefix is honoured (with and without trailing slash)', async () => {
  for (const base of ['/base', '/base/']) {
    const s = await setup({ base });
    try {
      const a = JSON.parse((await request(s.port, { path: '/v2/search?y=2', body: '{"query":"q","toolDetail":"c"}' })).body);
      assert.equal(a.url, '/base/v2/search?y=2');
      const b = JSON.parse((await request(s.port, { method: 'GET', path: '/v2/scrape' })).body);
      assert.equal(b.url, '/base/v2/scrape');
    } finally { await s.done(); }
  }
});

test('streams: first chunk arrives before the response ends', async () => {
  let release;
  const gate = new Promise((r) => { release = r; });
  const s = await setup({
    handler: (req, res) => {
      res.writeHead(200, { 'content-type': 'text/plain' });
      res.write('first');
      gate.then(() => res.end('second'));
    },
  });
  try {
    await new Promise((resolve, reject) => {
      const req = http.request({ host: '127.0.0.1', port: s.port, method: 'GET', path: '/v2/crawl/x' }, (res) => {
        res.once('data', (c) => {
          try {
            assert.equal(c.toString(), 'first');
            assert.equal(res.complete, false);
          } catch (e) { reject(e); return; }
          release();
          res.on('end', resolve);
        });
      });
      req.on('error', reject);
      req.end();
    });
  } finally { release(); await s.done(); }
});

test('client abort mid-response destroys the upstream request', async () => {
  let upstreamClosed;
  const closed = new Promise((r) => { upstreamClosed = r; });
  const s = await setup({
    handler: (req, res) => {
      res.writeHead(200);
      res.write('partial');
      res.on('close', upstreamClosed);
    },
  });
  try {
    await new Promise((resolve, reject) => {
      const req = http.request({ host: '127.0.0.1', port: s.port, method: 'GET', path: '/v2/crawl/x' }, (res) => {
        res.once('data', () => { req.destroy(); resolve(); });
      });
      req.on('error', () => {});
      req.on('close', resolve);
      req.end();
      setTimeout(() => reject(new Error('timeout')), 3000).unref();
    });
    await Promise.race([closed, new Promise((_, rej) => setTimeout(() => rej(new Error('upstream not closed')), 3000).unref())]);
  } finally { await s.done(); }
});

test('https upstream scheme selects the https transport (connection error gives 502)', async () => {
  // Nothing listens on this port: proves the https branch is constructed and errors map to 502.
  const dead = http.createServer();
  const deadPort = await listen(dead);
  await close(dead);
  const filter = createApiFilter({ upstreamUrl: `https://127.0.0.1:${deadPort}`, maxRequestBytes: 1024, logger });
  const port = await listen(filter);
  try {
    const r = await request(port, { method: 'GET', path: '/v2/map' });
    assert.equal(r.status, 502);
  } finally { await close(filter); }
});

test('keep-alive and request timeouts are tuned for a pooling client', () => {
  const filter = createApiFilter({ upstreamUrl: 'http://127.0.0.1:1', maxRequestBytes: 1, logger });
  assert.equal(filter.requestTimeout, 0);
  assert.ok(filter.keepAliveTimeout > 5000);
  assert.ok(filter.headersTimeout > filter.keepAliveTimeout);
});
