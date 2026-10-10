import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import net from 'node:net';
import { createGateway, tokenMatches, allowedHosts, probeChild } from '../src/gateway.mjs';
import { loadConfig } from '../src/config.mjs';
import { makeLogger } from '../src/redact.mjs';
import { startFakeChild, stopServer } from './fake-child.mjs';

const TOKEN = 'tok_' + 'q'.repeat(40);
let child;
let gw;
let gwPort;
let alive = true;
const logger = makeLogger({ level: 'error', write: () => {} });

function request({ method = 'GET', path = '/', headers = {}, body, host } = {}) {
  return new Promise((resolve, reject) => {
    const h = { host: host ?? `127.0.0.1:8768`, ...headers };
    const req = http.request({ host: '127.0.0.1', port: gwPort, method, path, headers: h }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, text: Buffer.concat(chunks).toString() }));
    });
    req.on('error', reject);
    if (body !== undefined) req.write(body);
    req.end();
  });
}
const auth = { authorization: `Bearer ${TOKEN}` };

before(async () => {
  child = await startFakeChild();
  const config = loadConfig({
    MCP_AUTH_TOKEN: TOKEN, CHILD_PORT: String(child.port), PUBLIC_PORT: '8768', MAX_REQUEST_BYTES: '1000',
  });
  gw = createGateway({ config, isChildAlive: () => alive, logger });
  await new Promise((r) => gw.listen(0, '127.0.0.1', r));
  gwPort = gw.address().port;
});

after(async () => {
  await stopServer(gw);
  await stopServer(child.server);
});

test('tokenMatches unit', () => {
  assert.equal(tokenMatches(undefined, TOKEN), false);
  assert.equal(tokenMatches('Basic abc', TOKEN), false);
  assert.equal(tokenMatches('Bearer ', TOKEN), false);
  assert.equal(tokenMatches('Bearer wrong', TOKEN), false);
  assert.equal(tokenMatches(`bearer ${TOKEN}`, TOKEN), false);
  assert.equal(tokenMatches(`Bearer ${TOKEN}`, TOKEN), true);
  assert.equal(tokenMatches(`Bearer ${TOKEN}x`, TOKEN), false);
});

test('allowedHosts derives from ports', () => {
  const h = allowedHosts(9999);
  assert.ok(h.has('127.0.0.1:9999') && h.has('localhost:8000') && h.has('[::1]:9999'));
});

test('auth: missing, wrong scheme, wrong token -> 401 with WWW-Authenticate', async () => {
  for (const headers of [{}, { authorization: `Basic ${TOKEN}` }, { authorization: 'Bearer nope' }]) {
    const r = await request({ method: 'POST', path: '/mcp', headers, body: '{}' });
    assert.equal(r.status, 401);
    assert.equal(r.headers['www-authenticate'], 'Bearer');
    assert.deepEqual(JSON.parse(r.text), { error: 'unauthorized' });
  }
});

test('auth: right token is proxied', async () => {
  const r = await request({ method: 'POST', path: '/mcp', headers: auth, body: '{"a":1}' });
  assert.equal(r.status, 200);
});

test('healthz: 200 when child up, unauthenticated, no secrets', async () => {
  const r = await request({ path: '/healthz' });
  assert.equal(r.status, 200);
  assert.deepEqual(JSON.parse(r.text), { status: 'ok' });
  assert.ok(!r.text.includes(TOKEN));
});

test('healthz: 503 when supervisor says child is dead', async () => {
  alive = false;
  const r = await request({ path: '/healthz' });
  alive = true;
  assert.equal(r.status, 503);
});

test('probeChild: false for a closed port, true for fake child', async () => {
  assert.equal(await probeChild(child.port), true);
  const srv = net.createServer().listen(0);
  await new Promise((r) => srv.once('listening', r));
  const free = srv.address().port;
  await new Promise((r) => srv.close(r));
  assert.equal(await probeChild(free, 300), false);
});

test('healthz: 503 when child port is not listening', async () => {
  const tmp = await startFakeChild();
  const config = loadConfig({ MCP_AUTH_TOKEN: TOKEN, CHILD_PORT: String(tmp.port) });
  const g = createGateway({ config, isChildAlive: () => true, logger });
  await new Promise((r) => g.listen(0, '127.0.0.1', r));
  await stopServer(tmp.server);
  const port = g.address().port;
  const status = await new Promise((resolve) => {
    http.get({ host: '127.0.0.1', port, path: '/healthz', headers: { host: '127.0.0.1:8768' } }, (res) => {
      res.resume();
      resolve(res.statusCode);
    });
  });
  await stopServer(g);
  assert.equal(status, 503);
});

