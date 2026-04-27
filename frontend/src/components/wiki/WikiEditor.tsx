// CodeMirror-backed markdown editor for the wiki view. Owns the edit-session
// lock lifecycle: acquire on mount, release on save / cancel / unmount.

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import CodeMirror from "@uiw/react-codemirror";
import { markdown } from "@codemirror/lang-markdown";
import { EditorView } from "@codemirror/view";
import { Save, X } from "lucide-react";

import { api, ApiError, type VaultNote } from "@/lib/api";
import { currentTheme, type Theme } from "@/lib/theme";

interface Props {
  path: string;
  initialContent: string;
  onSaved: (note: VaultNote) => void;
  onCancel: () => void;
}

interface LockResponse {
  path: string;
  token: string;
  expires_at: string;
}

// CSS-variable-based overlay so the editor follows the app's
// light/dark theme. The actual base theme (built-in light/dark) is
// selected dynamically via the `theme` prop on <CodeMirror>.
const editorTheme = EditorView.theme({
  "&": { height: "100%", fontSize: "14px" },
  ".cm-scroller": {
    fontFamily: "ui-monospace, SFMono-Regular, monospace",
    lineHeight: "1.55",
  },
  ".cm-content": { padding: "1.25rem 1.5rem" },
  ".cm-gutters": {
    backgroundColor: "rgb(var(--surface))",
    borderRight: "1px solid rgb(var(--border))",
    color: "rgb(var(--muted))",
  },
  ".cm-focused .cm-cursor": { borderLeftColor: "rgb(var(--accent))" },
  ".cm-line": { color: "rgb(var(--text))" },
});

export default function WikiEditor({ path, initialContent, onSaved, onCancel }: Props) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [content, setContent] = useState(initialContent);
  const [token, setToken] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [acquiring, setAcquiring] = useState(true);
  const releaseGuard = useRef(false);
  const [theme, setEditorTheme] = useState<Theme>(() => currentTheme());

  // Mirror the app theme (toggled via the sidebar Sun/Moon button) by
  // observing the `theme-light` class on <html>. Without this, the
  // editor would stay on whatever theme was active at mount even when
  // the user toggles modes mid-edit.
  useEffect(() => {
    const obs = new MutationObserver(() => setEditorTheme(currentTheme()));
    obs.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });
    return () => obs.disconnect();
  }, []);

  // Acquire the lock on mount; release on unmount.
  useEffect(() => {
    let mounted = true;
    setAcquiring(true);
    api
      .post<LockResponse>("/api/vault/edit/lock", { path })
      .then((g) => {
        if (!mounted) {
          // We unmounted before the response landed — release immediately.
          void api.delete(`/api/vault/edit/lock`).catch(() => undefined);
          return;
        }
        setToken(g.token);
        setError(null);
      })
      .catch((e: unknown) => {
        if (!mounted) return;
        if (e instanceof ApiError) {
          setError(
            e.status === 409
              ? t("wiki.lockBusy")
              : (e.detail as { detail?: string })?.detail ?? t("wiki.lockError"),
          );
        } else {
          setError(t("wiki.lockError"));
        }
      })
      .finally(() => {
        if (mounted) setAcquiring(false);
      });

    return () => {
      mounted = false;
      const tok = tokenRef.current;
      if (tok && !releaseGuard.current) {
        releaseGuard.current = true;
        // Best-effort release; this fires on unmount, including when we
        // navigate away before saving.
        void fetch("/api/vault/edit/lock", {
          method: "DELETE",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path, token: tok }),
        }).catch(() => undefined);
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path]);

  // Keep the latest token in a ref so the unmount cleanup can read it.
  const tokenRef = useRef<string | null>(null);
  useEffect(() => {
    tokenRef.current = token;
  }, [token]);

  const onChange = useCallback((value: string) => {
    setContent(value);
  }, []);

  async function save() {
    if (!token || saving) return;
    setSaving(true);
    setError(null);
    try {
      const n = await api.put<VaultNote>("/api/vault/note", {
        path,
        content,
        token,
      });
      releaseGuard.current = true; // PUT also implicitly does NOT release; cleanup below
      // The lock is still ours after save — explicitly release it.
      try {
        await api.delete(`/api/vault/edit/lock`).catch(() => undefined);
      } catch {
        /* ignore */
      }
      // Invalidate the cached tree + note so the SPA re-fetches.
      qc.invalidateQueries({ queryKey: ["vault-tree"] });
      qc.invalidateQueries({ queryKey: ["vault-note", path] });
      onSaved(n);
    } catch (e: unknown) {
      const msg =
        e instanceof ApiError
          ? (e.detail as { detail?: string })?.detail ?? `HTTP ${e.status}`
          : (e as Error).message;
      setError(msg);
    } finally {
      setSaving(false);
    }
  }

  const dirty = content !== initialContent;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-2 border-b border-border bg-surface px-3 py-2 text-xs">
        <span className="flex-1 truncate text-muted">
          {acquiring
            ? t("wiki.acquiring")
            : token
              ? t("wiki.editing", { path })
              : t("wiki.lockError")}
        </span>
        {error && <span className="text-red-400">{error}</span>}
        <button
          type="button"
          onClick={onCancel}
          className="flex items-center gap-1 rounded border border-border px-2 py-1 text-muted hover:text-text"
        >
          <X className="h-3.5 w-3.5" />
          {t("common.cancel")}
        </button>
        <button
          type="button"
          onClick={save}
          disabled={!token || saving || !dirty}
          className="flex items-center gap-1 rounded bg-accent px-2 py-1 font-medium text-bg disabled:bg-border"
        >
          <Save className="h-3.5 w-3.5" />
          {saving ? t("common.loading") : t("common.save")}
        </button>
      </div>
      <div className="flex-1 min-h-0">
        <CodeMirror
          value={content}
          onChange={onChange}
          extensions={[markdown(), editorTheme]}
          theme={theme}
          height="100%"
          basicSetup={{
            lineNumbers: true,
            foldGutter: true,
            highlightActiveLine: true,
            indentOnInput: true,
          }}
          editable={!!token}
        />
      </div>
    </div>
  );
}
