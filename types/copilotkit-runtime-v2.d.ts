// @copilotkit/runtime 1.70.3 ships v2 JavaScript subpaths without a `types`
// export condition. Point TypeScript at the declarations included in that
// exact package so the starter remains strict without ambient `any` imports.
declare module "@copilotkit/runtime/v2" {
  export const BuiltInAgent: typeof import("../node_modules/@copilotkit/runtime/dist/v2/index.d.cts").BuiltInAgent;
  export const CopilotKitIntelligence: typeof import("../node_modules/@copilotkit/runtime/dist/v2/index.d.cts").CopilotKitIntelligence;
  export const CopilotRuntime: typeof import("../node_modules/@copilotkit/runtime/dist/v2/index.d.cts").CopilotRuntime;
  export const createCopilotHonoHandler: typeof import("../node_modules/@copilotkit/runtime/dist/v2/index.d.cts").createCopilotHonoHandler;
  export type BuiltInAgent = InstanceType<typeof import("../node_modules/@copilotkit/runtime/dist/v2/index.d.cts").BuiltInAgent>;
  export type BuiltInAgentClassicConfig = import("../node_modules/@copilotkit/runtime/dist/v2/index.d.cts").BuiltInAgentClassicConfig;
  export type MCPClientConfig = import("../node_modules/@copilotkit/runtime/dist/v2/index.d.cts").MCPClientConfig;
}

declare module "@copilotkit/runtime/v2/node" {
  export const createCopilotNodeListener: typeof import("../node_modules/@copilotkit/runtime/dist/v2/node.d.cts").createCopilotNodeListener;
}
