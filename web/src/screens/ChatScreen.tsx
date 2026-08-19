import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Send, Plus, MessageSquareText, Loader2, Trash2 } from "lucide-react";
import { api, errorTitle, type ApiError } from "../lib/api";
import { cn } from "../lib/utils";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";

interface Msg {
  role: "user" | "ai" | "hint";
  text: string;
}

interface Session {
  /** Stable LOCAL identity (never changes) — used for selection + React keys. */
  key: string;
  /** Backend project uuid, "" until the first message creates it lazily. */
  id: string;
  title: string;
  msgs: Msg[];
  createdAt: number;
}

const SUGGESTIONS = [
  "Summarize my survey's key findings",
  "Suggest strong research questions",
  "Help me structure a literature review",
];

const MAX_TEXT_COL = "w-full max-w-[680px]"; // left-anchored readable rail, not edge-to-edge

function newKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : "s-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 8);
}

function makeSession(): Session {
  return { key: newKey(), id: "", title: "New chat", msgs: [], createdAt: Date.now() };
}

function readSessions(storageKey: string): Session[] {
  try {
    const raw = localStorage.getItem(storageKey);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((s) => s && typeof s.key === "string")
      .map((s) => ({
        key: s.key,
        id: typeof s.id === "string" ? s.id : "",
        title: s.title || "New chat",
        msgs: Array.isArray(s.msgs) ? s.msgs : [],
        createdAt: typeof s.createdAt === "number" ? s.createdAt : 0,
      }));
  } catch {
    return [];
  }
}

