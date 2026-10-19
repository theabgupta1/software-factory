// factory-live: the shapes it reads from the factory engine and the session state it draws from.

/** One job as `.factory/index.json` summarises it (written by factoryctl on every change). */
export type FactoryLiveJob = {
  id: string;
  title: string;
  workflow: string | null;
  status: string;
  stage: string | null;
  round: number;
  attention: { kind: string; reason: string; commands: string[] } | null;
  running_detail: { step: string; agent: string | null; kind: string | null; since: string | null }[];
  reviews: Record<string, string | null>;
  pending_messages: number;
  updated_at: string | null;
  legacy: boolean;
};

/** What the board knows right now. `repo` is null outside a repository that uses the factory. */
export type FactoryLiveSnapshot = {
  repo: string | null;
  jobs: FactoryLiveJob[];
  /** Epoch ms of the last successful read. */
  readAt: number;
  error: string | null;
};

/** The running agent the person chose to message from the pane. */
export type FactoryLiveTarget = { job: string; step: string; agent: string; title: string };

/** Display facts for one agent of the factory roster. */
export type FactoryLiveAgentMeta = { title: string; hue: number };

declare module 'claude-code' {
  interface PluginState {
    'factory-live': {
      snapshot: FactoryLiveSnapshot;
      /** `<job>/<step>` → the subagent id the factory hooks recorded at spawn. */
      agentIds: Record<string, string>;
      /** Ids of this session's agents that are running now (`$.agent.list()`). */
      live: string[];
      roster: Record<string, FactoryLiveAgentMeta>;
      target: FactoryLiveTarget | null;
      /** The attention signature the person hid the band for; it shows again when that changes. */
      bandHidden: string | null;
      consoleUrl: string | null;
    };
  }
}
