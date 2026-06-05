/**
 * Playwright global setup that primes the backend so the desktop shell
 * skips the first-run onboarding redirect.
 *
 * The shell's startup gate (apps/desktop/src/renderer/routes/router.tsx)
 * only lets the user past `/welcome` when:
 *
 * - `GET /v1/providers` returns at least one provider with
 *   `enabled && credential_source !== 'none'`.
 *
 * The cleanest non-invasive way to satisfy that on a fresh DB is to
 * `POST /v1/providers` with a placeholder API key — that yields
 * `credential_source = 'secret_ref'`. Idempotent: if a same-named provider
 * already exists, the create returns 4xx and we treat it as "already seeded".
 *
 * Set `VALUZ_E2E_API_BASE` to point at the backend (default: localhost:8000).
 * Set `VALUZ_E2E_SKIP_SEED=1` to bypass the seed entirely.
 */

const apiBase = process.env.VALUZ_E2E_API_BASE ?? "http://127.0.0.1:8000";

async function ensureProviderExists(): Promise<void> {
  const listRes = await fetch(`${apiBase}/v1/providers`);
  if (!listRes.ok) return;
  const { providers } = (await listRes.json()) as {
    providers: Array<{
      id: string;
      enabled: boolean;
      credential_source: string;
    }>;
  };
  const ready = providers.some(
    (c) => c.enabled && c.credential_source !== "none",
  );
  if (ready) return;

  await fetch(`${apiBase}/v1/providers`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: "E2E Test Provider",
      provider_kind: "anthropic",
      base_url: "https://api.anthropic.com",
      api_key: "sk-ant-e2e-placeholder",
      default_model: "claude-sonnet-4-6",
      protocol: "anthropic",
    }),
  }).catch(() => {
    // Tolerated — if backend rejects the duplicate, the gate may already pass.
  });
}

export default async function globalSetup(): Promise<void> {
  if (process.env.VALUZ_E2E_SKIP_SEED === "1") return;
  await ensureProviderExists();
}
