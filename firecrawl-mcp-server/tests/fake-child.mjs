// Fake firecrawl-mcp child used by the gateway tests. Listens on 127.0.0.1.
import http from 'node:http';

export async function startFakeChild(port = 0) {
  const state = { aborted: 0, requests: [] };
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://x');
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => {
      const body = Buffer.concat(chunks).toString();
      state.requests.push({ method: req.method, url: req.url, headers: req.headers, body });
      if (url.searchParams.has('status')) {
        res.writeHead(Number(url.searchParams.get('status')), { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ q: url.search }));
        return;
      }
      if (url.searchParams.has('sse')) {
        res.writeHead(200, { 'Content-Type': 'text/event-stream' });
        res.write('data: first\n\n');
        setTimeout(() => {
          res.write('data: second\n\n');
          res.end();
        }, 400);
        return;
      }
      if (url.searchParams.has('hang')) {
        res.writeHead(200, { 'Content-Type': 'text/event-stream' });
        res.write('data: start\n\n');
        res.on('close', () => { state.aborted += 1; });
        return;
      }
      if (url.searchParams.has('die')) {
        req.socket.destroy();
        return;
      }
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ method: req.method, url: req.url, headers: req.headers, body }));
    });
  });
  await new Promise((resolve) => server.listen(port, '127.0.0.1', resolve));
  return { server, state, port: server.address().port };
}

export const stopServer = (server) =>
  new Promise((resolve) => {
    server.closeAllConnections?.();
    server.close(() => resolve());
  });
