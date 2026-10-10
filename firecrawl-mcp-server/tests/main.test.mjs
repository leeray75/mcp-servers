import { test } from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { Readable } from 'node:stream';
import http from 'node:http';
import { run, childEnv } from '../src/main.mjs';
import { loadConfig } from '../src/config.mjs';
import { makeLogger } from '../src/redact.mjs';

const TOKEN = 'sup_' + 'k'.repeat(40);
const logger = makeLogger({ level: 'error', write: () => {} });

function fakeSpawn() {
  const calls = [];
  const spawn = (cmd, args, opts) => {
    const child = new EventEmitter();
    child.stdout = Readable.from(['hello ' + TOKEN + '\n']);
    child.stderr = Readable.from([]);
    child.kills = [];
    child.kill = (sig) => {
      child.kills.push(sig);
      setImmediate(() => child.emit('exit', null, sig));
    };
    calls.push({ cmd, args, opts, child });
    return child;
  };
  return { spawn, calls };
}

const cfg = (extra = {}) => loadConfig({
  MCP_AUTH_TOKEN: TOKEN, HOST: '127.0.0.1', PORT: '0', API_FILTER_ENABLED: 'false', ...extra,
});

test('childEnv: loopback, http mode, key only when set, no token', () => {
  const env = childEnv(cfg({ FIRECRAWL_API_KEY: 'fc-abc' }), { PATH: '/bin', MCP_AUTH_TOKEN: TOKEN });
  assert.equal(env.HTTP_STREAMABLE_SERVER, 'true');
  assert.equal(env.HOST, '127.0.0.1');
  assert.equal(env.PORT, '3000');
  assert.equal(env.FIRECRAWL_API_URL, 'http://api:3002');
  assert.equal(env.FIRECRAWL_NO_SEARCH_FEEDBACK, '1');
  assert.equal(env.FIRECRAWL_API_KEY, 'fc-abc');
  assert.equal(env.MCP_AUTH_TOKEN, undefined);
  assert.equal(childEnv(cfg(), { PATH: '/bin' }).FIRECRAWL_API_KEY, undefined);
});

test('childEnv: points at the loopback filter when enabled, at the real API when disabled', () => {
  const on = childEnv(cfg({ API_FILTER_ENABLED: 'true', API_FILTER_PORT: '3555', FIRECRAWL_API_URL: 'http://real:3002' }), { PATH: '/bin' });
  assert.equal(on.FIRECRAWL_API_URL, 'http://127.0.0.1:3555');
  const off = childEnv(cfg({ API_FILTER_ENABLED: 'false', FIRECRAWL_API_URL: 'http://real:3002' }), { PATH: '/bin' });
  assert.equal(off.FIRECRAWL_API_URL, 'http://real:3002');
});

function fakeFilter({ failListen = false } = {}) {
  const f = new EventEmitter();
  f.events = [];
  f.listen = (port, host, cb) => {
    f.events.push(['listen', port, host]);
    if (failListen) setImmediate(() => f.emit('error', Object.assign(new Error('in use'), { code: 'EADDRINUSE' })));
    else setImmediate(cb);
  };
  f.close = () => f.events.push(['close']);
  f.closeAllConnections = () => f.events.push(['closeAll']);
  return f;
}

test('filter listens on loopback before the child is spawned; shutdown closes it', async () => {
  const { spawn, calls } = fakeSpawn();
  const filter = fakeFilter();
  let spawnedAfterListen = false;
  const wrapped = (...a) => { spawnedAfterListen = filter.events.some((e) => e[0] === 'listen'); return spawn(...a); };
  const { shutdown } = await run({
    config: cfg({ API_FILTER_ENABLED: 'true', API_FILTER_PORT: '3555' }), spawn: wrapped, exit: () => {}, logger,
    proc: new EventEmitter(), probe: async () => true, pollMs: 5, createFilter: () => filter,
  });
  assert.ok(spawnedAfterListen);
  assert.deepEqual(filter.events[0], ['listen', 3555, '127.0.0.1']);
  assert.equal(calls[0].opts.env.FIRECRAWL_API_URL, 'http://127.0.0.1:3555');
  await shutdown();
  assert.ok(filter.events.some((e) => e[0] === 'close'));
  assert.ok(filter.events.some((e) => e[0] === 'closeAll'));
});

