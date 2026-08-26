const allowlistedRecord = (
  value: unknown,
  keys: readonly string[],
): Record<string, unknown> => {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return {};
  }
  const source = value as Record<string, unknown>;
  return Object.fromEntries(
    keys.filter((key) => key in source).map((key) => [key, source[key]]),
  );
};

/** Build a second, explicit allowlist before diagnostics leave the UI. */
export const buildEgressDiagnosticsExport = (
  status: unknown,
  snapshots: unknown[],
  diagnostics: unknown[],
  runtimePhases: unknown[],
) => {
  const aliases = (prefix: string) => {
    const values = new Map<string, string>();
    return (value: unknown): string | undefined => {
      if (typeof value !== "string" || !value) return undefined;
      let alias = values.get(value);
      if (!alias) {
        alias = `${prefix}-${values.size + 1}`;
        values.set(value, alias);
      }
      return alias;
    };
  };
  const runtimeRef = aliases("runtime");
  const attemptRef = aliases("attempt");
  const turnRef = aliases("turn");
  const withAlias = (
    item: unknown,
    keys: readonly string[],
    references: Record<string, string | undefined>,
  ) => ({
    ...allowlistedRecord(item, keys),
    ...Object.fromEntries(
      Object.entries(references).filter((entry) => entry[1] !== undefined),
    ),
  });
  const source = (item: unknown): Record<string, unknown> =>
    typeof item === "object" && item !== null && !Array.isArray(item)
      ? (item as Record<string, unknown>)
      : {};

  return {
    status: allowlistedRecord(status, [
      "mode",
      "enabled",
      "started",
      "emergencyOverride",
      "snapshotCount",
      "diagnosticEventCount",
      "lastErrorCode",
    ]),
    snapshots: snapshots.map((item) => {
      const raw = source(item);
      return withAlias(
        item,
        [
          "runtime",
          "frontend",
          "targetOrigin",
          "mode",
          "route",
          "health",
          "source",
          "redactedProxy",
          "resolveMs",
          "connectMs",
          "responseStatus",
          "responseMs",
          "firstByteMs",
          "totalMs",
          "reconnectCount",
          "fallbackCount",
          "lastErrorCode",
          "updatedAt",
        ],
        { runtimeRef: runtimeRef(raw.clientId) },
      );
    }),
    diagnostics: diagnostics.map((item) => {
      const raw = source(item);
      return withAlias(
        item,
        [
          "event",
          "runtime",
          "frontend",
          "targetOrigin",
          "mode",
          "timestamp",
          "resolveMs",
          "route",
          "source",
          "redactedProxy",
          "candidateCount",
          "errorCode",
          "candidateIndex",
          "connectMs",
          "fallbackCount",
          "statusCode",
          "responseMs",
          "firstByteMs",
          "totalMs",
        ],
        {
          runtimeRef: runtimeRef(raw.clientId),
          attemptRef: attemptRef(raw.connectionAttemptId),
        },
      );
    }),
    runtimePhases: runtimePhases.map((item) => {
      const raw = source(item);
      return withAlias(item, ["phase", "monotonicMs", "observedAt"], {
        runtimeRef: runtimeRef(raw.clientId),
        turnRef: turnRef(raw.turnAttemptId),
      });
    }),
  };
};
