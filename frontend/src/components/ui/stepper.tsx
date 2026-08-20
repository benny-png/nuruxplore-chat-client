import { Check, type LucideIcon } from "lucide-react";
import { cn } from "../../lib/utils";

export interface Step {
  id: string;
  label: string;
  icon?: LucideIcon;
}

export function Stepper({
  steps,
  current,
  onStep,
  className,
}: {
  steps: Step[];
  current: string;
  onStep?: (id: string) => void;
  className?: string;
}) {
  const idx = steps.findIndex((s) => s.id === current);
  return (
    <ol className={cn("flex items-center", className)}>
      {steps.map((s, i) => {
        const done = i < idx;
        const active = i === idx;
        const Icon = s.icon;
        return (
          <li key={s.id} className={cn("flex items-center", i < steps.length - 1 && "flex-1")}>
            <button
              type="button"
              onClick={() => onStep?.(s.id)}
              className="group flex items-center gap-2 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <span
                className={cn(
                  "flex size-7 shrink-0 items-center justify-center rounded-full border text-xs font-semibold transition-all",
                  done && "border-transparent brand-gradient text-white",
                  active && "border-ring text-foreground ring-2 ring-ring/20",
                  !done && !active && "border-border text-muted-foreground",
                )}
              >
                {done ? <Check className="size-3.5" /> : Icon ? <Icon className="size-3.5" /> : i + 1}
              </span>
              <span
                className={cn(
                  "hidden text-xs font-medium sm:block",
                  active ? "text-foreground" : "text-muted-foreground",
                )}
              >
                {s.label}
              </span>
            </button>
            {i < steps.length - 1 && (
              <span
                className={cn(
                  "mx-2 h-px min-w-4 flex-1 transition-colors",
                  i < idx ? "bg-primary" : "bg-border",
                )}
              />
            )}
          </li>
        );
      })}
    </ol>
  );
}
