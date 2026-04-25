import { useTranslation } from "react-i18next";

import type { VaultBacklink } from "@/lib/api";

interface Props {
  links: VaultBacklink[];
  onOpen: (path: string) => void;
}

export default function Backlinks({ links, onOpen }: Props) {
  const { t } = useTranslation();
  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border px-3 py-2 text-xs uppercase tracking-wide text-muted">
        {t("wiki.backlinks")} <span className="text-text">{links.length}</span>
      </div>
      <div className="flex-1 overflow-y-auto p-2">
        {links.length === 0 ? (
          <p className="px-2 py-1 text-xs text-muted">{t("wiki.noBacklinks")}</p>
        ) : (
          <ul className="space-y-2 text-sm">
            {links.map((b, i) => (
              <li key={i}>
                <button
                  type="button"
                  onClick={() => onOpen(b.path)}
                  className="block w-full text-left"
                >
                  <div className="truncate font-medium text-accent">
                    {b.path.replace(/\.md$/, "")}
                  </div>
                  <div className="line-clamp-2 text-xs text-muted">{b.snippet}</div>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
