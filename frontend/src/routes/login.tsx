import { FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";
import { Brain } from "lucide-react";

import { ApiError } from "@/lib/api";
import { login } from "@/lib/auth";
import { setLanguage, currentLanguage } from "@/lib/i18n";

interface Props {
  onSuccess: () => void;
}

export default function LoginPage({ onSuccess }: Props) {
  const { t, i18n } = useTranslation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await login(username, password);
      onSuccess();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError(t("login.invalid"));
      } else {
        setError(t("login.error"));
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex h-full items-center justify-center px-4">
      <form
        onSubmit={onSubmit}
        className="w-full max-w-sm space-y-5 rounded-2xl border border-border bg-surface p-8 shadow-lg"
      >
        <div className="flex items-center gap-3">
          <Brain className="h-8 w-8 text-accent" />
          <h1 className="text-2xl font-semibold">{t("app.title")}</h1>
        </div>
        <h2 className="text-lg text-muted">{t("login.title")}</h2>

        <label className="block">
          <span className="text-sm text-muted">{t("login.username")}</span>
          <input
            type="text"
            autoComplete="username"
            autoFocus
            required
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="mt-1 w-full rounded-lg border border-border bg-bg px-3 py-2 outline-none focus:border-accent"
          />
        </label>

        <label className="block">
          <span className="text-sm text-muted">{t("login.password")}</span>
          <input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="mt-1 w-full rounded-lg border border-border bg-bg px-3 py-2 outline-none focus:border-accent"
          />
        </label>

        {error && <p className="text-sm text-red-400">{error}</p>}

        <button
          type="submit"
          disabled={loading || !username || !password}
          className="w-full rounded-lg bg-accent px-4 py-2 font-medium text-bg transition hover:opacity-90"
        >
          {loading ? t("common.loading") : t("login.submit")}
        </button>

        <div className="flex justify-end gap-2 text-xs text-muted">
          <button
            type="button"
            className={currentLanguage() === "fr" ? "text-accent" : ""}
            onClick={() => {
              setLanguage("fr");
              i18n.changeLanguage("fr");
            }}
          >
            FR
          </button>
          <span>·</span>
          <button
            type="button"
            className={currentLanguage() === "en" ? "text-accent" : ""}
            onClick={() => {
              setLanguage("en");
              i18n.changeLanguage("en");
            }}
          >
            EN
          </button>
        </div>
      </form>
    </div>
  );
}