test('filter listen failure rejects without spawning a child', async () => {
  const { spawn, calls } = fakeSpawn();
  const filter = fakeFilter({ failListen: true });
  await assert.rejects(
    run({
      config: cfg({ API_FILTER_ENABLED: 'true' }), spawn, exit: () => {}, logger, proc: new EventEmitter(),
      createFilter: () => filter,
    }),
    /in use/,
  );
  assert.equal(calls.length, 0);
  assert.ok(filter.events.some((e) => e[0] === 'close'));
});

test('gateway listen failure also closes the filter', async () => {
  const net = await import('node:net');
  const blocker = net.createServer().listen(0, '127.0.0.1');
  await new Promise((r) => blocker.once('listening', r));
  const { spawn } = fakeSpawn();
  const filter = fakeFilter();
  await assert.rejects(
    run({
      config: cfg({ API_FILTER_ENABLED: 'true', PORT: String(blocker.address().port) }), spawn, exit: () => {}, logger,
      proc: new EventEmitter(), createFilter: () => filter,
    }),
    /EADDRINUSE/,
  );
  blocker.close();
  assert.ok(filter.events.some((e) => e[0] === 'close'));
});

test('child exit closes the filter; no filter server when disabled', async () => {
  const { spawn, calls } = fakeSpawn();
  const filter = fakeFilter();
  const { server } = await run({
    config: cfg({ API_FILTER_ENABLED: 'true' }), spawn, exit: () => {}, logger, proc: new EventEmitter(),
    probe: async () => true, pollMs: 5, createFilter: () => filter,
  });
  calls[0].child.emit('exit', 1, null);
  assert.ok(filter.events.some((e) => e[0] === 'close'));
  server.close();
  let created = false;
  const h = await run({
    config: cfg(), spawn: fakeSpawn().spawn, exit: () => {}, logger, proc: new EventEmitter(),
    probe: async () => true, pollMs: 5, createFilter: () => { created = true; return fakeFilter(); },
  });
  await h.shutdown();
  assert.equal(created, false);
});

test('real filter server binds loopback and answers', async () => {
  const net = await import('node:net');
  const probeSrv = net.createServer().listen(0, '127.0.0.1');
  await new Promise((r) => probeSrv.once('listening', r));
  const free = probeSrv.address().port;
  await new Promise((r) => probeSrv.close(r));
  const { spawn } = fakeSpawn();
  const h = await run({
    config: cfg({ API_FILTER_ENABLED: 'true', API_FILTER_PORT: String(free), FIRECRAWL_API_URL: 'http://127.0.0.1:1' }),
    spawn, exit: () => {}, logger, proc: new EventEmitter(), probe: async () => true, pollMs: 5,
  });
  const status = await new Promise((resolve, reject) => {
    http.get({ host: '127.0.0.1', port: free, path: '/v2/map' }, (res) => { res.resume(); resolve(res.statusCode); }).on('error', reject);
  });
  assert.equal(status, 502);
  await h.shutdown();
});

test('exits non-zero when the child exits unexpectedly', async () => {
  const { spawn, calls } = fakeSpawn();
  const codes = [];
  const { server } = await run({
    config: cfg(), spawn, exit: (c) => codes.push(c), logger, probe: async () => true, pollMs: 5,
  });
  calls[0].child.emit('exit', 1, null);
  assert.deepEqual(codes, [1]);
  assert.ok(calls[0].opts.env.HTTP_STREAMABLE_SERVER);
  server.close();
});

