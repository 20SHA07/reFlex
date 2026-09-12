import { loadProjectConfig } from "./config";
import { TruceCoordinator } from "./coordinator";

let coordinatorPromise: Promise<TruceCoordinator> | undefined;

export function getCoordinator(): Promise<TruceCoordinator> {
  coordinatorPromise ??= loadProjectConfig().then((config) => TruceCoordinator.open(config));
  return coordinatorPromise;
}

export async function initializeCoordinator(): Promise<TruceCoordinator> {
  const coordinator = await getCoordinator();
  await coordinator.reconcileOperations();
  return coordinator;
}

export function resetCoordinatorForTests(): void {
  coordinatorPromise = undefined;
}
