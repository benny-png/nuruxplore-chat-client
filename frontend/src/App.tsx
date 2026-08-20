import { useCallback, useEffect, useState } from "react";
import { Toaster } from "sonner";
import { Moon, Sun, LogOut, Zap, Cpu } from "lucide-react";
import { api, type Me, type Prefs } from "./lib/api";
import { cn } from "./lib/utils";
import { Button } from "./components/ui/button";
import { LoginScreen } from "./screens/LoginScreen";
import { ChatScreen } from "./screens/ChatScreen";
import { ResearchScreen } from "./screens/ResearchScreen";

type Mode = "chat" | "research";

const THEME_KEY = "nx-theme";
const AGENTS_KEY = "nx-agents";

/** DeepSeek agent toggle — mirrored to localStorage and the backend session.
 *  When the server has no key configured, the switch shows as disabled. */
function useAgentsPref() {
  // Agent mode is the default; a saved explicit preference overrides it.
  const [on, setOn] = useState<boolean>(() => {
    const saved = localStorage.getItem(AGENTS_KEY);
    return saved !== null ? saved === "1" : true;
  });
  const [available, setAvailable] = useState(false);
  const [model, setModel] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);

  const sync = useCallback(async () => {
    try {
      const p = await api<Prefs>("/api/local/prefs");
      setOn(p.use_agents);
      setAvailable(p.agents_available);
      setModel(p.model ?? null);
      localStorage.setItem(AGENTS_KEY, p.use_agents ? "1" : "0");
    } catch {
      /* logged out / offline — keep local state */
    }
  }, []);

  useEffect(() => {
    sync();
  }, [sync]);

  const toggle = async () => {
    const next = !on;
    setOn(next);
    localStorage.setItem(AGENTS_KEY, next ? "1" : "0");
    setSyncing(true);
    try {
      const p = await api<Prefs>("/api/local/prefs", { method: "POST", body: { use_agents: next } });
      setOn(p.use_agents);
      setAvailable(p.agents_available);
      setModel(p.model ?? null);
      localStorage.setItem(AGENTS_KEY, p.use_agents ? "1" : "0");
    } catch {
      setOn(!next); // revert on failure
      localStorage.setItem(AGENTS_KEY, !next ? "1" : "0");
    } finally {
      setSyncing(false);
    }
  };

  return { on, available, model, syncing, toggle, sync };
}

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
  const agents = useAgentsPref();
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

            {/* DeepSeek agent toggle */}
            <button
              role="switch"
              aria-checked={agents.on}
              aria-label="Toggle DeepSeek agents"
              title={
                agents.available
                  ? `DeepSeek agents ${agents.on ? "on" : "off"}${agents.model ? ` (${agents.model})` : ""}`
                  : "DeepSeek agents unavailable — no API key configured"
              }
              onClick={() => agents.available && !agents.syncing && agents.toggle()}
              disabled={!agents.available || agents.syncing}
              className={cn(
                "hidden items-center gap-1.5 border border-(--border) px-2 py-1 text-xs sm:inline-flex",
                agents.on ? "border-(--accent)/50 text-(--accent)" : "text-muted-foreground",
                !agents.available && "cursor-not-allowed opacity-45",
              )}
            >
              <Cpu className="size-3.5" />
              <span className="font-mono">{agents.on ? "AGENTS ON" : "AGENTS OFF"}</span>
              <span
                className={cn(
                  "inline-block size-2 rounded-full transition-colors",
                  agents.on ? "bg-(--accent)" : "bg-muted-foreground/40",
                )}
              />
            </button>

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
          <ChatScreen onCredits={refreshMe} userKey={me.user?.email} agentsOn={agents.on} />
        ) : (
          <ResearchScreen onCredits={refreshMe} agentsOn={agents.on} />
        )}
      </main>
    </div>
  );
}
