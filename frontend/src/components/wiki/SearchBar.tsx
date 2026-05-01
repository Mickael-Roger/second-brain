// Debounced search bar for the wiki sidebar.
//
// Issues TWO calls in parallel:
//   - GET /api/vault/find   → notes whose name (basename or path) matches
//   - GET /api/vault/search → ripgrep'd content matches
//
// Name hits appear at the top (a user typing a known title shouldn't
// have to scroll past content matches to find the file). Content
// matches follow, with the matched line snippet. Paths surfaced as a
// name hit AND a content hit are merged so the same file isn't listed
// twice.

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { FileText, Search } from "lucide-react";

import { api, type VaultSearchHit } from "@/lib/api";

interface Props {
  onOpen: (path: string) => void;
}

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const id = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(id);
  }, [value, ms]);
  return v;
}

function highlight(text: string, q: string): React.ReactNode {
  const trimmed = q.trim();
  if (!trimmed) return text;
  const escaped = trimmed.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const parts = text.split(new RegExp(`(${escaped})`, "ig"));
  return parts.map((part, i) =>
    part.toLowerCase() === trimmed.toLowerCase() ? (
      <mark key={i} className="rounded bg-accent/30 text-text">
        {part}
      </mark>
    ) : (
      <span key={i}>{part}</span>
    ),
  );
}

export default function SearchBar({ onOpen }: Props) {
  const { t } = useTranslation();
  const [q, setQ] = useState("");
  const debounced = useDebounced(q, 250);
  const enabled = debounced.trim().length > 0;

  const names = useQuery({
    queryKey: ["vault-find", debounced],
    queryFn: () =>
      api.get<string[]>(`/api/vault/find?q=${encodeURIComponent(debounced)}&limit=20`),
    enabled,
    staleTime: 5_000,
  });

  const contents = useQuery({
    queryKey: ["vault-search", debounced],
    queryFn: () =>
      api.get<VaultSearchHit[]>(`/api/vault/search?q=${encodeURIComponent(debounced)}`),
    enabled,
    staleTime: 5_000,
  });

  const namePaths = new Set(names.data ?? []);
  const contentHits = (contents.data ?? []).filter((h) => !namePaths.has(h.path));
  const nameHits = names.data ?? [];
  const total = nameHits.length + contentHits.length;

  function pick(p: string) {
    onOpen(p);
    setQ("");
  }

  return (
    <div className="relative">
      <div className="flex items-center gap-2 border-b border-border bg-surface px-3 py-2">
        <Search className="h-4 w-4 text-muted" />
        <input
          type="text"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={t("wiki.searchPlaceholder")}
          className="w-full bg-transparent text-sm outline-none"
        />
      </div>

      {enabled && total > 0 && (
        <ul className="absolute left-0 right-0 z-10 max-h-96 overflow-y-auto border-b border-border bg-surface shadow-lg">
          {nameHits.length > 0 && (
            <li className="border-b border-border bg-bg/40 px-3 py-1 text-[10px] font-semibold uppercase tracking-wide text-muted">
              {t("wiki.search.nameMatches")}
            </li>
          )}
          {nameHits.map((p) => (
            <li key={`n:${p}`}>
              <button
                type="button"
                onClick={() => pick(p)}
                className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-bg"
              >
                <FileText className="h-3.5 w-3.5 shrink-0 text-muted" />
                <span className="truncate text-xs text-accent">
                  {highlight(p, debounced)}
                </span>
              </button>
            </li>
          ))}

          {contentHits.length > 0 && (
            <li className="border-b border-border bg-bg/40 px-3 py-1 text-[10px] font-semibold uppercase tracking-wide text-muted">
              {t("wiki.search.contentMatches")}
            </li>
          )}
          {contentHits.map((h, i) => (
            <li key={`c:${i}:${h.path}:${h.line_number}`}>
              <button
                type="button"
                onClick={() => pick(h.path)}
                className="flex w-full flex-col gap-0.5 px-3 py-1.5 text-left hover:bg-bg"
              >
                <span className="truncate text-xs font-medium text-accent">
                  {h.path}
                </span>
                <span className="line-clamp-1 text-xs text-muted">
                  {highlight(h.snippet, debounced)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {enabled &&
        !names.isLoading &&
        !contents.isLoading &&
        total === 0 && (
          <div className="absolute left-0 right-0 z-10 border-b border-border bg-surface px-3 py-2 text-xs text-muted shadow-lg">
            {t("wiki.search.noHits")}
          </div>
        )}
    </div>
  );
}
