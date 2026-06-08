import type { ServiceInfo, ServiceStatusType } from "@valuz/shared";
import { t } from "@valuz/shared/i18n";
import { WindowDragRegion } from "@valuz/ui";
import { useMemo } from "react";

interface StartupScreenProps {
  services: ServiceInfo[];
  logs: string[];
  loading: boolean;
  error: string | null;
  onRetry: () => Promise<void>;
}

// Self-contained boot splash. Pure CSS animations (no extra deps) — an
// aurora-tinted gradient drift behind the Valuz mark, an expanding scan
// ring, three orbiting particles, a flowing progress bar, and a strip of
// service status pills that glow as they come online. Designed to feel
// like the app is *waking up* rather than just printing log lines.
export const StartupScreen = ({
  error,
  loading,
  logs,
  onRetry,
  services,
}: StartupScreenProps) => {
  const total = services.length;
  const ready = services.filter((s) => s.status === "running").length;
  const erroring = services.filter((s) => s.status === "error").length;
  // Boot progress goes 5% → 100% even when there are no services yet, so
  // the bar animates instead of sitting at zero on a fast launch.
  const progress = useMemo(() => {
    if (total === 0) return loading ? 18 : 100;
    return Math.max(8, Math.round((ready / total) * 100));
  }, [ready, total, loading]);

  const tail = logs.slice(-3);

  return (
    <div className="splash-root">
      <style>{SPLASH_CSS}</style>
      <WindowDragRegion />


      {/* Layer 1 — animated aurora */}
      <div className="splash-aurora splash-aurora-a" aria-hidden />
      <div className="splash-aurora splash-aurora-b" aria-hidden />
      <div className="splash-aurora splash-aurora-c" aria-hidden />
      <div className="splash-grid" aria-hidden />
      <div className="splash-vignette" aria-hidden />

      {/* Layer 2 — content */}
      <div className="splash-content">
        <div className="splash-hero">
          {/* Logo + scan ring + orbiting particles */}
          <div className="splash-logo-wrap">
            <span className="splash-ring splash-ring-1" aria-hidden />
            <span className="splash-ring splash-ring-2" aria-hidden />
            <span className="splash-ring splash-ring-3" aria-hidden />
            <span
              className="splash-orbit"
              style={
                {
                  "--orbit-r": "78px",
                  "--orbit-d": "9s",
                } as React.CSSProperties
              }
              aria-hidden
            >
              <span className="splash-orbit-dot" />
            </span>
            <span
              className="splash-orbit"
              style={
                {
                  "--orbit-r": "104px",
                  "--orbit-d": "13s",
                  "--orbit-delay": "-3s",
                } as React.CSSProperties
              }
              aria-hidden
            >
              <span className="splash-orbit-dot splash-orbit-dot-cyan" />
            </span>
            <span
              className="splash-orbit splash-orbit-reverse"
              style={
                {
                  "--orbit-r": "130px",
                  "--orbit-d": "17s",
                  "--orbit-delay": "-7s",
                } as React.CSSProperties
              }
              aria-hidden
            >
              <span className="splash-orbit-dot splash-orbit-dot-violet" />
            </span>

            <div className="splash-logo">
              <img
                src="./logo.png"
                alt="Valuz"
                className="splash-logo-mark"
                draggable={false}
              />
            </div>
          </div>

          {/* Title with shimmer */}
          <div className="splash-title-wrap">
            <h1 className="splash-title">
              <span className="splash-title-shimmer">VALUZ</span>
            </h1>
            <p className="splash-subtitle">
              {error
                ? t("startup.error" as Parameters<typeof t>[0])
                : loading
                  ? t("startup.waking" as Parameters<typeof t>[0])
                  : t("startup.ready" as Parameters<typeof t>[0])}
            </p>
          </div>

          {/* Progress + service strip — only when the caller has actually
              passed services. Used by the App boot flow, where we want to
              surface per-service status. Pure-loading callers (e.g.
              ``DeepLinkRoot`` showing the splash while it makes setup
              probes) pass ``services={[]}`` and intentionally render only
              the logo + title + subtitle. */}
          {(services.length > 0 || tail.length > 0 || error) && (
            <div className="splash-progress-card">
              {services.length > 0 && (
                <>
                  <div className="splash-progress-row">
                    <span className="splash-progress-label">
                      BOOT&nbsp;
                      <span className="splash-progress-pct">
                        {String(progress).padStart(3, "0")}%
                      </span>
                    </span>
                    <span className="splash-progress-meta">
                      {ready}/{total} services
                      {erroring > 0 && (
                        <span className="splash-progress-err">
                          {" "}
                          · {erroring} err
                        </span>
                      )}
                    </span>
                  </div>
                  <div
                    className={`splash-progress-track${error ? " splash-progress-track-err" : ""}`}
                  >
                    <div
                      className="splash-progress-fill"
                      style={{ width: `${progress}%` }}
                    >
                      <span className="splash-progress-flow" />
                    </div>
                  </div>
                  <div className="splash-pills">
                    {services.map((s) => (
                      <span
                        key={s.name}
                        className={`splash-pill splash-pill-${pillVariant(s.status)}`}
                        title={s.detail ?? s.status}
                      >
                        <span className="splash-pill-dot" />
                        {s.name}
                      </span>
                    ))}
                  </div>
                </>
              )}

              {tail.length > 0 && (
                <ul className="splash-log">
                  {tail.map((line, i) => (
                    <li key={`${i}-${line}`} className="splash-log-line">
                      <span className="splash-log-caret">›</span>
                      <span className="splash-log-text">{line}</span>
                    </li>
                  ))}
                </ul>
              )}

              {error && (
                <div className="splash-error">
                  <p>{error}</p>
                  <button
                    type="button"
                    className="splash-retry"
                    onClick={() => void onRetry()}
                  >
                    Retry startup
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

const pillVariant = (status: ServiceStatusType): string => {
  switch (status) {
    case "running":
      return "ok";
    case "error":
      return "err";
    case "starting":
      return "go";
    default:
      return "off";
  }
};

const SPLASH_CSS = `
.splash-root {
  position: fixed;
  inset: 0;
  overflow: hidden;
  background: #F4F5F8;
  color: #1B1F2A;
  font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  -webkit-font-smoothing: antialiased;
}

/* Soft pale tints — barely there, just to give the white background a
   tiny bit of depth behind the frosted card. */
.splash-aurora {
  position: absolute;
  inset: -20%;
  filter: blur(90px);
  opacity: 0.45;
  pointer-events: none;
  border-radius: 50%;
}
.splash-aurora-a {
  background: radial-gradient(circle at 30% 30%, rgba(114, 92, 249, 0.18), transparent 60%);
  animation: splash-drift-a 20s ease-in-out infinite alternate;
}
.splash-aurora-b {
  background: radial-gradient(circle at 70% 60%, rgba(58, 149, 255, 0.14), transparent 60%);
  animation: splash-drift-b 24s ease-in-out infinite alternate;
}
.splash-aurora-c {
  display: none;
}
@keyframes splash-drift-a {
  from { transform: translate3d(-8%, -4%, 0) scale(1.05); }
  to   { transform: translate3d( 4%,  4%, 0) scale(1.15); }
}
@keyframes splash-drift-b {
  from { transform: translate3d( 4%,  2%, 0) scale(1.1); }
  to   { transform: translate3d(-4%, -4%, 0) scale(1); }
}
@keyframes splash-drift-c { from { opacity: 0; } to { opacity: 0; } }

.splash-grid { display: none; }
.splash-vignette { display: none; }

.splash-content {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  padding: 48px 24px;
}

.splash-hero {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 24px;
  width: min(420px, 100%);
}

/* Logo + rings + orbits */
.splash-logo-wrap {
  position: relative;
  width: 200px;
  height: 200px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.splash-logo {
  position: relative;
  width: 80px;
  height: 80px;
  border-radius: 22px;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(255, 255, 255, 0.6);
  box-shadow:
    0 0 0 1px rgba(17, 24, 39, 0.05),
    0 8px 24px rgba(114, 92, 249, 0.16);
  animation: splash-logo-breathe 4s ease-in-out infinite;
}
.splash-logo-mark {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  user-select: none;
  -webkit-user-drag: none;
}
@keyframes splash-logo-breathe {
  0%, 100% { transform: scale(1); }
  50%      { transform: scale(1.03); }
}

.splash-ring {
  position: absolute;
  border-radius: 50%;
  border: 1px solid rgba(114, 92, 249, 0.22);
  pointer-events: none;
  animation: splash-ring-pulse 4s ease-out infinite;
}
.splash-ring-1 { width: 110px; height: 110px; animation-delay: 0s; }
.splash-ring-2 { width: 110px; height: 110px; animation-delay: 1.3s; }
.splash-ring-3 { width: 110px; height: 110px; animation-delay: 2.6s; }
@keyframes splash-ring-pulse {
  0%   { transform: scale(0.75); opacity: 0.5; }
  60%  { opacity: 0.15; }
  100% { transform: scale(1.8); opacity: 0; }
}

.splash-orbit {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: none;
  animation: splash-orbit-spin var(--orbit-d, 12s) linear infinite;
  animation-delay: var(--orbit-delay, 0s);
}
.splash-orbit-reverse { animation-direction: reverse; }
.splash-orbit-dot {
  position: absolute;
  top: 50%;
  left: 50%;
  width: 5px;
  height: 5px;
  margin: -2.5px 0 0 calc(var(--orbit-r) - 2.5px);
  border-radius: 50%;
  background: rgba(114, 92, 249, 0.55);
}
.splash-orbit-dot-cyan {
  background: rgba(58, 149, 255, 0.55);
}
.splash-orbit-dot-violet {
  background: rgba(167, 139, 250, 0.4);
}
@keyframes splash-orbit-spin {
  from { transform: rotate(0deg); }
  to   { transform: rotate(360deg); }
}

/* Title */
.splash-title-wrap {
  text-align: center;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.splash-title {
  margin: 0;
  font-size: 30px;
  letter-spacing: 0.28em;
  font-weight: 700;
  line-height: 1;
  color: #1B1F2A;
}
.splash-title-shimmer {
  background: linear-gradient(
    90deg,
    rgba(27, 31, 42, 0.7) 0%,
    rgba(27, 31, 42, 1) 25%,
    rgba(114, 92, 249, 1) 50%,
    rgba(27, 31, 42, 1) 75%,
    rgba(27, 31, 42, 0.7) 100%
  );
  background-size: 240% 100%;
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  animation: splash-shimmer 7s linear infinite;
}
@keyframes splash-shimmer {
  from { background-position: 0% 50%; }
  to   { background-position: 240% 50%; }
}
.splash-subtitle {
  margin: 0;
  font-size: 13px;
  letter-spacing: 0.04em;
  color: rgba(75, 85, 99, 0.85);
}

/* Progress card — flat, integrated into hero */
.splash-progress-card {
  width: 100%;
  padding-top: 8px;
}
.splash-progress-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  font-size: 11px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: rgba(107, 114, 128, 0.85);
  margin-bottom: 8px;
}
.splash-progress-pct {
  color: #725CF9;
  font-variant-numeric: tabular-nums;
}
.splash-progress-meta { font-variant-numeric: tabular-nums; }
.splash-progress-err { color: #DC2626; }

.splash-progress-track {
  position: relative;
  height: 4px;
  border-radius: 999px;
  background: rgba(17, 24, 39, 0.06);
  overflow: hidden;
}
.splash-progress-track-err .splash-progress-fill {
  background: linear-gradient(90deg, #F87171, #FB923C);
}
.splash-progress-fill {
  position: relative;
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, #A78BFA, #725CF9);
  transition: width 0.6s cubic-bezier(0.22, 1, 0.36, 1);
}
.splash-progress-flow {
  position: absolute;
  inset: 0;
  background: linear-gradient(
    90deg,
    transparent 0%,
    rgba(255,255,255,0.65) 50%,
    transparent 100%
  );
  animation: splash-flow 1.8s linear infinite;
}
@keyframes splash-flow {
  from { transform: translateX(-100%); }
  to   { transform: translateX(100%); }
}

/* Pills */
.splash-pills {
  margin-top: 14px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.splash-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 10px;
  font-size: 11px;
  letter-spacing: 0.02em;
  border-radius: 999px;
  border: 1px solid rgba(17, 24, 39, 0.08);
  background: rgba(255, 255, 255, 0.7);
  color: rgba(75, 85, 99, 0.95);
}
.splash-pill-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: rgba(107, 114, 128, 0.4);
}
.splash-pill-ok {
  border-color: rgba(34, 197, 94, 0.25);
  background: rgba(220, 252, 231, 0.55);
  color: #166534;
}
.splash-pill-ok .splash-pill-dot {
  background: #22C55E;
  animation: splash-dot-glow 2.4s ease-in-out infinite;
}
.splash-pill-go {
  border-color: rgba(114, 92, 249, 0.25);
  background: rgba(237, 233, 254, 0.55);
  color: #5B21B6;
}
.splash-pill-go .splash-pill-dot {
  background: #725CF9;
  animation: splash-dot-blink 1.1s ease-in-out infinite;
}
.splash-pill-err {
  border-color: rgba(220, 38, 38, 0.3);
  background: rgba(254, 226, 226, 0.55);
  color: #991B1B;
}
.splash-pill-err .splash-pill-dot {
  background: #DC2626;
  animation: splash-dot-blink 0.8s ease-in-out infinite;
}
.splash-pill-pending,
.splash-pill-off {
  color: rgba(107, 114, 128, 0.7);
}
@keyframes splash-dot-glow {
  0%, 100% { opacity: 1; transform: scale(1); }
  50%      { opacity: 0.55; transform: scale(0.85); }
}
@keyframes splash-dot-blink {
  0%, 100% { opacity: 1; }
  50%      { opacity: 0.35; }
}

/* Log ticker */
.splash-log {
  margin: 14px 0 0 0;
  padding: 0;
  list-style: none;
  font-family: ui-monospace, "JetBrains Mono", "Menlo", monospace;
  font-size: 11px;
  color: rgba(107, 114, 128, 0.75);
}
.splash-log-line {
  display: flex;
  gap: 8px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  animation: splash-log-in 0.4s ease-out;
}
.splash-log-line + .splash-log-line { margin-top: 2px; }
.splash-log-caret { color: rgba(114, 92, 249, 0.7); }
.splash-log-text { overflow: hidden; text-overflow: ellipsis; }
@keyframes splash-log-in {
  from { opacity: 0; transform: translateY(4px); }
  to   { opacity: 1; transform: translateY(0); }
}

/* Error */
.splash-error {
  margin-top: 14px;
  padding: 10px 12px;
  border-radius: 10px;
  border: 1px solid rgba(220, 38, 38, 0.2);
  background: rgba(254, 226, 226, 0.5);
  color: #991B1B;
  font-size: 12px;
  line-height: 1.5;
}
.splash-error p { margin: 0 0 8px 0; }
.splash-retry {
  appearance: none;
  border: 1px solid rgba(220, 38, 38, 0.3);
  background: rgba(255, 255, 255, 0.8);
  color: #991B1B;
  padding: 6px 14px;
  border-radius: 8px;
  font-size: 11px;
  letter-spacing: 0.04em;
  cursor: pointer;
  transition: background 0.2s;
}
.splash-retry:hover {
  background: rgba(254, 226, 226, 0.8);
}

@media (prefers-reduced-motion: reduce) {
  .splash-aurora,
  .splash-logo,
  .splash-ring,
  .splash-orbit,
  .splash-title-shimmer,
  .splash-progress-flow,
  .splash-pill-dot,
  .splash-log-line {
    animation: none !important;
  }
}
`;
