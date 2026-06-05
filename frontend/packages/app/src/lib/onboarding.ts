// Persisted "the user has completed connection setup on the welcome page"
// marker. Once set, the setup gate (``useAppSetupReady``) stops bouncing to
// /welcome on every refresh — needed for subscription logins (claude/codex
// /login), whose credential lives in the CLI keychain and therefore can't be
// detected from the frontend (the providers API reports them as
// ``credential_source="none"``). Set it when the user actively picks a
// connection on /welcome; the gate honours it as the source of truth.
const ONBOARDED_KEY = "valuz-onboarded";

export function markOnboarded(): void {
  try {
    localStorage.setItem(ONBOARDED_KEY, "1");
  } catch {
    // localStorage may be unavailable (sandboxed / tests) — best-effort.
  }
}

export function isOnboarded(): boolean {
  try {
    return localStorage.getItem(ONBOARDED_KEY) === "1";
  } catch {
    return false;
  }
}

export function clearOnboarded(): void {
  try {
    localStorage.removeItem(ONBOARDED_KEY);
  } catch {
    // best-effort
  }
}
