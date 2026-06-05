// Personal-edition default ports. Public skeleton intentionally only knows
// about ports it actually starts; any future enterprise overlay declares its
// own ports in the overlay package, not here.
export const PERSONAL_PORTS = {
  AGENT_SERVER: 19100,
} as const;

export type PersonalPortName = keyof typeof PERSONAL_PORTS;
