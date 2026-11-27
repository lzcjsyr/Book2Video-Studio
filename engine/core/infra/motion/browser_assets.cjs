// Serve only files confined to a composition and block external requests.
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');

const types = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.mjs': 'text/javascript',
  '.json': 'application/json', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png',
  '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.woff2': 'font/woff2',
  '.mp4': 'video/mp4', '.webm': 'video/webm',
};

function asset(root, pathname, rangeHeader) {
  if (pathname === '/favicon.ico') return {status: 204};
  try {
    const file = fs.realpathSync(path.resolve(root, '.' + decodeURIComponent(pathname)));
    if (!file.startsWith(root + path.sep)) return {status: 403};
    const stat = fs.statSync(file);
    if (!stat.isFile()) return {status: 404};
    const headers = {
      'Content-Type': types[path.extname(file).toLowerCase()] || 'application/octet-stream',
      'Accept-Ranges': 'bytes',
    };
    const range = /^bytes=(\d+)-(\d*)$/.exec(rangeHeader || '');
    if (!range) return {status: 200, headers: {...headers, 'Content-Length': stat.size}, file};
    const start = Number(range[1]);
    const end = Math.min(stat.size - 1, range[2] ? Number(range[2]) : stat.size - 1);
    if (start > end) return {status: 416, headers: {'Content-Range': `bytes */${stat.size}`}};
    return {
      status: 206, file, range: {start, end},
      headers: {...headers, 'Content-Range': `bytes ${start}-${end}/${stat.size}`, 'Content-Length': end - start + 1},
    };
  } catch {
    return {status: 404};
  }
}

async function openAssets(work) {
  const root = fs.realpathSync(work);
  const server = http.createServer((request, response) => {
    const pathname = new URL(request.url, 'http://localhost').pathname;
    const result = asset(root, pathname, request.headers.range);
    response.writeHead(result.status, result.headers);
    if (!result.file || request.method === 'HEAD') response.end();
    else fs.createReadStream(result.file, result.range).on('error', () => response.destroy()).pipe(response);
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const origin = `http://127.0.0.1:${server.address().port}`;
  return {
    origin,
    async attach(page, errors) {
      await page.setRequestInterception(true);
      page.on('request', async request => {
        try {
          const url = new URL(request.url());
          if (['data:', 'blob:'].includes(url.protocol)) await request.continue();
          else if (url.origin !== origin) {
            errors.push(`External resource blocked: ${url.href}`);
            await request.abort();
          } else await request.continue();
        } catch (error) {
          errors.push(String(error));
          if (!request.isInterceptResolutionHandled()) await request.abort().catch(() => {});
        }
      });
    },
    async close() {
      if (server.listening) await new Promise(resolve => server.close(resolve));
    },
  };
}

module.exports = { openAssets };
