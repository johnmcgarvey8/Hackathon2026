"use client";

import { useEffect } from "react";
import { shouldPollRun } from "@/lib/measurement-runtime";
import type { MeasurementRun } from "@/lib/types";

const POLL_INTERVAL_MS = 2500;
const MAX_POLLS = 120;

export function useRunPolling(
  run: MeasurementRun | null,
  refresh: () => Promise<void>,
) {
  const runId = run?.run_id;
  const pollingRequired = shouldPollRun(run);

  useEffect(() => {
    if (!pollingRequired) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let attempts = 0;

    const schedule = () => {
      if (stopped || attempts >= MAX_POLLS) return;
      timer = setTimeout(tick, POLL_INTERVAL_MS);
    };
    const tick = async () => {
      if (stopped) return;
      if (document.visibilityState === "hidden") {
        schedule();
        return;
      }
      attempts += 1;
      await refresh().catch(() => undefined);
      schedule();
    };
    const onVisibility = () => {
      if (document.visibilityState === "visible" && !timer) schedule();
    };

    document.addEventListener("visibilitychange", onVisibility);
    schedule();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [pollingRequired, refresh, runId]);
}
