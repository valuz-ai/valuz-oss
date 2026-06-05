import { useCallback, useEffect, useMemo, useState } from "react";
import type { ServiceInfo } from "@valuz/shared";
import { createTransport } from "@valuz/core";

interface DesktopStartupState {
  services: ServiceInfo[];
  logs: string[];
  loading: boolean;
  checking: boolean;
  ready: boolean;
  error: string | null;
  retry: () => Promise<void>;
}

const hasRunningServices = (services: ServiceInfo[]) =>
  services.some((service) => service.status === "running");

export const useDesktopStartup = (): DesktopStartupState => {
  const transport = useMemo(() => createTransport(), []);
  const [services, setServices] = useState<ServiceInfo[]>([]);
  const [logs, setLogs] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [checking, setChecking] = useState(true);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadPrimaryLogs = useCallback(
    async (snapshot: ServiceInfo[]) => {
      const primaryService = snapshot[0]?.name;

      if (!primaryService) {
        setLogs([]);
        return;
      }

      const nextLogs = await transport.invoke<string[]>("get_service_logs", {
        serviceName: primaryService,
      });
      setLogs(nextLogs);
    },
    [transport],
  );

  const bootstrap = useCallback(async () => {
    setError(null);

    try {
      const initialSnapshot = await transport.invoke<ServiceInfo[]>(
        "get_services_status",
      );
      setServices(initialSnapshot);

      if (hasRunningServices(initialSnapshot)) {
        setChecking(false);
        setReady(true);
        return;
      }

      setChecking(false);
      setLoading(true);

      const nextSnapshot =
        await transport.invoke<ServiceInfo[]>("start_all_services");

      setServices(nextSnapshot);
      await loadPrimaryLogs(nextSnapshot);
      setReady(hasRunningServices(nextSnapshot));
    } catch (reason: unknown) {
      setChecking(false);
      setError(
        reason instanceof Error
          ? reason.message
          : "Failed to bootstrap desktop runtime",
      );
      setReady(false);
    } finally {
      setLoading(false);
    }
  }, [loadPrimaryLogs, transport]);

  useEffect(() => {
    let active = true;

    const dispose = transport.listen<ServiceInfo[]>(
      "service-status-changed",
      (snapshot) => {
        if (!active) {
          return;
        }

        setServices(snapshot);
        setReady(hasRunningServices(snapshot));
      },
    );

    queueMicrotask(() => {
      if (active) {
        void bootstrap();
      }
    });

    return () => {
      active = false;
      dispose();
      transport.close();
    };
  }, [bootstrap, transport]);

  return {
    services,
    logs,
    loading,
    checking,
    ready,
    error,
    retry: bootstrap,
  };
};
