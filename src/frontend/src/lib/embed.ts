/**
 * Embed-mode support.
 *
 * Kratos can be hosted "embedded" under another origin (e.g. agentic-loop-site
 * behind Front Door at `/kratos/*`). When the host opens the app with
 * `?embed=1`, Kratos renders chromeless (no marketing sidebar) and reacts to a
 * small set of URL args so the host can drive it:
 *
 *   ?embed=1            — chromeless layout
 *   &theme=dark         — sync light/dark mode (or a named Kratos theme)
 *   &mode=dark          — sync light/dark mode independently of a named theme
 *                         (lets the host pass &theme=agentic-loop&mode=dark)
 *   &prompt=<text>      — auto-start a conversation with this first message
 *   &back=<path>        — where the in-sidebar "Back to Agentic Loop" button
 *                         returns to (same-origin path; default /reference/kratos)
 *
 */

export interface EmbedParams {
  embed: boolean;
  theme: string | null;
  mode: string | null;
  prompt: string | null;
  back: string | null;
}

function truthy(value: string | null): boolean {
  return value === "1" || value === "true" || value === "yes";
}

/**
 * `back` ends up in an `<a href>`, so anything other than a same-origin path
 * (`javascript:`, `//evil.example`, `/\evil.example`, absolute URLs) is an
 * XSS / open-redirect vector. Returns the normalised path, or null.
 */
export function safeBackPath(value: string | null, origin: string): string | null {
  if (!value || !value.startsWith("/") || value.startsWith("//") || value.startsWith("/\\")) {
    return null;
  }
  try {
    const url = new URL(value, origin);
    if (url.origin !== origin) return null;
    return url.pathname + url.search + url.hash;
  } catch {
    return null;
  }
}

/** Parse embed-relevant args from the current URL. SSR-safe (returns defaults). */
export function readEmbedParams(): EmbedParams {
  if (typeof window === "undefined") {
    return { embed: false, theme: null, mode: null, prompt: null, back: null };
  }
  const p = new URLSearchParams(window.location.search);
  return {
    embed: truthy(p.get("embed")),
    theme: p.get("theme"),
    mode: p.get("mode"),
    prompt: p.get("prompt"),
    back: safeBackPath(p.get("back"), window.location.origin),
  };
}
