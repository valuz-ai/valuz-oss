import { useEffect, useState } from "react";
import { providersApi } from "@valuz/core";
import { StartupScreen } from "./components/StartupScreen";
import { UpdaterListener } from "./components/UpdaterListener";
import { UpdateToast } from "./components/UpdateToast";
import { useDesktopStartup } from "./hooks/use-desktop-startup";
import { ElectronPlatformProvider } from "./lib/electron-platform";
import { AppRouter } from "./routes/router";
import { isOnboarded } from "@valuz/app/lib/onboarding";
import "./App.css";

const hasUsableProvider = (
  providers: { enabled: boolean; credential_source: string }[],
) => providers.some((p) => p.enabled && p.credential_source !== "none");

export const App = () => {
  const { services, logs, loading, checking, ready, error, retry } =
    useDesktopStartup();
  const [setupChecked, setSetupChecked] = useState(false);

  useEffect(() => {
    if (!ready) return;

    let cancelled = false;

    const check = async () => {
      // User already completed connection setup on /welcome (persisted
      // marker). Skip the startup gate so refresh doesn't bounce back to
      // /welcome — needed for subscription logins whose credential lives in
      // the CLI keychain and can't be detected from the providers API.
      if (isOnboarded()) {
        if (!cancelled) setSetupChecked(true);
        return;
      }

      for (let attempt = 0; attempt < 20; attempt++) {
        if (cancelled) return;

        try {
          const { providers } = await providersApi.list();
          if (cancelled) return;
          if (!hasUsableProvider(providers)) {
            window.location.hash = "#/welcome";
          }
          break;
        } catch {
          await new Promise((r) => setTimeout(r, 300));
        }
      }

      if (!cancelled) setSetupChecked(true);
    };

    void check();
    return () => {
      cancelled = true;
    };
  }, [ready]);

  if (checking || (ready && !setupChecked)) {
    return null;
  }

  if (!ready) {
    return (
      <StartupScreen
        services={services}
        logs={logs}
        loading={loading}
        error={error}
        onRetry={retry}
      />
    );
  }

  return (
    <ElectronPlatformProvider>
      <UpdaterListener />
      <UpdateToast />
      <AppRouter />
    </ElectronPlatformProvider>
  );
};
