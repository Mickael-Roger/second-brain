import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";

import { useMe } from "@/lib/auth";
import LoginPage from "@/routes/login";
import AppShell, { type ViewId } from "@/components/layout/AppShell";
import ChatView from "@/components/chat/ChatView";
import WikiView, { type WikiTarget } from "@/components/wiki/WikiView";
import OrganizeView from "@/components/organize/OrganizeView";
import NewsView from "@/components/news/NewsView";
import AnkiView from "@/components/anki/AnkiView";

export default function App() {
  const { t } = useTranslation();
  const me = useMe();
  const [view, setView] = useState<ViewId>("chat");

  // Cross-view wiki navigation: chat (or anywhere else) can ask the wiki
  // to open a specific path. The nonce makes consecutive requests for the
  // same path actually re-trigger.
  const [wikiTarget, setWikiTarget] = useState<WikiTarget | null>(null);
  const openWiki = useCallback((path: string | null) => {
    setWikiTarget((prev) => ({ path, nonce: (prev?.nonce ?? 0) + 1 }));
    setView("wiki");
  }, []);

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

  return (
    <AppShell active={view} onSelect={setView}>
      {view === "chat" ? (
        <ChatView onOpenWiki={openWiki} />
      ) : view === "organize" ? (
        <OrganizeView onOpenWiki={openWiki} />
      ) : view === "news" ? (
        <NewsView onOpenChat={() => setView("chat")} />
      ) : view === "anki" ? (
        <AnkiView />
      ) : (
        <WikiView target={wikiTarget} />
      )}
    </AppShell>
  );
}