export function ChatScreen({ onCredits, userKey }: { onCredits: () => void; userKey?: string }) {
  const storageKey = `nx-chat-sessions${userKey ? `:${userKey}` : ""}`;

  const [sessions, setSessions] = useState<Session[]>(() => readSessions(storageKey));
  const [activeKey, setActiveKey] = useState<string | null>(() => sessions[0]?.key ?? null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [loadingMsgs, setLoadingMsgs] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);
  const mountedRef = useRef(false);
  // Refs mirror the latest input + sessions so click/Enter handlers never read
  // a stale closure value (the old code could silently drop fast sends).
  const inputRef = useRef("");
  const sessionsRef = useRef(sessions);
  useEffect(() => {
    sessionsRef.current = sessions;
  }, [sessions]);
  const updateInput = (v: string) => {
    inputRef.current = v;
    setInput(v);
  };

  // Stick an empty first session so the pane is never a blank void.
  useEffect(() => {
    if (sessions.length === 0) {
      const s = makeSession();
      setSessions([s]);
      setActiveKey(s.key);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Persist whenever sessions change (skip the very first mount write race).
  useEffect(() => {
    if (!mountedRef.current) {
      mountedRef.current = true;
      return;
    }
    try {
      localStorage.setItem(storageKey, JSON.stringify(sessions));
    } catch {
      /* storage full/unavailable — sessions still work in-memory */
    }
  }, [sessions, storageKey]);

  const active = sessions.find((s) => s.key === activeKey) ?? null;

  const patchSession = (key: string, patch: Partial<Session>) =>
    setSessions((prev) => prev.map((s) => (s.key === key ? { ...s, ...patch } : s)));

  const scrollDown = () => {
    requestAnimationFrame(() => {
      logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: "smooth" });
    });
  };

  const selectSession = (s: Session) => {
    setActiveKey(s.key);
    setInput("");
    if (s.id) loadHistory(s.id); // best-effort: pull any server-side history for this project
  };

  /** One-time attempt to hydrate a session from the live API; never breaks the UI. */
  async function loadHistory(projectId: string) {
    if (!projectId) return;
    try {
      const body = await api<unknown>(`/api/local/projects/${projectId}/messages`);
      const items = Array.isArray(body) ? body : (body as any)?.messages;
      if (!Array.isArray(items) || items.length === 0) return;
      const msgs: Msg[] = [];
      for (const it of items) {
        const text =
          typeof it?.content === "string"
            ? it.content
            : typeof it?.message === "string"
              ? it.message
              : typeof it?.text === "string"
                ? it.text
                : "";
        if (!text) continue;
        const role = it?.role === "user" || it?.role === "human" ? "user" : "ai";
        msgs.push({ role: role as "user" | "ai", text });
      }
      setLoadingMsgs(false);
      if (msgs.length) {
        const s = sessionsRef.current.find((x) => x.key === activeKey);
        if (s) patchSession(s.key, { msgs });
        scrollDown();
      }
    } catch {
      /* remote history unavailable — local copy still shows */
    }
  }

  /** Create a backend project (real uuid) for a local session that has none yet. */
  async function ensureProject(s: Session): Promise<string | null> {
    if (s.id) return s.id;
    try {
      const r = await api<{ project_uuid: string }>("/api/local/projects", {
        method: "POST",
        body: { title: s.title.trim() || "Chat session", type: "chat" },
      });
      patchSession(s.key, { id: r.project_uuid });
      return r.project_uuid;
    } catch (err) {
      toast.error(errorTitle((err as ApiError).kind));
      return null;
    }
  }

  async function newSession() {
    const s = makeSession();
    setSessions((prev) => [s, ...prev]);
    setActiveKey(s.key);
    setInput("");
    inputRef.current = "";
  }

  async function send(raw?: string) {
    const text = (raw ?? inputRef.current).trim();
    if (!text || busy) return;
    inputRef.current = "";
    setInput("");
    setBusy(true);
    // Resolve the session by its stable LOCAL key, from the latest state (ref).
    const cur = sessionsRef.current.find((s) => s.key === activeKey) ?? null;
    if (!cur) {
      setBusy(false);
      return;
    }
    const rename = cur.title === "New chat" ? text.slice(0, 48) : cur.title;
    const same = (s: Session) => s.key === activeKey;
    setSessions((prev) =>
      prev.map((s) => (same(s) ? { ...s, title: rename, msgs: [...s.msgs, { role: "user", text }] } : s)),
    );
    scrollDown();
    const pid = await ensureProject(cur);
    if (!pid) {
      setBusy(false);
      return;
    }
    try {
      const r = await api<{ reply: string; credits_remaining?: number }>("/api/local/chat", {
        method: "POST",
        body: { project_uuid: pid, message: text },
      });
      setSessions((prev) =>
        prev.map((s) =>
          same(s) ? { ...s, id: pid, msgs: [...s.msgs, { role: "ai", text: r.reply || "(no reply)" }] } : s,
        ),
      );
      scrollDown();
      onCredits();
    } catch (err) {
      setSessions((prev) =>
        prev.map((s) =>
          same(s) ? { ...s, id: pid, msgs: [...s.msgs, { role: "ai", text: `⚠ ${errorTitle((err as ApiError).kind)}` }] } : s,
        ),
      );
    } finally {
      setBusy(false);
    }
  }

  function removeSession(key: string) {
    setSessions((prev) => {
      const next = prev.filter((s) => s.key !== key);
      if (activeKey === key) setActiveKey(next[0]?.key ?? null);
      return next.length ? next : [];
    });
  }

  const msgs = active?.msgs ?? [];

  return (
    <div className="flex h-[calc(100dvh-11rem)] min-h-[28rem] flex-col gap-3 sm:h-[calc(100dvh-10rem)] sm:flex-row">
      {/* ---------- Sidebar: chat management ---------- */}
      <aside className="flex w-full shrink-0 flex-col overflow-hidden rounded-lg border border-(--border) bg-(--sidebar) sm:h-full sm:w-72">
        <div className="flex items-center justify-between gap-2 border-b border-(--border) px-3 py-2.5">
          <span className="folio">Session</span>
          <Button size="sm" onClick={newSession} className="h-7 gap-1 rounded-md px-2.5 text-[13px] font-semibold">
            <Plus className="size-3.5" />
            New
          </Button>
        </div>

        <div className="flex gap-1 overflow-x-auto p-2 sm:flex-1 sm:flex-col sm:overflow-y-auto">
          {sessions.map((s) => {
            const isActive = s.key === activeKey;
            return (
              <div
                key={s.key}
                onClick={() => selectSession(s)}
                className={cn(
                  "group flex w-full min-w-0 shrink-0 cursor-pointer items-center gap-2 border-l-2 px-2.5 py-2 text-left text-sm transition-colors",
                  isActive
                    ? "border-l-(--accent) bg-accent text-accent-foreground"
                    : "border-l-transparent text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
              >
                <MessageSquareText className="size-4 shrink-0" />
                <span className="min-w-0 flex-1 truncate">{s.title}</span>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    removeSession(s.key);
                  }}
                  aria-label="Remove chat"
                  className="shrink-0 rounded p-1 text-muted-foreground/60 opacity-0 transition-opacity hover:bg-background hover:text-destructive group-hover:opacity-100"
                >
                  <Trash2 className="size-3.5" />
                </button>
              </div>
            );
          })}
          {sessions.length === 0 && (
            <p className="px-2 py-6 text-center text-sm text-muted-foreground">No chats yet — start one above.</p>
          )}
        </div>
      </aside>

      {/* ---------- Chat pane ---------- */}
      <section className="flex min-w-0 flex-1 flex-col overflow-hidden rounded-lg border border-(--border) bg-card shadow-sm">
        {/* log */}
        <div ref={logRef} className="scroll-thin flex-1 overflow-y-auto">
          {msgs.length === 0 ? (
            <div className="grid h-full place-items-center px-4">
              <div className="flex max-w-md flex-col items-start gap-4">
                <div className="brand-gradient flex size-10 items-center justify-center rounded-md text-white shadow-sm">
                  <MessageSquareText className="size-5" />
                </div>
                <div className="grid gap-1.5">
                  <span className="folio">01 · Greet the model</span>
                  <p className="rubric font-display text-2xl font-medium leading-tight text-foreground">
                    Chat with NuruXplore
                  </p>
                  <p className="text-sm text-muted-foreground">
                    Ask anything about your research — each reply uses 1 credit.
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  {SUGGESTIONS.map((s) => (
                    <button
                      key={s}
                      onClick={() => updateInput(s)}
                      className="rounded-md border border-(--border) bg-(--surface) px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:border-(--accent) hover:text-foreground"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <div className={cn("flex flex-col gap-3 p-4 pb-6", MAX_TEXT_COL)}>
              {msgs.map((m, i) => (
                <div
                  key={i}
                  className={cn(
                    "max-w-[85%] whitespace-pre-wrap text-sm leading-relaxed sm:max-w-[75%]",
                    m.role === "user" &&
                      "self-end rounded-lg rounded-br-sm bg-[var(--surface-ink)] px-4 py-2.5 text-[#f7f1e6] shadow-sm",
                    m.role === "ai" &&
                      "self-start rounded-lg rounded-bl-sm border border-(--border) bg-card px-4 py-2.5 text-foreground",
                    m.role === "hint" &&
                      "self-center rounded-full bg-muted/70 px-3.5 py-1.5 text-xs text-muted-foreground",
                  )}
                >
                  {m.text}
                </div>
              ))}
              {busy && (
                <div className="inline-flex items-center gap-2 self-start rounded-lg rounded-bl-sm bg-muted px-3.5 py-2.5 text-sm text-muted-foreground">
                  <Loader2 className="size-3.5 animate-spin" /> thinking…
                </div>
              )}
            </div>
          )}
        </div>

        {/* composer — left-anchored readable rail */}
        <div className="border-t bg-(--sidebar)">
          <div className={cn("flex flex-col gap-2 p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]", MAX_TEXT_COL)}>
            <div className="flex items-center gap-2">
              <Input
                value={input}
                onChange={(e) => updateInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && send()}
                placeholder="Ask about your research…"
                disabled={busy || loadingMsgs}
                className="h-10 rounded-md border-(--border-strong) sm:h-9"
              />
              <Button
                size="icon"
                onClick={() => send()}
                disabled={busy || loadingMsgs}
                aria-label="Send"
                className="size-10 shrink-0 rounded-md sm:size-9"
              >
                <Send className="size-4" />
              </Button>
            </div>
            <p className="folio">
              Use Research Expert for proposals &amp; theses · 1 CR per reply
            </p>
          </div>
        </div>
      </section>
    </div>
  );
}
