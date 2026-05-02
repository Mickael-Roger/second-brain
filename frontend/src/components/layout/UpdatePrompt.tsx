import { useTranslation } from "react-i18next";
import { useRegisterSW } from "virtual:pwa-register/react";
import { RefreshCw, X } from "lucide-react";

// Polls the SW once an hour for a new bundle on top of the natural
// "tab regained focus" check. Without this, an installed PWA that
// stays open for days would never see updates.
const UPDATE_POLL_INTERVAL_MS = 60 * 60 * 1000;

export default function UpdatePrompt() {
  const { t } = useTranslation();
  const {
    needRefresh: [needRefresh, setNeedRefresh],
    updateServiceWorker,
  } = useRegisterSW({
    onRegisteredSW(_swUrl, registration) {
      if (!registration) return;
      setInterval(() => {
        if (registration.installing || !navigator.onLine) return;
        registration.update().catch(() => {
          /* network blip — next tick will retry */
        });
      }, UPDATE_POLL_INTERVAL_MS);
    },
  });

  if (!needRefresh) return null;

  return (
    <div
      role="status"
      className="fixed inset-x-0 bottom-0 z-50 flex justify-center px-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] md:bottom-4 md:right-4 md:left-auto md:justify-end md:px-0 md:pb-0"
    >
      <div className="flex w-full max-w-md items-center gap-2 rounded-lg border border-accent bg-surface px-3 py-2 shadow-lg">
        <RefreshCw className="h-4 w-4 shrink-0 text-accent" />
        <span className="flex-1 truncate text-sm">
          {t("pwa.updateAvailable")}
        </span>
        <button
          type="button"
          onClick={() => updateServiceWorker(true)}
          className="rounded-md bg-accent px-3 py-1 text-xs font-medium text-bg hover:bg-accent/90"
        >
          {t("pwa.reload")}
        </button>
        <button
          type="button"
          onClick={() => setNeedRefresh(false)}
          aria-label={t("pwa.dismiss")}
          className="rounded-md p-1 text-muted hover:bg-bg hover:text-text"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}
