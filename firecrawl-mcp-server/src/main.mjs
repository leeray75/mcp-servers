// Supervisor: spawns firecrawl-mcp (HTTP mode, loopback only) and the auth gateway.
import { spawn as nodeSpawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';
import { loadConfig, ConfigError } from './config.mjs';
import { makeLogger, makeRedactor } from './redact.mjs';
import { createGateway, probeChild } from './gateway.mjs';
import { createApiFilter } from './api-filter.mjs';

const CHILD_ENTRY = 'node_modules/firecrawl-mcp/dist/index.js';

export function childEnv(config, base = process.env) {
  const env = {
    PATH: base.PATH,
    NODE_ENV: 'production',
    HTTP_STREAMABLE_SERVER: 'true',
    HOST: '127.0.0.1',
    PORT: String(config.childPort),
    // With the filter on, the child talks to the loopback filter, which forwards to the real API.
    FIRECRAWL_API_URL: config.apiFilterEnabled
      ? `http://127.0.0.1:${config.apiFilterPort}`
      : config.firecrawlApiUrl,
    FIRECRAWL_NO_SEARCH_FEEDBACK: '1',
    FIRECRAWL_NO_ENDPOINT_FEEDBACK: '1',
  };
  if (config.firecrawlApiKey) env.FIRECRAWL_API_KEY = config.firecrawlApiKey;
  return env;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/**
 * Start everything. All side effects are injectable for tests.
 * Returns { server, child, shutdown }.
 */
export async function run({
  config,
  spawn = nodeSpawn,
  exit = (code) => process.exit(code),
  logger,
  probe = probeChild,
  pollMs = 250,
  killGraceMs = 5000,
  entry = CHILD_ENTRY,
  proc = process,
  createFilter = createApiFilter,
} = {}) {
  const redact = makeRedactor([config.authToken, config.firecrawlApiKey]);
  const log = logger ?? makeLogger({ level: config.logLevel, redact });

  let childAlive = false;
  let shuttingDown = false;
  let exited = false;
  const doExit = (code) => {
    if (exited) return;
    exited = true;
    exit(code);
  };

  if (!config.authToken) {
    log.warn('MCP_AUTH_TOKEN is not set: /mcp requires NO authentication. Keep the port bound to 127.0.0.1 only.');
  }

  // The filter must be listening before the child starts so its first API call cannot be refused.
  let filter;
  if (config.apiFilterEnabled) {
    filter = createFilter({
      upstreamUrl: config.firecrawlApiUrl, maxRequestBytes: config.maxRequestBytes, logger: log,
    });
    try {
      await new Promise((resolve, reject) => {
        filter.once('error', reject);
        filter.listen(config.apiFilterPort, '127.0.0.1', resolve);
      });
    } catch (err) {
      shuttingDown = true;
      filter.close();
      throw err;
    }
    log.info('api filter listening', { host: '127.0.0.1', port: config.apiFilterPort });
  }

  const child = spawn(process.execPath, [entry], {
    env: childEnv(config),
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  childAlive = true;

  for (const stream of [child.stdout, child.stderr]) {
    if (!stream) continue;
    createInterface({ input: stream }).on('line', (line) => {
      process.stdout.write(`${redact(`[firecrawl-mcp] ${line}`)}\n`);
    });
  }

  let childExited;
  const childExit = new Promise((resolve) => { childExited = resolve; });
  child.on('exit', (code, signal) => {
    childAlive = false;
    childExited();
    if (shuttingDown) return;
    log.error('firecrawl-mcp exited unexpectedly', { code, signal });
    server.close();
    filter?.close();
    doExit(1);
  });
  child.on('error', (err) => {
    childAlive = false;
    if (shuttingDown) return;
    log.error('failed to run firecrawl-mcp', { error: err.code ?? 'spawn_error' });
    doExit(1);
  });

  const server = createGateway({ config, isChildAlive: () => childAlive, logger: log });

  async function shutdown(signal = 'SIGTERM', code = 0) {
    if (shuttingDown) return;
    shuttingDown = true;
    log.info('shutting down', { signal });
    server.close();
    server.closeAllConnections?.();
    filter?.close();
    filter?.closeAllConnections?.();
    if (childAlive) {
      child.kill('SIGTERM');
      const timer = setTimeout(() => { if (childAlive) child.kill('SIGKILL'); }, killGraceMs);
      timer.unref?.();
      await childExit;
      clearTimeout(timer);
    }
    doExit(code);
  }

  // Register signal handlers now so a signal during startup still shuts the child down gracefully.
  for (const sig of ['SIGTERM', 'SIGINT']) proc.on(sig, () => shutdown(sig));

  try {
    await new Promise((resolve, reject) => {
      server.once('error', reject);
      server.listen(config.port, config.host, resolve);
    });
  } catch (err) {
    shuttingDown = true;
    filter?.close();
    if (childAlive) child.kill('SIGKILL');
    throw err;
  }
  log.info('gateway listening', { host: config.host, port: config.port });

  // Wait for the child to accept connections.
  const deadline = Date.now() + config.childStartTimeoutSeconds * 1000;
  let ready = false;
  while (Date.now() < deadline && childAlive && !shuttingDown) {
    if (await probe(config.childPort)) { ready = true; break; }
    await sleep(pollMs);
  }
  if (ready) {
    log.info('firecrawl-mcp ready');
  } else if (childAlive && !shuttingDown) {
    log.error('firecrawl-mcp did not become ready in time');
    await shutdown('startup-timeout', 1);
  }

  return { server, child, shutdown };
}

export async function main(env = process.env) {
  let config;
  try {
    config = loadConfig(env);
  } catch (err) {
    if (err instanceof ConfigError) {
      process.stderr.write(`configuration error: ${err.message}\n`);
      process.exit(2);
    }
    throw err;
  }
  await run({ config });
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  main().catch((err) => {
    process.stderr.write(`fatal: ${err.code ?? err.message}\n`);
    process.exit(1);
  });
}