test('SIGTERM path: kills child with SIGTERM, exits 0 once', async () => {
  const { spawn, calls } = fakeSpawn();
  const codes = [];
  const { shutdown } = await run({
    config: cfg(), spawn, exit: (c) => codes.push(c), logger, probe: async () => true, pollMs: 5,
  });
  await shutdown('SIGTERM');
  await shutdown('SIGTERM');
  assert.deepEqual(calls[0].child.kills, ['SIGTERM']);
  assert.deepEqual(codes, [0]);
});

test('spawn error exits non-zero', async () => {
  const { spawn, calls } = fakeSpawn();
  const codes = [];
  const { server } = await run({
    config: cfg(), spawn, exit: (c) => codes.push(c), logger, probe: async () => true, pollMs: 5,
  });
  calls[0].child.emit('error', Object.assign(new Error('x'), { code: 'ENOENT' }));
  assert.deepEqual(codes, [1]);
  server.close();
});

test('startup timeout shuts down and exits non-zero', async () => {
  const { spawn } = fakeSpawn();
  const codes = [];
  await run({
    config: cfg({ CHILD_START_TIMEOUT_SECONDS: '1' }), spawn, exit: (c) => codes.push(c), logger,
    probe: async () => false, pollMs: 20,
  });
  assert.deepEqual(codes, [1]);
});

test('child output is redacted and prefixed', async () => {
  const { spawn } = fakeSpawn();
  const writes = [];
  const orig = process.stdout.write.bind(process.stdout);
  process.stdout.write = (s, ...rest) => {
    writes.push(String(s));
    return orig(s, ...rest);
  };
  let handle;
  try {
    handle = await run({ config: cfg(), spawn, exit: () => {}, logger, probe: async () => true, pollMs: 5 });
    await new Promise((r) => setTimeout(r, 30));
  } finally {
    process.stdout.write = orig;
  }
  const out = writes.join('');
  assert.ok(out.includes('[firecrawl-mcp] hello [REDACTED]'));
  assert.ok(!out.includes(TOKEN));
  await handle.shutdown();
});

test('signal during startup shuts the child down gracefully', async () => {
  const { spawn, calls } = fakeSpawn();
  const codes = [];
  const proc = new EventEmitter();
  const p = run({
    config: cfg(), spawn, exit: (c) => codes.push(c), logger, proc, pollMs: 10,
    probe: async () => { proc.emit('SIGTERM'); return false; },
  });
  await p;
  await new Promise((r) => setTimeout(r, 30));
  assert.deepEqual(calls[0].child.kills, ['SIGTERM']);
  assert.deepEqual(codes, [0]);
});

test('listen failure kills the already-spawned child and rejects', async () => {
  const net = await import('node:net');
  const blocker = net.createServer().listen(0, '127.0.0.1');
  await new Promise((r) => blocker.once('listening', r));
  const { spawn, calls } = fakeSpawn();
  const codes = [];
  await assert.rejects(
    run({ config: cfg({ PORT: String(blocker.address().port) }), spawn, exit: (c) => codes.push(c), logger, proc: new EventEmitter() }),
    /EADDRINUSE/,
  );
  blocker.close();
  assert.deepEqual(calls[0].child.kills, ['SIGKILL']);
});

test('startup warning is logged only when no token is configured', async () => {
  for (const [env, expectWarn] of [[{}, true], [{ MCP_AUTH_TOKEN: TOKEN }, false]]) {
    const { spawn } = fakeSpawn();
    const lines = [];
    const lg = makeLogger({ level: 'info', write: (l) => lines.push(l) });
    const config = loadConfig({ HOST: '127.0.0.1', PORT: '0', API_FILTER_ENABLED: 'false', ...env });
    const { shutdown } = await run({
      config, spawn, exit: () => {}, logger: lg, proc: new EventEmitter(), probe: async () => true, pollMs: 5,
    });
    await shutdown();
    const warned = lines.some((l) => JSON.parse(l).level === 'warn' && /MCP_AUTH_TOKEN is not set/.test(l));
    assert.equal(warned, expectWarn);
  }
});
