// Debounced search bar that calls /api/vault/search and lists hits.

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Search } from "lucide-react";

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

export default function SearchBar({ onOpen }: Props) {
  const { t } = useTranslation();
  const [q, setQ] = useState("");
  const debounced = useDebounced(q, 250);

  const hits = useQuery({
    queryKey: ["vault-search", debounced],
    queryFn: () =>
      api.get<VaultSearchHit[]>(`/api/vault/search?q=${encodeURIComponent(debounced)}`),
    enabled: debounced.trim().length > 0,
    staleTime: 5_000,
  });

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
      {debounced.trim() && hits.data && hits.data.length > 0 && (
        <ul className="absolute left-0 right-0 z-10 max-h-80 overflow-y-auto border-b border-border bg-surface shadow-lg">
          {hits.data.map((h, i) => (
            <li key={i}>
              <button
                type="button"
                onClick={() => {
                  onOpen(h.path);
                  setQ("");
                }}
                className="flex w-full flex-col gap-0.5 px-3 py-1.5 text-left hover:bg-bg"
              >
                <span className="truncate text-xs font-medium text-accent">
                  {h.path}
                </span>
                <span className="line-clamp-1 text-xs text-muted">{h.snippet}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
