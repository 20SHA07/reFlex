export const DECISIONS = ["BLOCK", "DEFER", "REVIEW", "ALLOW"] as const;
export type Decision = (typeof DECISIONS)[number];

export const TASK_STATES = [
  "running",
  "awaiting_review",
  "stale",
  "publishing",
  "completed",
  "cancelled",
  "failed",
] as const;
export type TaskState = (typeof TASK_STATES)[number];
export type TaskKind = "report" | "cleanup";

export const ACTION_STATES = [
  "blocked",
  "deferred",
  "pending_review",
  "executing",
  "succeeded",
  "cancelled",
  "stale",
  "failed",
  "uncertain",
] as const;
export type ActionState = (typeof ACTION_STATES)[number];

export type TrustedActor = {
  canonicalUserId: string;
  displayName: string;
  workspaceKey: string;
  channelKey: string;
  conversationKey: string;
  eventId: string;
  scopeVerified: boolean;
};

export type ProjectConfig = {
  projectId: string;
  workspaceRoot: string;
  privateRoot: string;
  dbPath: string;
  draftsRoot: string;
  quarantineRoot: string;
  reportsRoot: string;
  dataRoot: string;
  policyVersion: string;
  allowedWorkspaceKey: string;
  allowedChannelKey: string;
  allowedConversationKey: string;
  memberIds: Set<string>;
  demoOperatorIds: Set<string>;
  demoMode: boolean;
  approvalTtlSeconds: number;
  modelTimeoutMs: number;
  maxToolSteps: number;
  processSession: string;
};

export type TaskRecord = {
  id: string;
  projectId: string;
  kind: TaskKind;
  ownerId: string;
  ownerLabel: string;
  state: TaskState;
  revision: number;
  eventId: string;
  conversationKey: string;
  inputFileId?: string;
  inputPath?: string;
  inputHash?: string;
  draftHash?: string;
  narrative?: string;
  metrics?: ReportMetrics;
  error?: string;
  createdAt: string;
  updatedAt: string;
};

export type ReportMetrics = {
  fromPeriod: string;
  toPeriod: string;
  revenueAed: { from: number; to: number; change: number; percent: number | null };
  closedDeals: { from: number; to: number; change: number; percent: number | null };
  openTickets: { from: number; to: number; change: number; percent: number | null };
};

export type DependencyRecord = {
  id: string;
  projectId: string;
  taskId: string;
  fileId: string;
  relativePath: string;
  observedHash: string;
  active: boolean;
  reason: string;
};

export type CleanupAction = {
  id: string;
  taskId: string;
  projectId: string;
  fileId: string;
  relativePath: string;
  reason: string;
  decision: Decision;
  state: ActionState;
  revision: number;
  expectedHash?: string;
  blockingTaskIds: string[];
  requiredApproverId?: string;
  updatedAt: string;
};

export type RefereeVerdict = {
  actionId: string;
  revision: number;
  decision: Decision;
  reasonCode: string;
  reason: string;
  relativePath: string;
  currentHash?: string;
  blockingTaskIds: string[];
  requiredApproverId?: string;
};

export type ApprovalRecord = {
  id: string;
  kind: "cleanup" | "report_publish";
  taskId: string;
  actionIds: string[];
  revision: number;
  expectedHash?: string;
  approverId: string;
  workspaceKey: string;
  channelKey: string;
  conversationKey: string;
  policyVersion: string;
  processSession: string;
  expiresAt: string;
  consumedAt?: string;
  cancelledAt?: string;
  decision?: "approve" | "decline";
};

export type OperationRecord = {
  id: string;
  kind: "quarantine" | "publication";
  taskId: string;
  actionId?: string;
  sourcePath: string;
  destinationPath: string;
  expectedHash: string;
  state: "prepared" | "executing" | "verified" | "failed" | "uncertain";
  receiptId?: string;
};

export type FileInventoryItem = {
  fileId: string;
  relativePath: string;
  size: number;
  modifiedAt: string;
  hash: string;
  kind: "csv" | "log" | "markdown";
};

export type CleanupOutcome = {
  task: TaskRecord;
  verdicts: RefereeVerdict[];
  approvals: ApprovalRecord[];
  injected: boolean;
};

export type ExecutionOutcome =
  | { status: "succeeded"; action: CleanupAction; operation: OperationRecord }
  | { status: "already_settled"; action?: CleanupAction; operation?: OperationRecord }
  | { status: "rejected" | "stale" | "deferred" | "failed" | "uncertain"; message: string; action?: CleanupAction };

export type PublicationOutcome =
  | { status: "succeeded"; task: TaskRecord; operation: OperationRecord; rechecked: CleanupOutcome[] }
  | { status: "rejected" | "stale" | "failed" | "uncertain"; message: string; task?: TaskRecord };

export type StatusSnapshot = {
  projectId: string;
  runRoot: string;
  files: Array<{ relativePath: string; exists: boolean; hash?: string; size?: number }>;
  tasks: TaskRecord[];
  actions: CleanupAction[];
  activeDependencies: DependencyRecord[];
  unfinishedOperations: OperationRecord[];
  consistent: boolean;
  problems: string[];
};
