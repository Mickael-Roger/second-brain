import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";

import { useMe } from "@/lib/auth";
import LoginPage from "@/routes/login";
import AppShell, { type ViewId } from "@/components/layout/AppShell";
import ChatView from "@/components/chat/ChatView";
import WikiView, { type WikiTarget } from "@/components/wiki/WikiView";
import NewsView from "@/components/news/NewsView";
import WikiReviewModal from "@/components/wiki/WikiReviewModal";
import { api, type TreeEntry, type WikiReviewStatus } from "@/lib/api";

export default function App() {
  const { t } = useTranslation();
  const me = useMe();
  const [view, setView] = useState<ViewId>("chat");
  const [reviewOpen, setReviewOpen] = useState(false);

  // Cross-view wiki navigation: chat (or anywhere else) can ask the wiki
  // to open a specific path. The nonce makes consecutive requests for the
  // same path actually re-trigger.
  const [wikiTarget, setWikiTarget] = useState<WikiTarget | null>(null);
  const openWiki = useCallback((path: string | null) => {
    setWikiTarget((prev) => ({ path, nonce: (prev?.nonce ?? 0) + 1 }));
    setView("wiki");
  }, []);

  // Wiki-review status drives the review badge in the global nav rail.
  // The query is enabled only when the user is logged in (gated below).
  const reviewStatus = useQuery({
    queryKey: ["wiki-review-status"],
    queryFn: () => api.get<WikiReviewStatus>("/api/wiki-reviews/status"),
    enabled: !!me.data,
    staleTime: 60_000,
  });

  // Vault tree (cached) — the modal needs it for NoteRenderer wikilink
  // resolution. WikiView reads the same key, so this fetch is shared.
  const tree = useQuery({
    queryKey: ["vault-tree"],
    queryFn: () => api.get<TreeEntry[]>("/api/vault/tree"),
    enabled: !!me.data,
  });

  if (me.isLoading) {
    return (
      <div className="flex h-full items-center justify-center text-muted">
        {t("common.loading")}
      </div>
    );
  }

  if (!me.data) {
    return <LoginPage onSuccess={() => me.refetch()} />;
  }

  const reviewNeeded =
    !!reviewStatus.data && !reviewStatus.data.has_reviewed_today;

  return (
    <>
      <AppShell
        active={view}
        onSelect={setView}
        reviewNeeded={reviewNeeded}
        onOpenReview={() => setReviewOpen(true)}
      >
        {view === "chat" ? (
          <ChatView onOpenWiki={openWiki} />
        ) : view === "news" ? (
          <NewsView onOpenChat={() => setView("chat")} />
        ) : (
          <WikiView
            target={wikiTarget}
            onOpenChat={() => setView("chat")}
          />
        )}
      </AppShell>

      {reviewOpen && (
        <WikiReviewModal
          treeEntries={tree.data ?? []}
          onOpenWiki={openWiki}
          onClose={() => setReviewOpen(false)}
        />
      )}
    </>
  );
}
