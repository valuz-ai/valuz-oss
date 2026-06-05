import crypto from "node:crypto";
import {
  PERSONAL_PORTS,
  type CraftServerInfo,
  type ServiceInfo,
  type ServiceStatusType,
} from "@valuz/shared";
import type { ServiceDescriptor } from "@valuz/core";
import { DescriptorRegistry, personalDescriptors } from "./descriptors";
import { startSidecar, type DesktopSidecarResult } from "./sidecar";
import { recordSidecarLine } from "./system-logs";

const AGENT_SERVER_DETAIL = "Primary local agent runtime";
const HEALTH_CHECK_TIMEOUT_MS = 30_000;
const HEALTH_CHECK_INTERVAL_MS = 500;

export interface DesktopServiceManager {
  descriptors: DescriptorRegistry;
  startAllServices(): Promise<ServiceInfo[]>;
  stopAllServices(): ServiceInfo[];
  restartService(name: string): Promise<ServiceInfo[]>;
  getLogs(name: string): string[];
  getAgentServerInfo(): CraftServerInfo;
  getShellStatus(): { ready: boolean };
  getAllStatus(): ServiceInfo[];
  registerDescriptor(descriptor: ServiceDescriptor): ServiceDescriptor;
  unregisterDescriptor(name: string): boolean;
}

const formatLogLine = (line: string) => {
  const timestamp = new Date().toISOString().slice(11, 23);
  return `[${timestamp}] ${line}`;
};

/**
 * Poll the backend health endpoint until it responds or times out.
 */
const waitForHealth = async (
  port: number,
  timeoutMs: number,
): Promise<boolean> => {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/v1/workspaces`);
      if (res.ok) return true;
    } catch {
      // Not ready yet
    }
    await new Promise((r) => setTimeout(r, HEALTH_CHECK_INTERVAL_MS));
  }
  return false;
};

export const createServiceManager = (
  appDataDir = process.cwd(),
  options?: { devMode?: boolean; devPort?: number },
): DesktopServiceManager => {
  const devMode = options?.devMode ?? false;
  // In dev mode, probe a backend at this port. Defaults to :8000 (matches
  // ``./scripts/dev.sh``), but ``VALUZ_BACKEND_PORT`` env override lets a
  // sibling worktree point Electron at its own backend (e.g. :28765) without
  // colliding with another dev backend already on :8000.
  const envPort = Number(process.env.VALUZ_BACKEND_PORT);
  const devPort =
    options?.devPort ??
    (Number.isFinite(envPort) && envPort > 0 ? envPort : 8000);
  const services = new Map<string, ServiceInfo>([
    [
      "agent-server",
      {
        name: "agent-server",
        status: "stopped",
        port: PERSONAL_PORTS.AGENT_SERVER,
        pid: null,
        detail: AGENT_SERVER_DETAIL,
      },
    ],
  ]);
  const logs = new Map<string, string[]>();
  const descriptors = new DescriptorRegistry(personalDescriptors());
  const agentServerToken = crypto.randomBytes(16).toString("hex");

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
    getAllStatus: () => [...services.values()],
    async startAllServices() {
      for (const descriptor of descriptors.snapshot()) {
        setStatus(descriptor.name, "starting");
        addLog(descriptor.name, "Starting sidecar...");

        // In dev mode, skip spawning a sidecar process. The user runs the
        // backend externally (``valuz start`` boots it on port 8000). Just
        // probe the dev port and mark the service accordingly.
        if (devMode) {
          addLog(
            descriptor.name,
            `Dev mode — skipping sidecar, probing port ${devPort}...`,
          );
          const healthy = await waitForHealth(devPort, HEALTH_CHECK_TIMEOUT_MS);
          if (healthy) {
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
          const result = await startSidecar({
            appDataDir,
            name: descriptor.name,
            port: descriptor.defaultPort,
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
              setStatus(descriptor.name, "stopped");
              sidecars.delete(descriptor.name);
            },
          });

          sidecars.set(descriptor.name, result);
          addLog(descriptor.name, `Sidecar spawned (pid=${result.pid})`);

          // Wait for health check
          const healthy = await waitForHealth(
            descriptor.defaultPort,
            HEALTH_CHECK_TIMEOUT_MS,
          );
          if (healthy) {
            setStatus(descriptor.name, "running", result.pid);
            addLog(descriptor.name, "Service is ready");
          } else {
            setStatus(descriptor.name, "error", result.pid);
            addLog(
              descriptor.name,
              "Health check timed out — service may not be responding",
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
    stopAllServices() {
      for (const [name, sidecar] of sidecars.entries()) {
        addLog(name, "Stopping sidecar...");
        sidecar.stop();
        setStatus(name, "stopped");
        addLog(name, "Service stopped");
      }
      sidecars.clear();

      return [...services.values()];
    },
    async restartService(name: string) {
      const sidecar = sidecars.get(name);
      if (sidecar) {
        addLog(name, "Restarting sidecar...");
        sidecar.stop();
        sidecars.delete(name);
      }

      const descriptor = descriptors.snapshot().find((d) => d.name === name);
      if (!descriptor) {
        addLog(name, "No descriptor found for restart");
        return [...services.values()];
      }

      setStatus(name, "starting");

      try {
        const result = await startSidecar({
          appDataDir,
          name: descriptor.name,
          port: descriptor.defaultPort,
          onLog: (line) => addLog(descriptor.name, line),
          onExit: (code, signal) => {
            addLog(
              descriptor.name,
              `Process exited (code=${code}, signal=${signal})`,
            );
            setStatus(descriptor.name, "stopped");
            sidecars.delete(descriptor.name);
          },
        });

        sidecars.set(name, result);
        setStatus(name, "running", result.pid);
        addLog(name, "Service restarted");
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
        port: PERSONAL_PORTS.AGENT_SERVER,
        status: services.get("agent-server")?.status ?? "stopped",
        token: agentServerToken,
      };
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
