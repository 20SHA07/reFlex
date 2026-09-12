import type {
  ApplicationUser,
  ChannelIdentityContext,
  ChannelToolContext,
  IncomingMessage,
  InteractionContext,
  ProviderActor,
} from "@copilotkit/channels";
import type { ProjectConfig, TrustedActor } from "./types";

/**
 * The managed adapter is the source of truth for the human identity. The
 * tenant is namespaced into the id so the same Slack id in another workspace
 * cannot accidentally match a configured member.
 */
export function identifyPlatformUser(context: ChannelIdentityContext): ApplicationUser | null {
  if (context.actor.kind !== "human" || !context.actor.id) return null;
  return {
    id: `${context.provider}:${context.tenant.id}:${context.actor.id}`,
    name: context.actor.name || context.actor.handle || context.actor.id,
  };
}

type TrustedContext = {
  actor: ProviderActor;
  user: ApplicationUser | null;
  platform: string;
  message?: IncomingMessage;
  scope?: Partial<Pick<TrustedActor, "workspaceKey" | "channelKey" | "conversationKey">>;
  eventId?: string;
};

function record(value: unknown): Record<string, unknown> | undefined {
  return typeof value === "object" && value !== null ? value as Record<string, unknown> : undefined;
}

function stringField(source: Record<string, unknown> | undefined, key: string): string | undefined {
  const value = source?.[key];
  return typeof value === "string" && value ? value : undefined;
}

function messageScope(message: IncomingMessage | undefined) {
  const source = record(message);
  const scope = record(source?.scope) ?? source;
  return {
    workspaceKey: stringField(scope, "workspaceKey") ?? stringField(scope, "workspace_key"),
    channelKey: stringField(scope, "channelKey") ?? stringField(scope, "channel_key"),
    conversationKey: stringField(scope, "conversationKey") ?? stringField(scope, "conversation_key"),
  };
}

export function trustedActor(context: TrustedContext): TrustedActor {
  const derived = messageScope(context.message);
  const workspaceKey = context.scope?.workspaceKey ?? derived.workspaceKey;
  const channelKey = context.scope?.channelKey ?? derived.channelKey;
  const conversationKey = context.scope?.conversationKey ?? derived.conversationKey;
  const canonicalUserId = context.user?.id || `${context.platform}:${context.actor.id}`;
  return {
    canonicalUserId,
    displayName: context.user?.name || context.actor.name || context.actor.handle || context.actor.id,
    workspaceKey: workspaceKey ?? "",
    channelKey: channelKey ?? "",
    conversationKey: conversationKey ?? "",
    eventId: context.eventId || context.message?.eventId || context.message?.ref.id || "",
    scopeVerified: Boolean(workspaceKey && channelKey && conversationKey),
  };
}

export function trustedFromToolContext(context: ChannelToolContext, scope?: TrustedContext["scope"]): TrustedActor {
  return trustedActor({ ...context, scope });
}

export function trustedFromInteractionContext(context: InteractionContext, scope?: TrustedContext["scope"]): TrustedActor {
  return trustedActor({ ...context, scope, message: context.message });
}

export function testActor(id: string, displayName: string, eventId = `test-${id}`): TrustedActor {
  return {
    canonicalUserId: id,
    displayName,
    workspaceKey: "workspace-test",
    channelKey: "channel-test",
    conversationKey: "conversation-test",
    eventId,
    scopeVerified: true,
  };
}

export function assertMember(actor: TrustedActor, config: ProjectConfig): void {
  if (!actor.scopeVerified || actor.workspaceKey !== config.allowedWorkspaceKey || actor.channelKey !== config.allowedChannelKey || actor.conversationKey !== config.allowedConversationKey) {
    throw new Error("This request is outside Truce's configured workspace, channel, or project thread.");
  }
  if (!config.memberIds.has(actor.canonicalUserId)) throw new Error("You are not a configured member of this Truce project.");
}

export function assertDemoOperator(actor: TrustedActor, config: ProjectConfig): void {
  assertMember(actor, config);
  if (!config.demoMode || !config.demoOperatorIds.has(actor.canonicalUserId)) throw new Error("The labelled demo is disabled for this user.");
}

export function assertOwner(actor: TrustedActor, ownerId: string): void {
  if (actor.canonicalUserId !== ownerId) throw new Error("Only the task owner can approve or change this task.");
}
