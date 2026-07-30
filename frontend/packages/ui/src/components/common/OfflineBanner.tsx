import { useState, useEffect } from "react";
import { useI18n } from "../../hooks/use-i18n";

export const OfflineBanner = () => {
  const { t } = useI18n();
  const [offline, setOffline] = useState(!navigator.onLine);

  useEffect(() => {
    const goOffline = () => setOffline(true);
    const goOnline = () => setOffline(false);
    window.addEventListener("offline", goOffline);
    window.addEventListener("online", goOnline);
    return () => {
      window.removeEventListener("offline", goOffline);
      window.removeEventListener("online", goOnline);
    };
  }, []);

  if (!offline) return null;

  return (
    // Fixed overlay rather than an in-flow strip: as part of the layout flow it
    // pushed the entire shell (TopBar included) down by its own height, which
    // also left the macOS traffic lights — pinned by the window at y=12 and not
    // moved by CSS — stranded on top of the banner instead of inside the bar.
    // h-[36px] matches TopBar, so the traffic lights stay vertically centered
    // while the strip covers that row. Overlay editions may mount their own
    // top strip at z-[100] (commercial's cloud-recovery banner); being offline
    // is the more fundamental condition, so this one sits above it.
    <div className="fixed inset-x-0 top-0 z-[110] flex h-[36px] items-center justify-center bg-error-strong px-4 text-xs font-medium text-white">
      {t("offline.banner")}
    </div>
  );
};