test('host guard', async () => {
  for (const host of ['127.0.0.1:8768', 'localhost:8768', '[::1]:8768', '127.0.0.1:8000', 'localhost:8000']) {
    assert.equal((await request({ path: '/healthz', host })).status, 200, host);
  }
  for (const host of ['evil.example.com', 'evil.example.com:8768', '127.0.0.1:9', '127.0.0.1', '']) {
    assert.equal((await request({ path: '/healthz', host })).status, 421, host);
  }
  assert.equal((await request({ path: '/mcp', headers: auth, host: 'evil.test' })).status, 421);
});

test('proxy: JSON POST round trip, auth stripped, query preserved', async () => {
  const r = await request({
    method: 'POST', path: '/mcp?x=1&y=two', body: '{"hello":"world"}',
    headers: { ...auth, 'content-type': 'application/json', 'x-custom': 'keep', 'mcp-protocol-version': '2025-06-18' },
  });
  assert.equal(r.status, 200);
  const echoed = JSON.parse(r.text);
  assert.equal(echoed.method, 'POST');
  assert.equal(echoed.url, '/mcp?x=1&y=two');
  assert.equal(echoed.body, '{"hello":"world"}');
  assert.equal(echoed.headers.authorization, undefined);
  assert.equal(echoed.headers['x-custom'], 'keep');
  assert.equal(echoed.headers['mcp-protocol-version'], '2025-06-18');
  assert.equal(echoed.headers.host, `127.0.0.1:${child.port}`);
  assert.ok(!r.text.includes(TOKEN));
});

test('proxy: status codes and other methods preserved', async () => {
  for (const code of [202, 405, 418, 500]) {
    const r = await request({ method: 'POST', path: `/mcp?status=${code}`, headers: auth, body: 'x' });
    assert.equal(r.status, code);
  }
  const r = await request({ method: 'DELETE', path: '/mcp?status=405', headers: auth });
  assert.equal(r.status, 405);
});

test('proxy: SSE arrives incrementally', async () => {
  const start = Date.now();
  const timing = await new Promise((resolve, reject) => {
    const req = http.request(
      { host: '127.0.0.1', port: gwPort, method: 'POST', path: '/mcp?sse=1', headers: { host: '127.0.0.1:8768', ...auth } },
      (res) => {
        let first;
        const parts = [];
        res.on('data', (c) => {
          first ??= Date.now() - start;
          parts.push(c.toString());
        });
        res.on('end', () => resolve({ first, total: Date.now() - start, text: parts.join(''), type: res.headers['content-type'] }));
      },
    );
    req.on('error', reject);
    req.end('{}');
  });
  assert.equal(timing.type, 'text/event-stream');
  assert.ok(timing.text.includes('first') && timing.text.includes('second'));
  assert.ok(timing.first < timing.total - 150, `first chunk ${timing.first}ms vs total ${timing.total}ms`);
});

test('unknown paths are 404 (authenticated or not)', async () => {
  for (const path of ['/', '/other', '/mcp/extra', '/healthz/x', '/mcpx']) {
    assert.equal((await request({ path, headers: auth })).status, 404, path);
  }
});

test('oversize body: declared length and streamed -> 413', async () => {
  const big = 'x'.repeat(5000);
  const r = await request({ method: 'POST', path: '/mcp', headers: auth, body: big });
  assert.equal(r.status, 413);

  // No content-length (chunked): the streaming counter must trip.
  const status = await new Promise((resolve) => {
    const req = http.request(
      { host: '127.0.0.1', port: gwPort, method: 'POST', path: '/mcp', headers: { host: '127.0.0.1:8768', ...auth, 'transfer-encoding': 'chunked' } },
      (res) => {
        res.resume();
        resolve(res.statusCode);
      },
    );
    req.on('error', () => resolve('error'));
    req.write('y'.repeat(600));
    setTimeout(() => { req.write('y'.repeat(600)); req.end(); }, 50);
  });
  assert.equal(status, 413);
});

