import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useMe } from "@/lib/auth";
import LoginPage from "@/routes/login";
import AppShell, { type ViewId } from "@/components/layout/AppShell";
import ChatView from "@/components/chat/ChatView";
import WikiView from "@/components/wiki/WikiView";

export default function App() {
  const { t } = useTranslation();
  const me = useMe();
  const [view, setView] = useState<ViewId>("chat");

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
      {view === "chat" ? <ChatView /> : <WikiView />}
    </AppShell>
  );
}
