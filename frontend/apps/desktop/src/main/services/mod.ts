import crypto from "node:crypto";
import {
  PERSONAL_PORTS,
  type CraftServerInfo,
  type ServiceInfo,
  type ServiceStatusType,
} from "@valuz/shared";
import type { ServiceDescriptor } from "@valuz/core";
import type { EgressManager } from "@valuz/desktop-network-egress/main";
import type {
  EgressBootstrap,
  EgressDiagnosticEvent,
  EgressManagerStatus,
  EgressMode,
  EgressSnapshot,
  RuntimePhaseRecord,
} from "@valuz/desktop-network-egress/contracts";
import { DescriptorRegistry, personalDescriptors } from "./descriptors";
import {
  reclaimStaleSidecar,
  resolveSidecarDataDir,
  startSidecar,
  type DesktopSidecarResult,
} from "./sidecar";
import { recordSidecarLine } from "./system-logs";

const AGENT_SERVER_DETAIL = "Primary local agent runtime";
const HEALTH_CHECK_INTERVAL_MS = 500;
// The desktop no longer hard-fails the splash at a fixed deadline: a cold
// backend (first-run unpack, alembic migration, slow disk) can legitimately
// take a while. We keep probing as long as the sidecar process is alive and
// surface an error the moment it actually exits — or, as a last-resort
// backstop, after this generous absolute cap.
const HEALTH_SLOW_HINT_MS = 15_000;
const HEALTH_HARD_CAP_MS = 180_000;
// Dev mode probes an externally-run backend (no child process to watch), so it
// keeps a bounded wait with a "start it yourself" hint.
const DEV_HEALTH_TIMEOUT_MS = 60_000;

export interface DesktopServiceManager {
  descriptors: DescriptorRegistry;
  startAllServices(): Promise<ServiceInfo[]>;
  stopAllServices(): Promise<ServiceInfo[]>;
  restartService(name: string): Promise<ServiceInfo[]>;
  getLogs(name: string): string[];
  getAgentServerInfo(): CraftServerInfo;
  getDesktopControlToken(): string;
  getShellStatus(): { ready: boolean };
  getAllStatus(): ServiceInfo[];
  registerDescriptor(descriptor: ServiceDescriptor): ServiceDescriptor;
  unregisterDescriptor(name: string): boolean;
  getEgressDiagnostics(): EgressDiagnosticEvent[];
  getEgressSnapshots(): EgressSnapshot[];
  getEgressMode(): EgressMode;
  getEgressStatus(): EgressManagerStatus;
  getEgressRuntimePhases(): RuntimePhaseRecord[];
  getEgressBootstrap?(): EgressBootstrap | null;
  setEgressMode(mode: EgressMode): Promise<EgressManagerStatus>;
}

const formatLogLine = (line: string) => {
  const timestamp = new Date().toISOString().slice(11, 23);
  return `[${timestamp}] ${line}`;
};

type HealthResult = "healthy" | "exited" | "timeout";

/**
 * Probe the backend health endpoint until it responds, the sidecar process
 * exits, or a hard cap elapses.
 *
 * Returns ``"healthy"`` on a 200, ``"exited"`` when ``isAlive`` reports the
 * child process is gone (a real crash — fail fast instead of waiting out the
 * cap), or ``"timeout"`` if neither happens within ``hardCapMs``.
 */
