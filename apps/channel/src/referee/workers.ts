import type { CleanupOutcome, ProjectConfig, RefereeVerdict, ReportMetrics, TaskRecord, TrustedActor } from "./types";

export type ReportJob = { task: TaskRecord; actor: TrustedActor };
export type CleanupJob = { task: TaskRecord; actor: TrustedActor };
export type ReportOutcome = { task: TaskRecord; metrics: ReportMetrics; narrative: string };

/** Application boundary for model workers. Filesystem writes remain private. */
export interface WorkerRunner {
  runReport(job: ReportJob, context: TrustedRunContext): Promise<ReportOutcome>;
  runCleanup(job: CleanupJob, context: TrustedRunContext): Promise<CleanupOutcome>;
  explainDecision(facts: DecisionFacts): Promise<string>;
}

export type TrustedRunContext = {
  projectId: string;
  policyVersion: string;
  maxToolSteps: number;
  signal?: AbortSignal;
};

export type DecisionFacts = {
  projectId: string;
  verdicts: RefereeVerdict[];
  ownerLabels: Record<string, string>;
};

export function workerContext(config: ProjectConfig, signal?: AbortSignal): TrustedRunContext {
  return { projectId: config.projectId, policyVersion: config.policyVersion, maxToolSteps: config.maxToolSteps, signal };
}
