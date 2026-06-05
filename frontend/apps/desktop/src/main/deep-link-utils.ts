export const DEEP_LINK_PROTOCOL = "valuz-oss";

export interface ParsedDeepLink {
  raw: string;
  host: string;
  pathname: string;
  search: string;
}

export const parseDeepLink = (value: string): ParsedDeepLink | null => {
  try {
    const parsed = new URL(value);
    if (parsed.protocol !== `${DEEP_LINK_PROTOCOL}:`) {
      return null;
    }

    return {
      raw: value,
      host: parsed.host,
      pathname: parsed.pathname,
      search: parsed.search,
    };
  } catch {
    return null;
  }
};