const waitForHealth = async (
  port: number,
  opts: {
    isAlive?: () => boolean;
    hardCapMs?: number;
    onSlow?: () => void;
  } = {},
): Promise<HealthResult> => {
  const start = Date.now();
  const cap = opts.hardCapMs ?? HEALTH_HARD_CAP_MS;
  let slowFired = false;
  while (Date.now() - start < cap) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/v1/projects`);
      if (res.ok) return "healthy";
    } catch {
      // Not ready yet
    }
    // The process died before it ever served — surface the failure now.
    if (opts.isAlive && !opts.isAlive()) return "exited";
    if (
      !slowFired &&
      opts.onSlow &&
      Date.now() - start >= HEALTH_SLOW_HINT_MS
    ) {
      slowFired = true;
      opts.onSlow();
    }
    await new Promise((r) => setTimeout(r, HEALTH_CHECK_INTERVAL_MS));
  }
  return "timeout";
};

export const createServiceManager = (
  appDataDir = process.cwd(),
  options?: {
    devMode?: boolean;
    /** Spawn and own the source backend so network-mode changes can restart it. */
    managedDevMode?: boolean;
    devPort?: number;
    egressManager?: Pick<
      EgressManager,
      | "start"
      | "quiesce"
      | "stop"
      | "setMode"
      | "getDiagnostics"
      | "getSnapshots"
      | "getRuntimePhases"
      | "getMode"
      | "getStatus"
      | "getBootstrap"
    >;
    onEgressModeChanged?: (mode: EgressMode) => void;
  },
): DesktopServiceManager => {
  const devMode = options?.devMode ?? false;
  const managedDevMode = options?.managedDevMode ?? false;
  // In dev mode, probe a backend at this port. Defaults to :8000 (matches
  // ``./scripts/dev.sh``), but ``VALUZ_BACKEND_PORT`` env override lets a
  // sibling worktree point Electron at its own backend (e.g. :28765) without
  // colliding with another dev backend already on :8000.
  const envPort = Number(process.env.VALUZ_BACKEND_PORT);
  const devPort =
    options?.devPort ??
    (Number.isFinite(envPort) && envPort > 0 ? envPort : 8000);
  const agentServerPort =
    devMode || managedDevMode ? devPort : PERSONAL_PORTS.AGENT_SERVER;
  const services = new Map<string, ServiceInfo>([
    [
      "agent-server",
      {
        name: "agent-server",
        status: "stopped",
        port: agentServerPort,
        pid: null,
        detail: AGENT_SERVER_DETAIL,
      },
    ],
  ]);
  const logs = new Map<string, string[]>();
  const descriptors = new DescriptorRegistry(personalDescriptors());
  const agentServerToken = crypto.randomBytes(16).toString("hex");
  // Separate from the renderer-visible agent server token. This capability
  // only travels main-process -> managed backend stdin/loopback control API.
  const desktopControlToken = crypto.randomBytes(32).toString("hex");

  // Track running sidecars for cleanup
  const sidecars = new Map<string, DesktopSidecarResult>();

  const setStatus = (
    name: string,
    status: ServiceStatusType,
    pid: number | null = null,
  ) => {
    const existing = services.get(name);
    if (!existing) {
      return;
    }

    services.set(name, {
      ...existing,
      status,
      pid,
    });
  };

  const addLog = (name: string, line: string) => {
    const history = logs.get(name) ?? [];
    history.push(formatLogLine(line));
    logs.set(name, history.slice(-1000));
  };

  return {
    descriptors,
    getEgressDiagnostics: () => options?.egressManager?.getDiagnostics() ?? [],
    getEgressSnapshots: () => options?.egressManager?.getSnapshots() ?? [],
    getEgressMode: () => options?.egressManager?.getMode() ?? "off",
    getEgressStatus: () =>
      options?.egressManager?.getStatus() ?? {
        mode: "off",
        enabled: false,
        started: false,
        emergencyOverride: false,
        snapshotCount: 0,
        diagnosticEventCount: 0,
      },
    getEgressRuntimePhases: () =>
      options?.egressManager?.getRuntimePhases() ?? [],
    getEgressBootstrap: () => options?.egressManager?.getBootstrap() ?? null,
    async setEgressMode(mode) {
      await options?.egressManager?.setMode(mode);
      try {
        options?.onEgressModeChanged?.(mode);
      } catch {
        // The active in-memory recovery choice has already succeeded. A
        // persistence failure must not prevent the caller from rebuilding the
        // sidecar across the compatibility boundary.
        addLog(
          "agent-server",
          "Network recovery mode changed but could not be saved for the next launch",
        );
      }
      return (
        options?.egressManager?.getStatus() ?? {
          mode: "off",
          enabled: false,
          started: false,
          emergencyOverride: false,
          snapshotCount: 0,
          diagnosticEventCount: 0,
        }
      );
    },
    getAllStatus: () => [...services.values()],
    async startAllServices() {
      // Start Electron's egress owner before any sidecar so an enabled canary
      // can deliver its inherited bootstrap before runtimes are constructed.
      let egressStartFailed = false;
      try {
        await options?.egressManager?.start();
      } catch {
        egressStartFailed = true;
      }
      for (const descriptor of descriptors.snapshot()) {
        const servicePort = managedDevMode
          ? devPort
          : descriptor.defaultPort;
        setStatus(descriptor.name, "starting");
        addLog(descriptor.name, "Starting sidecar...");
        if (egressStartFailed) {
          addLog(
            descriptor.name,
            "Unified model network is unavailable; model requests will remain blocked until model-client-managed connections are selected",
          );
        }

        // In dev mode, skip spawning a sidecar process. The user runs the
        // backend externally (``valuz start`` boots it on port 8000). Just
        // probe the dev port and mark the service accordingly.
        if (devMode) {
          addLog(
            descriptor.name,
            `Dev mode — skipping sidecar, probing port ${devPort}...`,
          );
          const health = await waitForHealth(devPort, {
            hardCapMs: DEV_HEALTH_TIMEOUT_MS,
          });
          if (health === "healthy") {
            setStatus(descriptor.name, "running");
            addLog(
              descriptor.name,
              `External backend detected on port ${devPort}`,
            );
          } else {
            setStatus(descriptor.name, "error");
            addLog(
              descriptor.name,
              `No backend responding on port ${devPort} — start it with \`valuz start\``,
            );
          }
          continue;
        }

        try {
          // Heal leftovers first: a previous shell that crashed / was
          // force-quit leaves an orphaned valuz-server holding the
          // single-writer lock and the port — our own spawn would then die
          // on the lock while the UI talks to a server it can't manage.
          await reclaimStaleSidecar(
            managedDevMode ? resolveSidecarDataDir(true) : appDataDir,
            servicePort,
            (line) => addLog(descriptor.name, line),
          );

          // Track the child's exit so the health wait can fail fast when the
          // backend process dies before it ever serves (vs. just being slow).
          let exited = false;
          let exitCode: number | null = null;
          const egressStatus = options?.egressManager?.getStatus();
          const egressBootstrap = options?.egressManager?.getBootstrap() ?? null;
          const result = await startSidecar({
            appDataDir,
            name: descriptor.name,
            port: servicePort,
            development: managedDevMode,
            onLog: (line) => {
              addLog(descriptor.name, line);
              // Also feed the system-logs ring so the desktop ``服务``
              // panel sees stdout/stderr lines (e.g. uvicorn access logs,
              // crash backtraces) that don't make it into the structured
              // JSON file.
              if (descriptor.name === "agent-server") {
                recordSidecarLine(line);
              }
            },
            onExit: (code, signal) => {
              addLog(
                descriptor.name,
                `Process exited (code=${code}, signal=${signal})`,
              );
              exited = true;
              exitCode = code;
              setStatus(descriptor.name, "stopped");
              sidecars.delete(descriptor.name);
            },
            egressBootstrap,
            egressRequired: Boolean(
              !egressBootstrap &&
                egressStatus?.enabled &&
                egressStatus.mode !== "off",
            ),
            desktopControlToken,
          });

          sidecars.set(descriptor.name, result);
          addLog(descriptor.name, `Sidecar spawned (pid=${result.pid})`);

          // Probe until healthy, the process exits, or the hard cap. No fixed
          // 30s deadline — a slow-but-alive backend keeps the splash spinning
          // instead of flashing a premature error; a real crash fails fast.
          const health = await waitForHealth(servicePort, {
            isAlive: () => !exited,
            onSlow: () =>
              addLog(
                descriptor.name,
                "Backend is taking longer than usual — still starting…",
              ),
          });
          if (health === "healthy") {
            setStatus(descriptor.name, "running", result.pid);
            addLog(descriptor.name, "Service is ready");
          } else if (health === "exited") {
            setStatus(descriptor.name, "error");
            addLog(
              descriptor.name,
              `Backend exited before it became ready (code=${exitCode ?? "?"}) — see logs`,
            );
          } else {
            setStatus(descriptor.name, "error", result.pid);
            addLog(
              descriptor.name,
              "Backend did not become ready within the maximum wait — see logs",
            );
          }
        } catch (err) {
          setStatus(descriptor.name, "error");
          addLog(
            descriptor.name,
            `Failed to start: ${err instanceof Error ? err.message : String(err)}`,
          );
        }
      }

      return [...services.values()];
    },
    async stopAllServices() {
      // Reject new registrations/capabilities first, then let sidecar-owned
      // runtimes wind down before the bounded proxy teardown closes any
      // remaining relay sockets.
      await options?.egressManager?.quiesce();
      // Await each teardown so the process trees are actually gone before we
      // report stopped — on quit this runs under before-quit (see index.ts), so
      // children release their files before the app exits / the updater installs.
      await Promise.all(
        [...sidecars.entries()].map(async ([name, sidecar]) => {
          addLog(name, "Stopping sidecar...");
          await sidecar.stop();
          setStatus(name, "stopped");
          addLog(name, "Service stopped");
        }),
      );
      sidecars.clear();
      await options?.egressManager?.stop();

      return [...services.values()];
    },
    async restartService(name: string) {
      if (devMode) {
        addLog(
          name,
          "Dev mode uses an external backend — restart it from the development shell",
        );
        return [...services.values()];
      }
      const sidecar = sidecars.get(name);
      if (sidecar) {
        addLog(name, "Restarting sidecar...");
        await sidecar.stop();
        sidecars.delete(name);
      }

      const descriptor = descriptors.snapshot().find((d) => d.name === name);
      if (!descriptor) {
        addLog(name, "No descriptor found for restart");
        return [...services.values()];
      }

      setStatus(name, "starting");
      const servicePort = managedDevMode
        ? devPort
        : descriptor.defaultPort;

      try {
        let exited = false;
        let exitCode: number | null = null;
        const egressStatus = options?.egressManager?.getStatus();
        const egressBootstrap = options?.egressManager?.getBootstrap() ?? null;
        const result = await startSidecar({
          appDataDir,
          name: descriptor.name,
          port: servicePort,
          development: managedDevMode,
          onLog: (line) => {
            addLog(descriptor.name, line);
            if (descriptor.name === "agent-server") {
              recordSidecarLine(line);
            }
          },
          onExit: (code, signal) => {
            addLog(
              descriptor.name,
              `Process exited (code=${code}, signal=${signal})`,
            );
            exited = true;
            exitCode = code;
            setStatus(descriptor.name, "stopped");
            sidecars.delete(descriptor.name);
          },
          egressBootstrap,
          egressRequired: Boolean(
            !egressBootstrap &&
              egressStatus?.enabled &&
              egressStatus.mode !== "off",
          ),
          desktopControlToken,
        });

        sidecars.set(name, result);
        const health = await waitForHealth(servicePort, {
          isAlive: () => !exited,
          onSlow: () =>
            addLog(
              descriptor.name,
              "Backend is taking longer than usual — still restarting…",
            ),
        });
        if (health === "healthy") {
          setStatus(name, "running", result.pid);
          addLog(name, "Service restarted");
        } else if (health === "exited") {
          setStatus(name, "error");
          addLog(
            name,
            `Backend exited before restart completed (code=${exitCode ?? "?"}) — see logs`,
          );
        } else {
          setStatus(name, "error", result.pid);
          addLog(
            name,
            "Backend did not become ready after restart — see logs",
          );
        }
      } catch (err) {
        setStatus(name, "error");
        addLog(
          name,
          `Failed to restart: ${err instanceof Error ? err.message : String(err)}`,
        );
      }

      return [...services.values()];
    },
    getLogs(name: string) {
      return logs.get(name) ?? [];
    },
    getAgentServerInfo() {
      return {
        port: agentServerPort,
        status: services.get("agent-server")?.status ?? "stopped",
        token: agentServerToken,
      };
    },
    getDesktopControlToken() {
      return desktopControlToken;
    },
    getShellStatus() {
      return { ready: true };
    },
    registerDescriptor(descriptor) {
      return descriptors.register(descriptor);
    },
    unregisterDescriptor(name) {
      return descriptors.unregister(name);
    },
  };
};