test('502 when the child dies mid-request, and when unreachable', async () => {
  const r = await request({ method: 'POST', path: '/mcp?die=1', headers: auth, body: '{}' });
  assert.equal(r.status, 502);
  assert.deepEqual(JSON.parse(r.text), { error: 'bad_gateway' });

  const tmp = await startFakeChild();
  const config = loadConfig({ MCP_AUTH_TOKEN: TOKEN, CHILD_PORT: String(tmp.port) });
  const g = createGateway({ config, isChildAlive: () => true, logger });
  await new Promise((res) => g.listen(0, '127.0.0.1', res));
  await stopServer(tmp.server);
  const status = await new Promise((resolve) => {
    const req = http.request(
      { host: '127.0.0.1', port: g.address().port, method: 'POST', path: '/mcp', headers: { host: '127.0.0.1:8768', ...auth } },
      (res) => { res.resume(); resolve(res.statusCode); },
    );
    req.end('{}');
  });
  await stopServer(g);
  assert.equal(status, 502);
});

test('client abort propagates to the child', async () => {
  const before = child.state.aborted;
  await new Promise((resolve) => {
    const req = http.request(
      { host: '127.0.0.1', port: gwPort, method: 'POST', path: '/mcp?hang=1', headers: { host: '127.0.0.1:8768', ...auth } },
      (res) => {
        res.once('data', () => {
          req.destroy();
          resolve();
        });
      },
    );
    req.on('error', () => {});
    req.end('{}');
  });
  for (let i = 0; i < 40 && child.state.aborted === before; i++) await new Promise((r) => setTimeout(r, 50));
  assert.equal(child.state.aborted, before + 1);
  assert.equal((await request({ path: '/healthz' })).status, 200);
});

test('declared oversize Content-Length: 413 and connection closed', async () => {
  const result = await new Promise((resolve) => {
    const sock = net.connect(gwPort, '127.0.0.1');
    let data = '';
    sock.on('data', (d) => { data += d; });
    sock.on('close', () => resolve(data));
    sock.write(
      ['POST /mcp HTTP/1.1', 'Host: 127.0.0.1:8768', `Authorization: Bearer ${TOKEN}`,
        'Content-Length: 99999999', '', ''].join('\r\n'),
    );
    setTimeout(() => sock.destroy(), 3000);
  });
  assert.match(result, /^HTTP\/1.1 413/);
  assert.match(result, /[Cc]onnection: close/);
});

test('/healthz allows GET and HEAD only', async () => {
  assert.equal((await request({ method: 'HEAD', path: '/healthz' })).status, 200);
  for (const method of ['POST', 'PUT', 'DELETE']) {
    const r = await request({ method, path: '/healthz', body: method === 'DELETE' ? undefined : 'x' });
    assert.equal(r.status, 405, method);
    assert.equal(r.headers.allow, 'GET, HEAD');
  }
});

test('no token configured: /mcp is open, Authorization still stripped, healthz and guards unchanged', async () => {
  const tmp = await startFakeChild();
  const config = loadConfig({ CHILD_PORT: String(tmp.port), PUBLIC_PORT: '8768' });
  assert.equal(config.authToken, undefined);
  const g = createGateway({ config, isChildAlive: () => true, logger });
  await new Promise((r) => g.listen(0, '127.0.0.1', r));
  const port = g.address().port;
  const get = (path, headers = {}, method = 'POST') => new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port, method, path, headers: { host: '127.0.0.1:8768', ...headers } }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({ status: res.statusCode, text: Buffer.concat(chunks).toString() }));
    });
    req.on('error', reject);
    req.end(method === 'POST' ? '{}' : undefined);
  });
  try {
    const open = await get('/mcp');
    assert.equal(open.status, 200);
    const withAuth = await get('/mcp', { authorization: 'Bearer something-the-client-sent' });
    assert.equal(withAuth.status, 200);
    assert.equal(JSON.parse(withAuth.text).headers.authorization, undefined);
    assert.equal((await get('/healthz', {}, 'GET')).status, 200);
    assert.equal((await get('/other')).status, 404);
    assert.equal((await get('/mcp', { host: 'evil.test' })).status, 421);
  } finally {
    await stopServer(g);
    await stopServer(tmp.server);
  }
});
