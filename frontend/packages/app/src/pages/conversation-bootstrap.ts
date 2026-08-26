export interface ConversationBootstrapRequest {
  isCurrent: () => boolean;
  cancel: () => void;
}

/**
 * Latest-request guard for route-driven conversation bootstraps. Starting a
 * request invalidates every older request; cleaning up an obsolete request
 * cannot accidentally cancel the current one.
 */
export const createConversationBootstrapGuard = () => {
  let currentGeneration = 0;

  return {
    start(): ConversationBootstrapRequest {
      const generation = ++currentGeneration;
      return {
        isCurrent: () => currentGeneration === generation,
        cancel: () => {
          if (currentGeneration === generation) currentGeneration++;
        },
      };
    },
  };
};
