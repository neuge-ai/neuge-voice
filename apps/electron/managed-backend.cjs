/**
 * Managed backend launcher helpers.
 * Protocol: docs/managed-backend-launcher.md
 */

const PORT_RE = /^PORT:(\d+)\s*$/;
const DEFAULT_TIMEOUT_MS = 15000;
const POLL_INTERVAL_MS = 200;

function parsePortLine(line) {
  const match = String(line).trim().match(PORT_RE);
  return match ? Number(match[1]) : null;
}

async function waitForBackendHealth(host, port, timeoutMs = DEFAULT_TIMEOUT_MS) {
  const deadline = Date.now() + timeoutMs;
  const url = `http://${host}:${port}/health`;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) {
        return;
      }
      lastError = `HTTP ${response.status}`;
    } catch (err) {
      lastError = String(err);
    }
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
  }
  throw new Error(`Backend at ${url} not healthy within ${timeoutMs}ms. Last error: ${lastError}`);
}

module.exports = {
  DEFAULT_TIMEOUT_MS,
  parsePortLine,
  waitForBackendHealth,
};
