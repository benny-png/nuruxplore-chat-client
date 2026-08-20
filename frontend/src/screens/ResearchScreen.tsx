import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { toast } from "sonner";
import {
  FilePlus2, UploadCloud, ClipboardList, PenLine, Rocket, CheckCircle2,
  Loader2, FileDown, FileText, AlertTriangle, Zap, FolderPlus, Send, Cpu as CpuIcon, Download, Eye,
} from "lucide-react";
import { api, errorTitle, type AgentContent, type ApiError, type GenerationStatus, type OutlineResult, type ResearchProfile } from "../lib/api";
import { cn } from "../lib/utils";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card";
import { Textarea } from "../components/ui/textarea";
import { Label } from "../components/ui/label";
import { Progress } from "../components/ui/progress";
import { Tabs, TabsList, TabsTrigger } from "../components/ui/tabs";
import { Stepper } from "../components/ui/stepper";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "../components/ui/dialog";

const STEPS = [
  { id: "create", label: "Create", icon: FolderPlus },
  { id: "upload", label: "Upload", icon: UploadCloud },
  { id: "profile", label: "Profile", icon: ClipboardList },
  { id: "outline", label: "Outline", icon: PenLine },
  { id: "generate", label: "Generate", icon: Rocket },
  { id: "done", label: "Done", icon: CheckCircle2 },
];

const COST: Record<string, string> = { proposal: "~108 credits", thesis: "400–600 credits" };
const POLL_MS = 2000;
const POLL_MAX = 360;

