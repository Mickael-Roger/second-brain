import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import App from "./App";
import ErrorBoundary from "./components/layout/ErrorBoundary";
import { initI18n } from "./lib/i18n";
import { applyTheme, currentTheme } from "./lib/theme";
import "./index.css";

initI18n();
// Apply the persisted theme BEFORE the first React render so a
// light-mode visitor doesn't see a flash of the dark default.
applyTheme(currentTheme());

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <ErrorBoundary>
        <App />
      </ErrorBoundary>
    </QueryClientProvider>
  </React.StrictMode>,
);
