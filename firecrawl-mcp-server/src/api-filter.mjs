// Loopback request filter between firecrawl-mcp and the self-hosted Firecrawl API. Node built-ins only.
// firecrawl-mcp 3.27.3 always adds the cloud-only search fields below; the self-hosted API rejects them
// (400 unrecognized_keys). They are removed from search bodies; everything else is streamed through untouched.
import http from 'node:http';
import https from 'node:https';
import { HOP_BY_HOP, sendJson } from './gateway.mjs';

const SEARCH_PATHS = new Set(['/v2/search', '/v1/search']);
const STRIPPED_KEYS = ['domainTools', 'toolDetail'];

/** Returns { body, removed } for a search body; the original buffer and [] when it is not a JSON object. */
export function filterSearchBody(buf) {
  let parsed;
  try {
    parsed = JSON.parse(buf.toString('utf8'));
  } catch {
    return { body: buf, removed: [] };
  }
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) return { body: buf, removed: [] };
  const removed = STRIPPED_KEYS.filter((k) => Object.hasOwn(parsed, k));
  if (removed.length === 0) return { body: buf, removed };
  for (const k of removed) delete parsed[k];
  return { body: Buffer.from(JSON.stringify(parsed)), removed };
}

/**
 * @param {object} opts
 * @param {string} opts.upstreamUrl  the real Firecrawl API (origin plus optional path prefix)
 * @param {number} opts.maxRequestBytes  cap for buffered (search) bodies only
 * @param {object} opts.logger
 * @returns {http.Server} not yet listening
 */
export function createApiFilter({ upstreamUrl, maxRequestBytes, logger }) {
  const target = new URL(upstreamUrl);
  const transport = target.protocol === 'https:' ? https : http;
  const prefix = target.pathname.replace(/\/+$/, '');

  function forward(req, res, body) {
    const headers = {};
    for (const [k, v] of Object.entries(req.headers)) {
      const key = k.toLowerCase();
      if (HOP_BY_HOP.has(key) || key === 'host') continue;
      if (body && key === 'content-length') continue;
      headers[k] = v;
    }
    headers.host = target.host;
    if (body) headers['content-length'] = String(body.length);

    let finished = false;
    const upstream = transport.request(
      {
        protocol: target.protocol,
        hostname: target.hostname,
        port: target.port || undefined,
        method: req.method,
        path: prefix + req.url,
        headers,
      },
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
      logger.warn('api upstream error');
      sendJson(res, 502, { error: 'bad_gateway' });
    });
    res.on('close', () => {
      if (!res.writableFinished) upstream.destroy();
    });
    req.on('aborted', () => upstream.destroy());
    if (body) upstream.end(body);
    else req.pipe(upstream);
  }

  function tooLarge(req, res) {
    req.resume();
    res.once('finish', () => setTimeout(() => req.destroy(), 500).unref());
    sendJson(res, 413, { error: 'request_too_large' }, { Connection: 'close' });
  }

  function filterSearch(req, res) {
    const declared = Number(req.headers['content-length']);
    if (Number.isFinite(declared) && declared > maxRequestBytes) {
      tooLarge(req, res);
      return;
    }
    const chunks = [];
    let received = 0;
    let rejected = false;
    req.on('data', (chunk) => {
      if (rejected) return;
      received += chunk.length;
      if (received > maxRequestBytes) {
        rejected = true;
        tooLarge(req, res);
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => {
      if (rejected) return;
      const { body, removed } = filterSearchBody(Buffer.concat(chunks));
      if (removed.length) logger.debug('removed unsupported search keys', { keys: removed });
      forward(req, res, body);
    });
    req.on('error', () => res.destroy());
  }

  const server = http.createServer((req, res) => {
    let pathname = '';
    try {
      pathname = new URL(req.url, 'http://x').pathname;
    } catch {
      // Unparseable target: pass it through and let the API answer.
    }
    if (req.method === 'POST' && SEARCH_PATHS.has(pathname)) filterSearch(req, res);
    else forward(req, res, null);
  });
  // No proxy-imposed timeouts: scrapes and crawls can run for minutes.
  server.requestTimeout = 0;
  // Longer than any client-side idle socket timeout (Node's default agent drops idle sockets after ~5 s), so the
  // child never reuses a keep-alive socket the filter has just closed (ECONNRESET race).
  server.keepAliveTimeout = 65000;
  // Node 22 requires headersTimeout > keepAliveTimeout (otherwise idle keep-alive sockets can be cut early).
  server.headersTimeout = server.keepAliveTimeout + 1000;
  return server;
}