export function ResearchScreen({ onCredits, agentsOn }: { onCredits: () => void; agentsOn?: boolean }) {
  const [type, setType] = useState<"proposal" | "thesis">("proposal");
  const [topic, setTopic] = useState("");
  const [step, setStep] = useState("create");
  const [projectUuid, setProjectUuid] = useState<string | null>(null);

  // upload — the document role is derived from the mode: a thesis is built
  // FROM a proposal (upload a proposal doc); a proposal is built FROM data
  // (upload a dataset/survey). No manual role toggle.
  const [file, setFile] = useState<File | null>(null);
  const [drag, setDrag] = useState(false);

  // profile
  const [profileText, setProfileText] = useState("");
  const [profileBuilt, setProfileBuilt] = useState(false);

  // outline
  const [outlineText, setOutlineText] = useState("");

  // generation / progress
  const [gen, setGen] = useState<GenerationStatus | null>(null);
  const [polling, setPolling] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const [busy, setBusy] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const pollTries = useRef(0);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const fail = (err: unknown) => toast.error(errorTitle((err as ApiError).kind));

  const createProject = async () => {
    setBusy("create");
    // Topic is optional and only editable in Proposal mode (the field is hidden
    // for Thesis, which is driven by the uploaded proposal + its title). A
    // sensible default avoids a dead-end when the prompt is blank.
    const title = topic.trim() || (type === "thesis" ? "Untitled thesis" : "Untitled proposal");
    try {
      const r = await api<{ project_uuid: string }>("/api/local/projects", {
        method: "POST",
        body: { title, type, auto_title: !!topic.trim() },
      });
      setProjectUuid(r.project_uuid);
      setStep("upload");
      toast.success(topic.trim() ? "Project created" : "Project created — upload your data to begin");
      onCredits();
    } catch (err) {
      fail(err);
    } finally {
      setBusy(null);
    }
  };

  const uploadRole = type === "thesis" ? "proposal" : "dataset";
  const acceptExt = type === "thesis" ? ".pdf,.docx,.txt" : ".csv,.xlsx,.txt";

  const uploadSource = async () => {
    if (!projectUuid) return toast.error("Create a project first, then upload into it.");
    if (!file) return toast.error(`Choose a ${type === "thesis" ? "proposal" : "data"} file first.`);
    setBusy("upload");
    const role = uploadRole;
    const fd = new FormData();
    fd.append("file", file);
    fd.append("document_role", role);
    fd.append("type", role);
    try {
      await api(`/api/local/projects/${projectUuid}/upload`, { method: "POST", body: fd });
      toast.success(type === "thesis" ? "Proposal uploaded — building thesis from it" : "Data uploaded — building proposal from it");
      setStep("profile");
    } catch (err) {
      fail(err);
    } finally {
      setBusy(null);
    }
  };

  const buildProfile = async () => {
    if (!projectUuid) return;
    setBusy("profile");
    try {
      const r = await api<{ profile?: ResearchProfile }>(
        `/api/local/projects/${projectUuid}/build-research-profile`,
        { method: "POST" },
      );
      const p = (r.profile ?? r) as ResearchProfile;
      setProfileText(JSON.stringify(p, null, 2));
      setProfileBuilt(true);
      setStep("profile");
      onCredits();
    } catch (err) {
      fail(err);
    } finally {
      setBusy(null);
    }
  };

  const approveProfile = async () => {
    if (!projectUuid) return;
    let parsed: unknown;
    try {
      parsed = JSON.parse(profileText);
    } catch {
      return toast.error("The profile JSON is invalid — fix it and try again.");
    }
    setBusy("profile");
    try {
      await api(`/api/local/projects/${projectUuid}/approve-research-profile`, {
        method: "POST",
        body: { research_profile: parsed },
      });
      setStep("outline");
      toast.success("Profile approved");
    } catch (err) {
      fail(err);
    } finally {
      setBusy(null);
    }
  };

  const buildOutline = async () => {
    if (!projectUuid) return;
    setBusy("outline");
    try {
      const r = await api<OutlineResult>(`/api/local/projects/${projectUuid}/generate-outline`, {
        method: "POST",
      });
      setOutlineText(JSON.stringify(r.outline ?? r.sections ?? [], null, 1));
      setStep("generate");
      toast.success(r.message ?? "Outline generated");
      onCredits();
    } catch (err) {
      fail(err);
    } finally {
      setBusy(null);
    }
  };

  const startGenerate = async () => {
    if (!projectUuid) return;
    setConfirmOpen(false);
    setBusy("generate");
    try {
      await api(`/api/local/projects/${projectUuid}/generate-complete`, {
        method: "POST",
        body: { type },
      });
      setGen(null);
      pollTries.current = 0;
      setPolling(true);
      onCredits();
    } catch (err) {
      fail(err);
    } finally {
      setBusy(null);
    }
  };

  const pollStatus = useCallback(async () => {
    if (!projectUuid) return;
    try {
      const s = await api<GenerationStatus>(`/api/local/projects/${projectUuid}/generation-status`);
      setGen(s);
      if (s.status === "completed") {
        setPolling(false);
        setStep("done");
      } else if (s.status === "failed" || s.kind === "generation_failed") {
        setPolling(false);
        toast.error(s.message ?? errorTitle("generation_failed"));
        setStep("generate");
      }
    } catch (err) {
      // transient network during polling: keep trying unless we've hit the cap
    }
  }, [projectUuid]);

  useEffect(() => {
    if (!polling) return;
    pollStatus();
    pollTimer.current = setInterval(() => {
      pollTries.current += 1;
      if (pollTries.current >= POLL_MAX) {
        setPolling(false);
        toast.error("Generation is taking too long — stopped polling. Refresh to re-check.");
        return;
      }
      pollStatus();
    }, POLL_MS);
    return () => {
      if (pollTimer.current) clearInterval(pollTimer.current);
    };
  }, [polling, pollStatus]);

  return (
    <div className="grid gap-5">
      <Card>
        <CardContent className="grid gap-5 p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <CardTitle className="text-lg">Research Expert</CardTitle>
              <CardDescription>
                Turn a topic + source context into a full academic document, step by step.
              </CardDescription>
            </div>
            <Tabs value={type} onValueChange={(v) => setType(v as "proposal" | "thesis")}>
              <TabsList>
                <TabsTrigger value="proposal">Proposal</TabsTrigger>
                <TabsTrigger value="thesis">Thesis</TabsTrigger>
              </TabsList>
            </Tabs>
          </div>

          {type === "proposal" && (
            <div className="grid gap-2">
              <Label htmlFor="topic">Research topic / prompt</Label>
              <Textarea
                id="topic"
                rows={3}
                value={topic}
                onChange={(e) => setTopic(e.target.value)}
                placeholder="e.g. Impact of mobile money adoption on the financial inclusion of smallholder farmers in Tanzania"
              />
            </div>
          )}
        </CardContent>
      </Card>

      {/* stepper */}
      {projectUuid && (
        <Card className="p-4">
          <Stepper steps={STEPS} current={step} />
        </Card>
      )}

      {/* ---------- CREATE ---------- */}
      {!projectUuid && (
        <Card className="flex flex-col items-center gap-3 p-10 text-center">
          <div className="brand-gradient flex size-12 items-center justify-center rounded-2xl text-white shadow-md">
            <FilePlus2 className="size-6" />
          </div>
          <CardDescription>
            Uploading the right source context is what lets the AI build an accurate proposal or thesis.
          </CardDescription>
          <Button size="lg" onClick={createProject} disabled={busy === "create"}>
            {busy === "create" ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
            Create {type} project
          </Button>
        </Card>
      )}

      {/* ---------- UPLOAD ---------- */}
      {projectUuid && step === "upload" && (
        <Card>
          <CardHeader>
            <CardTitle>{type === "thesis" ? "Upload your proposal" : "Upload your data"}</CardTitle>
            <CardDescription>
              {type === "thesis"
                ? "A thesis is written from a real proposal. Upload the approved proposal (PDF/DOCX) — its title & scope drive the full thesis."
                : "A proposal is built from your data. Upload the dataset/survey (CSV/XLSX) — the findings drive a grounded proposal."}
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-3">
            <label
              onDragOver={(e) => {
                e.preventDefault();
                setDrag(true);
              }}
              onDragLeave={() => setDrag(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDrag(false);
                const f = e.dataTransfer.files?.[0];
                if (f) setFile(f);
              }}
              className={cn(
                "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed px-6 py-10 text-center transition-colors",
                drag ? "border-primary bg-accent" : "border-border hover:border-primary/60 hover:bg-muted/40",
              )}
            >
              <UploadCloud className={cn("size-8", drag ? "text-primary" : "text-muted-foreground")} />
              <p className="text-sm font-medium">
                {file ? file.name : `Drag & drop your ${type === "thesis" ? "proposal" : "data"} file, or click to browse`}
              </p>
              <p className="text-xs text-muted-foreground">
                {type === "thesis" ? "PDF · DOCX · TXT" : "CSV · XLSX · TXT"}
              </p>
              <input
                ref={fileInput}
                type="file"
                className="hidden"
                accept={acceptExt}
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
            </label>

            <div className="flex flex-wrap items-end justify-end gap-3">
              <Button onClick={uploadSource} disabled={!file || busy === "upload"}>
                {busy === "upload" ? <Loader2 className="size-4 animate-spin" /> : <UploadCloud className="size-4" />}
                {type === "thesis" ? "Upload proposal" : "Upload data"}
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* ---------- PROFILE ---------- */}
      {projectUuid && step === "profile" && (
        <Card>
          <CardHeader>
            <CardTitle>Research profile</CardTitle>
            <CardDescription>
              The AI builds a structured profile from your topic + source. Review and edit it before approving.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-3">
            {!profileBuilt ? (
              <Button onClick={buildProfile} disabled={busy === "profile"}>
                {busy === "profile" ? <Loader2 className="size-4 animate-spin" /> : <ClipboardList className="size-4" />}
                Build research profile (+3)
              </Button>
            ) : (
              <>
                <Textarea
                  value={profileText}
                  onChange={(e) => setProfileText(e.target.value)}
                  spellCheck={false}
                  className="min-h-[260px] font-mono text-xs"
                />
                <div className="flex justify-end">
                  <Button onClick={approveProfile} disabled={busy === "profile"}>
                    <CheckCircle2 className="size-4" />
                    Approve profile
                  </Button>
                </div>
              </>
            )}
          </CardContent>
        </Card>
      )}

      {/* ---------- OUTLINE ---------- */}
      {projectUuid && step === "outline" && (
        <Card>
          <CardHeader>
            <CardTitle>Outline</CardTitle>
            <CardDescription>Generate the chapter outline from your approved profile.</CardDescription>
          </CardHeader>
          <CardContent>
            <Button onClick={buildOutline} disabled={busy === "outline"}>
              {busy === "outline" ? <Loader2 className="size-4 animate-spin" /> : <PenLine className="size-4" />}
              Generate outline (+5)
            </Button>
          </CardContent>
        </Card>
      )}

      {/* ---------- GENERATE ---------- */}
      {projectUuid && step === "generate" && !polling && (
        <Card>
          <CardHeader>
            <CardTitle>Generate document</CardTitle>
            <CardDescription>
              {agentsOn ? (
                <>
                  Generate a full {type} with the DeepSeek multi-agent graph (cheap, no credit queue).
                </>
              ) : (
                <>
                  Queues a full {type} in the background and charges{" "}
                  <span className="font-semibold text-foreground">{COST[type]}</span> up front.
                </>
              )}
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-3">
            {outlineText && (
              <pre className="scroll-thin max-h-48 overflow-auto rounded-lg border bg-muted/40 p-3 font-mono text-xs text-muted-foreground">
                {outlineText}
              </pre>
            )}
            <div className="flex flex-wrap gap-2">
              <Button
                onClick={() => (agentsOn ? startGenerate() : setConfirmOpen(true))}
                variant={agentsOn ? "primary" : "destructive"}
              >
                <Rocket className="size-4" />
                {agentsOn ? `Generate ${type} (DeepSeek agents)` : `Generate ${type} (${COST[type]})`}
              </Button>
              {type === "thesis" && !agentsOn && (
                <p className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
                  <AlertTriangle className="size-3.5 text-warning" />
                  Thesis costs 400–600 credits — most of a full budget on its own.
                </p>
              )}
              {agentsOn && (
                <p className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
                  <CpuIcon className="size-3.5" /> Orchestrator plans → full-length chapters in parallel → editor frame.
                </p>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      {/* ---------- PROGRESS ---------- */}
      {projectUuid && step === "generate" && polling && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Loader2 className="size-4 animate-spin text-primary" />
              Generating your {type}
            </CardTitle>
            <CardDescription>{gen?.current_step || "Working…"}</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4">
            <div className="flex items-center gap-3">
              <Progress value={gen?.progress ?? 0} className="h-2.5" />
              <span className="w-10 text-right text-sm font-medium tabular-nums">
                {Math.round(gen?.progress ?? 0)}%
              </span>
            </div>
            {gen?.steps && gen.steps.length > 0 && (
              <ul className="grid gap-1.5">
                {gen.steps.map((s, i) => (
                  <li key={i} className="flex items-center gap-2 text-sm">
                    <span
                      className={cn(
                        "size-4 shrink-0 rounded-full",
                        s.status === "completed"
                          ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                          : s.status === "failed"
                            ? "bg-destructive/15 text-destructive"
                            : "bg-muted text-muted-foreground",
                      )}
                    >
                      {s.status === "completed" ? "✓" : s.status === "failed" ? "✕" : ""}
                    </span>
                    <span
                      className={cn(
                        s.status === "completed"
                          ? "text-foreground"
                          : s.status === "active" || s.status === "processing"
                            ? "text-foreground"
                            : "text-muted-foreground",
                      )}
                    >
                      {s.message || s.step}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      )}

      {/* ---------- DONE ---------- */}
      {projectUuid && step === "done" && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-emerald-600 dark:text-emerald-400">
              <CheckCircle2 className="size-5" />
              Document generated
            </CardTitle>
            <CardDescription>
              {agentsOn
                ? `Generated with DeepSeek agents${gen?.word_count ? ` · ${gen.word_count.toLocaleString()} words` : ""}. Review and download below.`
                : `${gen?.word_count ? `${gen.word_count.toLocaleString()} words · ` : ""}export your finished ${type}.`}
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            {agentsOn ? (
              <AgentResult projectUuid={projectUuid} />
            ) : (
              <>
                <ExportButton fmt="pdf" label="Export PDF" icon={<FileText className="size-4" />} projectUuid={projectUuid} onCredits={onCredits} />
                <ExportButton fmt="word" label="Export Word" icon={<FileDown className="size-4" />} projectUuid={projectUuid} onCredits={onCredits} />
              </>
            )}
          </CardContent>
        </Card>
      )}

      {/* confirm dialog */}
      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Queue {type} generation?</DialogTitle>
            <DialogDescription className="grid gap-2">
              <span>
                This charges <b>{COST[type]}</b> up front and can't be undone. Your budget is fixed.
              </span>
              <span className="inline-flex items-center gap-1.5 text-muted-foreground">
                <Zap className="size-3.5" /> Continue only if you're ready.
              </span>
            </DialogDescription>
          </DialogHeader>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setConfirmOpen(false)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={startGenerate} disabled={busy === "generate"}>
              {busy === "generate" ? <Loader2 className="size-4 animate-spin" /> : <Rocket className="size-4" />}
              Generate
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function ExportButton({
  fmt,
  label,
  icon,
  projectUuid,
  onCredits,
}: {
  fmt: "pdf" | "word";
  label: string;
  icon: ReactNode;
  projectUuid: string;
  onCredits: () => void;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <Button
      variant="secondary"
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        try {
          const r = await api<{ download_url: string }>(`/api/local/projects/${projectUuid}/export/${fmt}`, {
            method: "POST",
          });
          window.open(r.download_url, "_blank", "noopener");
          toast.success(fmt === "word" ? "Word export ready" : "PDF export ready");
          onCredits();
        } catch (err) {
          toast.error(errorTitle((err as ApiError).kind));
        } finally {
          setBusy(false);
        }
      }}
    >
      {busy ? <Loader2 className="size-4 animate-spin" /> : icon}
      {label}
    </Button>
  );
}

/** DeepSeek path: fetch the agent-generated document, preview it and allow a
 *  local .md download (no nuruxplore export needed). */
function AgentResult({ projectUuid }: { projectUuid: string }) {
  const [data, setData] = useState<AgentContent | null>(null);
  const [busy, setBusy] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const c = await api<AgentContent>(`/api/local/projects/${projectUuid}/content`);
        setData(c);
      } catch {
        toast.error("Could not load the generated document.");
      } finally {
        setBusy(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectUuid]);

  if (busy) return <Loader2 className="size-4 animate-spin text-primary" />;
  if (!data?.text) return <p className="text-sm text-muted-foreground">No document text available.</p>;

  const download = () => {
    const blob = new Blob([`# ${data.title ?? "Research Document"}\n\n${data.text}`], {
      type: "text/markdown",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${(data.title ?? "document").replace(/[^\w\-]+/g, "_").slice(0, 60)}.md`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="grid w-full gap-3">
      <h3 className="font-display text-lg font-medium leading-snug text-foreground">
        {data.title || "Generated document"}
      </h3>
      <div className="flex flex-wrap items-center gap-2">
        <span className="folio inline-flex items-center gap-1.5 border border-(--accent)/40 px-2 py-1 text-xs">
          <CpuIcon className="size-3" /> DEEPSEEK · {data.word_count?.toLocaleString() ?? 0} WORDS
        </span>
        <Button variant="secondary" size="sm" onClick={download} className="ml-auto">
          <Download className="size-4" /> Download .md
        </Button>
      </div>
      <details className="rounded-lg border border-(--border)">
        <summary className="flex cursor-pointer items-center gap-2 px-3 py-2 text-sm text-muted-foreground hover:text-foreground">
          <Eye className="size-4" /> Preview document
        </summary>
        <pre className="scroll-thin max-h-[28rem] overflow-auto border-t border-(--border) whitespace-pre-wrap p-4 font-mono text-xs leading-relaxed">
          {data.text}
        </pre>
      </details>
    </div>
  );
}
