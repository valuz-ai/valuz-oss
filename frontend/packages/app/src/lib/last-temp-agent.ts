// Remembers the agent the user last started a 临时对话 with, so the next new
// conversation pre-selects it instead of just falling back to the first library
// agent (10-new-conversation-guidance slice 3). Per-device (localStorage); the
// caller validates the slug still exists in "我的" before honouring it, so a
// deleted agent self-heals to the first-agent fallback.
const LAST_TEMP_AGENT_KEY = "valuz-last-temp-agent";

export function getLastTempAgent(): string | null {
  try {
    return localStorage.getItem(LAST_TEMP_AGENT_KEY) || null;
  } catch {
    // localStorage may be unavailable (sandboxed / tests) — best-effort.
    return null;
  }
}

export function setLastTempAgent(slug: string): void {
  try {
    localStorage.setItem(LAST_TEMP_AGENT_KEY, slug);
  } catch {
    // best-effort
  }
}
