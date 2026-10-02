import "@fontsource/poppins/latin-400.css";
import "@fontsource/poppins/latin-500.css";
import "@fontsource/poppins/latin-600.css";
import "./styles.css";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Toaster } from "sonner";

import App from "./App";
import { platform } from "./platform";

if (platform.kind === "native") document.documentElement.classList.add("native");
if (platform.kind === "native" && platform.windowRole() === "settings") document.documentElement.classList.add("settings-window");

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false, staleTime: Infinity } },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
      <Toaster position="bottom-center" toastOptions={{ className: "font-sans" }} />
    </QueryClientProvider>
  </StrictMode>,
);
