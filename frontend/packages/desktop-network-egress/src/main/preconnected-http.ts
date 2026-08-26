import { Agent as HttpAgent } from "node:http";
import type { Socket } from "node:net";

/**
 * Bind one HTTP request to a socket that has already traversed the selected
 * egress route. Using `agent: false` would let node:http create a second,
 * direct connection and silently bypass the resolver/connector decision.
 */
export const createPreconnectedHttpAgent = (socket: Socket): HttpAgent => {
  const agent = new HttpAgent({ keepAlive: false, maxSockets: 1 });
  agent.createConnection = () => socket;
  return agent;
};
