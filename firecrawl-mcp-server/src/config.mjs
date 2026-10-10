// The ONLY module that reads process.env. Errors name fields, never values.

export class ConfigError extends Error {}

const LOG_LEVELS = ['debug', 'info', 'warn', 'error'];

function intField(env, name, def, { min, max }) {
  const raw = env[name];
  if (raw === undefined || raw === '') return def;
  if (!/^\d+$/.test(raw)) throw new ConfigError(`${name} must be an integer`);
  const n = Number(raw);
  if (n < min || n > max) throw new ConfigError(`${name} must be between ${min} and ${max}`);
  return n;
}

function boolField(env, name, def) {
  const raw = env[name];
  if (raw === undefined || raw === '') return def;
  const v = raw.toLowerCase();
  if (v === 'true' || v === '1') return true;
  if (v === 'false' || v === '0') return false;
  throw new ConfigError(`${name} must be true or false`);
}

export function loadConfig(env = process.env) {
  // The token is OPTIONAL (spec amendment 1): unset or empty disables auth on /mcp.
  const token = env.MCP_AUTH_TOKEN ?? '';
  if (token) {
    if (token.startsWith('replace-me')) throw new ConfigError('MCP_AUTH_TOKEN still has its placeholder value');
    if (token.length < 32) throw new ConfigError('MCP_AUTH_TOKEN must be at least 32 characters');
  }

  const apiUrl = env.FIRECRAWL_API_URL || 'http://api:3002';
  let parsed;
  try {
    parsed = new URL(apiUrl);
  } catch {
    throw new ConfigError('FIRECRAWL_API_URL must be a valid http or https URL');
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    throw new ConfigError('FIRECRAWL_API_URL must use http or https');
  }
  if (parsed.username || parsed.password) {
    throw new ConfigError('FIRECRAWL_API_URL must not contain credentials');
  }

  const logLevel = (env.LOG_LEVEL || 'info').toLowerCase();
  if (!LOG_LEVELS.includes(logLevel)) {
    throw new ConfigError(`LOG_LEVEL must be one of ${LOG_LEVELS.join(', ')}`);
  }

  const childPort = intField(env, 'CHILD_PORT', 3000, { min: 1, max: 65535 });
  const apiFilterPort = intField(env, 'API_FILTER_PORT', 3001, { min: 1, max: 65535 });
  if (apiFilterPort === childPort) throw new ConfigError('API_FILTER_PORT must differ from CHILD_PORT');

  return Object.freeze({
    authToken: token || undefined,
    firecrawlApiUrl: apiUrl,
    firecrawlApiKey: env.FIRECRAWL_API_KEY || undefined,
    host: env.HOST || '0.0.0.0',
    port: intField(env, 'PORT', 8000, { min: 0, max: 65535 }),
    publicPort: intField(env, 'PUBLIC_PORT', 8768, { min: 1, max: 65535 }),
    childPort,
    apiFilterEnabled: boolField(env, 'API_FILTER_ENABLED', true),
    apiFilterPort,
    maxRequestBytes: intField(env, 'MAX_REQUEST_BYTES', 1048576, { min: 1, max: 1073741824 }),
    childStartTimeoutSeconds: intField(env, 'CHILD_START_TIMEOUT_SECONDS', 30, { min: 1, max: 600 }),
    logLevel,
  });
}
