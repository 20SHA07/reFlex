import { z } from "zod";
import { ACTION_STATES, DECISIONS, TASK_STATES } from "./types";

export const cleanupProposalSchema = z.object({
  taskId: z.string().min(1).max(120),
  candidates: z
    .array(
      z.object({
        fileId: z.string().min(1).max(240),
        reason: z.string().min(1).max(500),
      }),
    )
    .min(1)
    .max(50),
});

export const narrativeSchema = z.string().trim().min(1).max(1200);
export const reportTaskIdSchema = z.object({ taskId: z.string().min(1).max(120) });
export const approvalIdSchema = z.object({ approvalId: z.string().min(1).max(120) });

export const taskStateSchema = z.enum(TASK_STATES);
export const actionStateSchema = z.enum(ACTION_STATES);
export const decisionSchema = z.enum(DECISIONS);

export const runPointerSchema = z.object({
  runId: z.string().regex(/^run-[a-z0-9-]+$/),
});

export const seedMarkerSchema = z.object({
  marker: z.literal("truce-disposable-run"),
  runId: z.string().min(1),
  createdAt: z.string().datetime(),
});
