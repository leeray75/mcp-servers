// Bearer-token reverse proxy in front of firecrawl-mcp. Node built-ins only.
import http from 'node:http';
import crypto from 'node:crypto';

export const HOP_BY_HOP = new Set([
  'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te',
  'trailer', 'transfer-encoding', 'upgrade', 'proxy-connection',
]);

const sha256 = (s) => crypto.createHash('sha256').update(s).digest();

export function tokenMatches(header, token) {
  if (typeof header !== 'string') return false;
  const m = /^Bearer (.+)$/.exec(header);
  const presented = m ? m[1] : '';
  // Hash both sides so lengths are equal; always compare, even when the scheme is wrong.
  const ok = crypto.timingSafeEqual(sha256(presented), sha256(token));
  return Boolean(m) && ok;
}

export function allowedHosts(publicPort, port = 8000) {
  const hosts = new Set();
  for (const p of [publicPort, port]) {
    hosts.add(`127.0.0.1:${p}`);
    hosts.add(`localhost:${p}`);
    hosts.add(`[::1]:${p}`);
  }
  return hosts;
}

export function sendJson(res, status, body, extra = {}) {
  if (res.headersSent) {
    res.destroy();
    return;
  }
  const data = JSON.stringify(body);
  res.writeHead(status, {
    'Content-Type': 'application/json',
    'Content-Length': Buffer.byteLength(data),
    ...extra,
  });
  res.end(data);
}

export function probeChild(childPort, timeoutMs = 2000) {
  return new Promise((resolve) => {
    const req = http.request(
      { host: '127.0.0.1', port: childPort, path: '/mcp', method: 'GET', timeout: timeoutMs },
      (res) => {
        res.resume();
        resolve(true);
      },
    );
    req.on('timeout', () => req.destroy());
    req.on('error', () => resolve(false));
    req.end();
  });
}

/**
 * @param {object} opts
 * @param {object} opts.config  loaded config
 * @param {() => boolean} opts.isChildAlive
 * @param {object} opts.logger
 */
export function createGateway({ config, isChildAlive, logger }) {
  const hosts = allowedHosts(config.publicPort, config.port);

  async function handleHealth(res) {
    if (isChildAlive() && (await probeChild(config.childPort))) {
      sendJson(res, 200, { status: 'ok' });
    } else {
      sendJson(res, 503, { status: 'unavailable' });
    }
  }

  function proxy(req, res) {
    const headers = {};
    for (const [k, v] of Object.entries(req.headers)) {
      const key = k.toLowerCase();
      if (HOP_BY_HOP.has(key) || key === 'authorization' || key === 'host') continue;
      headers[k] = v;
    }
    headers.host = `127.0.0.1:${config.childPort}`;

    const declared = Number(req.headers['content-length']);
    if (Number.isFinite(declared) && declared > config.maxRequestBytes) {
      req.resume();
      res.once('finish', () => setTimeout(() => req.destroy(), 500).unref());
      sendJson(res, 413, { error: 'request_too_large' }, { Connection: 'close' });
      return;
    }

    let finished = false;
    let received = 0;
    const upstream = http.request(
      { host: '127.0.0.1', port: config.childPort, method: req.method, path: req.url, headers },
      (upRes) => {
        const out = {};
        for (const [k, v] of Object.entries(upRes.headers)) {
          if (!HOP_BY_HOP.has(k.toLowerCase())) out[k] = v;
        }
        res.writeHead(upRes.statusCode ?? 502, out);
        upRes.on('error', () => res.destroy());
        upRes.on('end', () => { finished = true; });
        upRes.pipe(res);
      },
    );
    upstream.on('error', () => {
      if (finished) return;
      finished = true;
      logger.warn('upstream error');
      sendJson(res, 502, { error: 'bad_gateway' });
    });
    res.on('close', () => {
      if (!res.writableFinished) upstream.destroy();
    });
    req.on('aborted', () => upstream.destroy());
    req.on('data', (chunk) => {
      received += chunk.length;
      if (received > config.maxRequestBytes && !finished) {
        finished = true;
        upstream.destroy();
        req.unpipe(upstream);
        req.resume();
        res.once('finish', () => setTimeout(() => req.destroy(), 500).unref());
        sendJson(res, 413, { error: 'request_too_large' }, { Connection: 'close' });
      }
    });
    req.pipe(upstream);
  }

  const server = http.createServer((req, res) => {
    if (!hosts.has(String(req.headers.host ?? '').toLowerCase())) {
      sendJson(res, 421, { error: 'misdirected_request' });
      req.resume();
      return;
    }
    let pathname;
    try {
      pathname = new URL(req.url, 'http://x').pathname;
    } catch {
      sendJson(res, 404, { error: 'not_found' });
      return;
    }
    if (pathname === '/healthz') {
      req.resume();
      if (req.method !== 'GET' && req.method !== 'HEAD') {
        sendJson(res, 405, { error: 'method_not_allowed' }, { Allow: 'GET, HEAD' });
        return;
      }
      handleHealth(res).catch(() => sendJson(res, 503, { status: 'unavailable' }));
      return;
    }
    if (pathname === '/mcp') {
      if (config.authToken && !tokenMatches(req.headers.authorization, config.authToken)) {
        sendJson(res, 401, { error: 'unauthorized' }, { 'WWW-Authenticate': 'Bearer' });
        req.resume();
        return;
      }
      proxy(req, res);
      return;
    }
    sendJson(res, 404, { error: 'not_found' });
    req.resume();
  });
  // Bounds slow request uploads only; long-lived SSE responses are unaffected.
  server.requestTimeout = 600000;
  server.headersTimeout = 30000;
  return server;
}
