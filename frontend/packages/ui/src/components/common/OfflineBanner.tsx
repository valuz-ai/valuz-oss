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
    <div className="flex h-8 items-center justify-center bg-red-500 px-4 text-xs font-medium text-white">
      {t("offline.banner")}
    </div>
  );
};
