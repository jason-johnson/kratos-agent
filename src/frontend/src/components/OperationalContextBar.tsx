"use client";

import { useEffect, useState } from "react";
import { getOperationalContext } from "@/lib/api";
import type { OperationalContext } from "@/types";

const ENVIRONMENT_STYLES: Record<OperationalContext["environmentClass"], string> = {
  production: "border-red-400/50 bg-red-500/10 text-red-700 dark:text-red-300",
  "non-production": "border-sky-400/40 bg-sky-500/10 text-sky-700 dark:text-sky-300",
  local: "border-emerald-400/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  unknown: "border-amber-400/50 bg-amber-500/10 text-amber-700 dark:text-amber-300",
};

export function OperationalContextBar({ compact = false }: { compact?: boolean }) {
  const [context, setContext] = useState<OperationalContext | null>(null);
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getOperationalContext()
      .then((value) => {
        if (!cancelled) setContext(value);
      })
      .catch(() => {
        if (!cancelled) setUnavailable(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (unavailable) {
    return (
      <div className="rounded-lg border border-amber-400/50 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
        Operational context unavailable. Confirm the environment and evidence target before taking action.
      </div>
    );
  }

  if (!context) {
    return <div className="h-9 animate-pulse rounded-lg bg-surface-2" aria-label="Loading operational context" />;
  }

  const environmentLabel =
    context.environmentClass === "production"
      ? `PRODUCTION · ${context.environment}`
      : `${context.environmentClass.toUpperCase()} · ${context.environment}`;

  return (
    <div
      className={`flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border px-3 ${
        compact ? "py-1.5" : "py-2.5"
      } ${ENVIRONMENT_STYLES[context.environmentClass]}`}
      role="status"
      aria-label="Operational context"
    >
      <span className="text-xs font-bold tracking-wide">{environmentLabel}</span>
      <span className="text-xs text-text">
        Evidence: <strong>{context.evidenceTarget}</strong> ({context.evidenceAccess})
      </span>
      <span className="text-xs text-text">
        Writes: <strong>approved ServiceNow updates only</strong>
      </span>
      {!context.evidenceTargetConfigured && (
        <span className="text-xs font-semibold">Evidence collection is unavailable.</span>
      )}
    </div>
  );
}
