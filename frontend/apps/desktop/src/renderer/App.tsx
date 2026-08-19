import { useEffect, useRef, useState } from "react";
import { providersApi } from "@valuz/core";
import { ErrorBoundary, LogoShimmer } from "@valuz/ui";
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

  // "Arrive, then enter": once the backend is up we keep the splash mounted
  // until its progress bar has visibly run to 100% (StartupScreen calls
  // onComplete) instead of cutting away mid-bar. Only when a boot was
  // actually shown — if services were already up when we looked (renderer
  // reload, warm relaunch) there is nothing to finish and we go straight on.
  const [splashDone, setSplashDone] = useState(false);
  const sawBootRef = useRef(false);
  if (!checking && !ready) sawBootRef.current = true;
  const holdSplash = ready && sawBootRef.current && !splashDone;

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

  // The platform provider wraps EVERY branch — StartupScreen calls
  // usePlatform() (frameless-window controls), so rendering it outside
  // the provider crashes the renderer before the backend is ready.
  let content = null;
  if (!checking && (!ready || holdSplash)) {
    // The onboarding probe above runs concurrently while the bar finishes.
    content = (
      <StartupScreen
        services={services}
        logs={logs}
        loading={loading}
        error={error}
        onRetry={retry}
        complete={ready}
        onComplete={() => setSplashDone(true)}
      />
    );
  } else if (checking || !setupChecked) {
    // Startup gates (services status probe / onboarding check) used to
    // render literally nothing here — a plain white window with no hint
    // of life for however long they took (the onboarding probe can retry
    // for several seconds against a slow backend). Show the shimmer.
    content = (
      <div className="flex h-screen items-center justify-center">
        <LogoShimmer size="md" />
      </div>
    );
  } else {
    content = (
      <>
        <UpdaterListener />
        <UpdateToast />
        <AppRouter />
      </>
    );
  }

  // Root boundary: without it, any uncaught render/effect throw above the
  // layout-level boundary (router root, layout hooks, startup branches)
  // unmounts the entire tree — a permanently white window that only a
  // reload can recover. Degrade to the "Something went wrong" fallback
  // with a Retry instead.
  return (
    <ElectronPlatformProvider>
      <ErrorBoundary>{content}</ErrorBoundary>
    </ElectronPlatformProvider>
  );
};
