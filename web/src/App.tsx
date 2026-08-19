import { useCallback, useEffect, useState } from "react";
import { Toaster } from "sonner";
import { Moon, Sun, LogOut, Zap } from "lucide-react";
import { api, type Me } from "./lib/api";
import { cn } from "./lib/utils";
import { Button } from "./components/ui/button";
import { LoginScreen } from "./screens/LoginScreen";
import { ChatScreen } from "./screens/ChatScreen";
import { ResearchScreen } from "./screens/ResearchScreen";

type Mode = "chat" | "research";

const THEME_KEY = "nx-theme";

function useTheme() {
  const [dark, setDark] = useState<boolean>(() => {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved) return saved === "dark";
    return true; // dark-first, matching the NuruXplore brand
  });
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    localStorage.setItem(THEME_KEY, dark ? "dark" : "light");
  }, [dark]);
  return { dark, toggle: () => setDark((d) => !d) };
}

export default function App() {
  const { dark, toggle } = useTheme();
  const [me, setMe] = useState<Me | null>(null);
  const [booted, setBooted] = useState(false);
  const [mode, setMode] = useState<Mode>("chat");

  const refreshMe = useCallback(async () => {
    try {
      setMe(await api<Me>("/api/local/me"));
    } catch {
      setMe(null);
    }
  }, []);

  useEffect(() => {
    (async () => {
      try {
        setMe(await api<Me>("/api/local/me"));
      } catch {
        setMe(null);
      }
      setBooted(true);
    })();
  }, []);

  const logout = () => {
    document.cookie = "nurux_session=; Max-Age=0; path=/";
    setMe(null);
  };

  if (!booted) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="size-8 animate-spin rounded-full border-2 border-border border-t-primary" />
      </div>
    );
  }

  if (!me) {
    return (
      <>
        <LoginScreen onLogin={setMe} />
        <Toaster position="top-center" richColors closeButton />
      </>
    );
  }

  const credits = me.credits_balance ?? me.user?.credits_balance;

  return (
    <div className="glow-shell relative flex min-h-dvh flex-col text-foreground">
      <div className="paper-grain" aria-hidden />
      <Toaster position="top-center" richColors closeButton />

      <header className="sticky top-0 z-40 border-b bg-(--background)">
        <div className="flex h-12 w-full items-center gap-4 px-4 sm:px-6">
          {/* Brand — registration mark + folio wordmark */}
          <div className="flex min-w-0 items-center gap-2.5">
            <span className="regmark size-6 text-(--accent)">
              <svg viewBox="0 0 100 100" aria-hidden focusable="false">
                <circle cx="50" cy="50" r="34" fill="none" stroke="currentColor" strokeWidth="9" />
                <path d="M50 8v84M8 50h84" stroke="currentColor" strokeWidth="9" />
              </svg>
            </span>
            <span className="rubric hidden whitespace-nowrap font-display text-[17px] font-semibold tracking-tight min-[420px]:inline">
              NURU<span className="brand-text">XPLORE</span>
            </span>
          </div>

          {/* Tabs — mono folios */}
          <nav className="hidden h-full items-center gap-5 sm:flex">
            {(
              [
                { id: "chat", label: "Chat", num: "01" },
                { id: "research", label: "Research Expert", num: "02" },
              ] as const
            ).map(({ id, label, num }) => (
              <button
                key={id}
                onClick={() => setMode(id)}
                className={cn(
                  "folio relative flex h-full items-center gap-1.5 pb-1 transition-colors",
                  mode === id ? "text-(--accent)" : "text-muted-foreground hover:text-foreground",
                )}
              >
                <span className="font-mono">{num}</span>
                {label}
                <span
                  className={cn(
                    "absolute inset-x-0 bottom-0 h-[3px] bg-(--accent)",
                    mode === id ? "block" : "hidden",
                  )}
                />
              </button>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-2.5">
            {credits != null && (
              <span className="folio hidden items-center gap-1.5 border border-(--border) px-2.5 py-1 sm:inline-flex">
                <Zap className="size-3" />
                {credits} CR · <span className="text-(--accent)">CREDITS</span>
              </span>
            )}
            <span className="folio hidden text-muted-foreground md:inline">
              {me.user?.name || me.user?.email}
            </span>
            <Button variant="ghost" size="icon" onClick={toggle} aria-label="Toggle theme">
              {dark ? <Sun className="size-4" /> : <Moon className="size-4" />}
            </Button>
            <Button variant="ghost" size="icon" onClick={logout} aria-label="Log out">
              <LogOut className="size-4" />
            </Button>
          </div>
        </div>
      </header>

      <main className="flex w-full flex-1 flex-col px-3 py-4 sm:px-5 sm:py-6">
        {mode === "chat" ? (
          <ChatScreen onCredits={refreshMe} userKey={me.user?.email} />
        ) : (
          <ResearchScreen onCredits={refreshMe} />
        )}
      </main>
    </div>
  );
}
