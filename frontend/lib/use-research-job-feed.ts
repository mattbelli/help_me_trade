"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  getResearchJob,
  listResearchJobs,
  runResearch,
  type ResearchActivityEvent,
  type ResearchDepth,
  type ResearchJob,
  type ResearchRecipe,
} from "@/lib/research";

const POLL_INTERVAL_MS = 1500;
const POLL_EVENT_LIMIT = 200;

export type ResearchRunParams = {
  topic: string;
  symbol?: string;
  depth?: ResearchDepth;
  recipe?: ResearchRecipe;
  symbols?: string[];
};

export type UseResearchJobFeedOptions = {
  onComplete?: (artifactId: number) => void | Promise<void>;
  pollIntervalMs?: number;
};

function applyPhaseDetail(
  event: ResearchActivityEvent,
  setPhaseDetail: (detail: string) => void,
  setStreamWarning: (warning: string | null) => void,
) {
  if (event.phase === "web_warning" && event.warning) {
    setStreamWarning(String(event.warning));
  } else if (event.phase === "searching" && event.query) {
    setPhaseDetail(String(event.query));
  } else if (event.phase === "sources" && event.total != null) {
    setPhaseDetail(`${event.total} sources`);
  } else if (event.phase === "writing") {
    setPhaseDetail(`${Number(event.findings ?? 0)} findings`);
  }
}

function resolveArtifactId(job: ResearchJob): number | null {
  if (job.artifact_id != null) return job.artifact_id;
  const fromResult = job.result?.artifact_id;
  return fromResult != null ? Number(fromResult) : null;
}

export function useResearchJobFeed(options: UseResearchJobFeedOptions = {}) {
  const pollIntervalMs = options.pollIntervalMs ?? POLL_INTERVAL_MS;
  const onCompleteRef = useRef(options.onComplete);
  onCompleteRef.current = options.onComplete;

  const [jobId, setJobId] = useState<string | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [activityEvents, setActivityEvents] = useState<ResearchActivityEvent[]>([]);
  const [phase, setPhase] = useState<string | null>(null);
  const [phaseDetail, setPhaseDetail] = useState("");
  const [streamWarning, setStreamWarning] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const processJobUpdate = useCallback((job: ResearchJob) => {
    const events = job.events ?? [];
    setActivityEvents(events);
    setPhase(job.phase ?? events.at(-1)?.phase ?? job.status);

    const latest = events.at(-1);
    if (latest) {
      applyPhaseDetail(latest, setPhaseDetail, setStreamWarning);
    }

    if (job.status === "completed" || job.status === "failed") {
      setIsRunning(false);
      setPhase(null);
      setPhaseDetail("");
      if (job.status === "failed") {
        setError(job.error ?? "Research failed");
        return;
      }
      const artifactId = resolveArtifactId(job);
      if (artifactId != null) {
        void onCompleteRef.current?.(artifactId);
      }
    }
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function reattach() {
      try {
        const jobs = await listResearchJobs();
        const active = jobs.find(
          (job) => job.status === "queued" || job.status === "running",
        );
        if (!active || cancelled) return;
        setJobId(active.job_id);
        setIsRunning(true);
        setError(null);
        const job = await getResearchJob(active.job_id, POLL_EVENT_LIMIT);
        if (!cancelled) {
          processJobUpdate(job);
        }
      } catch {
        // Reattach is best-effort; ignore list/poll failures on mount.
      }
    }

    void reattach();
    return () => {
      cancelled = true;
    };
  }, [processJobUpdate]);

  useEffect(() => {
    if (!jobId || !isRunning) return;

    let cancelled = false;
    const activeJobId = jobId;

    async function poll() {
      try {
        const job = await getResearchJob(activeJobId, POLL_EVENT_LIMIT);
        if (!cancelled) {
          processJobUpdate(job);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Research poll failed");
          setIsRunning(false);
        }
      }
    }

    void poll();
    const interval = setInterval(() => void poll(), pollIntervalMs);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [jobId, isRunning, pollIntervalMs, processJobUpdate]);

  const startResearch = useCallback(async (params: ResearchRunParams) => {
    setIsRunning(true);
    setError(null);
    setPhase("started");
    setPhaseDetail("");
    setActivityEvents([]);
    setStreamWarning(null);

    try {
      const queued = await runResearch(params);
      setJobId(queued.job_id);
    } catch (err) {
      setIsRunning(false);
      setPhase(null);
      setPhaseDetail("");
      setError(err instanceof Error ? err.message : "Research failed");
    }
  }, []);

  return {
    isRunning,
    activityEvents,
    phase,
    phaseDetail,
    streamWarning,
    error,
    setError,
    startResearch,
  };
}
