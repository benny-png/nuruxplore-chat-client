import { useEffect, useState, type FormEvent } from "react";
import { Sparkles, Loader2, Eye, EyeOff } from "lucide-react";

import { api, errorTitle, type Me } from "../lib/api";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card";

const REMEMBER_KEY = "nx_remembered_email";

export function LoginScreen({ onLogin }: { onLogin: (me: Me) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [remember, setRemember] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const saved = localStorage.getItem(REMEMBER_KEY);
    if (saved) {
      setEmail(saved);
      setRemember(true);
    }
  }, []);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const me = await api<Me>("/api/local/login", {
        method: "POST",
        body: { email, password },
      });
      if (remember) localStorage.setItem(REMEMBER_KEY, email);
      else localStorage.removeItem(REMEMBER_KEY);
      onLogin(me);
    } catch (err) {
      setError(errorTitle((err as { kind?: import("../lib/api").ErrorKind }).kind));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="glow-shell relative flex min-h-screen items-center justify-center overflow-hidden px-4 py-10">
      <Card className="w-full max-w-sm rounded-lg border border-(--border) bg-(--surface) p-8 shadow-[0_1px_2px_rgba(0,0,0,0.12)]">
        <CardHeader className="items-start gap-1 pb-5 text-left">
          <div className="brand-gradient mb-1 flex size-10 items-center justify-center rounded-md text-white">
            <Sparkles className="size-5" />
          </div>
          <span className="folio">Account</span>
          <CardTitle className="rubric font-display text-2xl font-medium tracking-tight">Welcome back</CardTitle>
          <CardDescription>Log in to your NuruXplore workspace</CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          <form onSubmit={submit} className="grid gap-4">
            <div className="grid gap-1.5">
              <Label htmlFor="email">Email address</Label>
              <Input
                id="email"
                type="email"
                required
                autoComplete="username"
                placeholder="you@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>

            <div className="grid gap-1.5">
              <Label htmlFor="password">Password</Label>
              <div className="relative">
                <Input
                  id="password"
                  type={showPw ? "text" : "password"}
                  required
                  autoComplete="current-password"
                  placeholder="Enter your password"
                  className="pr-10"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
                <button
                  type="button"
                  onClick={() => setShowPw((v) => !v)}
                  aria-label={showPw ? "Hide password" : "Show password"}
                  className="absolute top-1/2 right-3 -translate-y-1/2 text-muted-foreground transition-colors hover:text-foreground"
                >
                  {showPw ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                </button>
              </div>
            </div>

            <div className="flex items-center justify-between text-sm">
              <label className="flex cursor-pointer items-center gap-2 text-muted-foreground">
                <input
                  type="checkbox"
                  checked={remember}
                  onChange={(e) => setRemember(e.target.checked)}
                  className="size-4 accent-[var(--primary)]"
                />
                Remember me
              </label>
              <a
                href="https://nuruxplore.com/forgot-password"
                target="_blank"
                rel="noreferrer"
                className="font-medium text-primary hover:underline"
              >
                Forgot password?
              </a>
            </div>

            {error && (
              <p className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
                {error}
              </p>
            )}

            <Button type="submit" size="lg" disabled={loading} className="w-full rounded-xl">
              {loading && <Loader2 className="size-4 animate-spin" />}
              {loading ? "Logging in…" : "Log in"}
            </Button>
          </form>

          <div className="my-5 h-px bg-border" />

          <p className="text-center text-sm text-muted-foreground">
            Don&apos;t have an account?{" "}
            <a
              href="https://nuruxplore.com/register"
              target="_blank"
              rel="noreferrer"
              className="font-medium text-primary hover:underline"
            >
              Create one free
            </a>
          </p>

          <p className="mt-6 text-center text-[11px] text-muted-foreground">
            The app never stores or displays your token.
          </p>

          <div className="mt-3 flex items-center justify-center gap-3 text-[11px] text-muted-foreground">
            <a className="hover:text-foreground" href="https://nuruxplore.com/privacy" target="_blank" rel="noreferrer">Privacy</a>
            <span aria-hidden>·</span>
            <a className="hover:text-foreground" href="https://nuruxplore.com/terms" target="_blank" rel="noreferrer">Terms</a>
            <span aria-hidden>·</span>
            <a className="hover:text-foreground" href="https://nuruxplore.com/cookies" target="_blank" rel="noreferrer">Cookies</a>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
