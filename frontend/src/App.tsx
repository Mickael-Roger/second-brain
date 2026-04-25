import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useMe } from "@/lib/auth";
import LoginPage from "@/routes/login";
import ChatPage from "@/routes/chat";
import Sidebar from "@/components/layout/Sidebar";

export default function App() {
  const { t } = useTranslation();
  const me = useMe();
  const [activeChatId, setActiveChatId] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);

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
    <div className="flex h-full">
      <Sidebar
        activeChatId={activeChatId}
        onSelectChat={(id) => {
          setActiveChatId(id);
          setSidebarOpen(false);
        }}
        onNewChat={() => {
          setActiveChatId(null);
          setSidebarOpen(false);
        }}
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
      />
      <main className="flex-1 min-w-0">
        <ChatPage
          chatId={activeChatId}
          onChatCreated={(id) => setActiveChatId(id)}
          onOpenSidebar={() => setSidebarOpen(true)}
        />
      </main>
    </div>
  );
}
