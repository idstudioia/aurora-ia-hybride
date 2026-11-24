// Google's return after « Se connecter à Google » (Paramètres › Studio, tools/youtube_comments.py): Google sends the
// browser to /youtube-retour?state=…&code=… (or &error=…). main.tsx calls captureYoutubeReturn() before the first
// render: the code leaves the address of this tab at once (history.replaceState) and waits in memory until the
// Studio settings give it to the service with the session token (POST /api/youtube/retour). The browser's own
// history still lists the address: the code there is useless to anyone (single use, bound to PKCE and to the
// client secret kept by the service, and the state is used up).
export interface YoutubeReturn { state: string; code: string; error: string }

export const YOUTUBE_RETURN_PATH = '/youtube-retour';
let pending: YoutubeReturn | null = null;

export function captureYoutubeReturn(): void {
  if (window.location.pathname !== YOUTUBE_RETURN_PATH) return;
  const query = new URLSearchParams(window.location.search);
  pending = { state: query.get('state') ?? '', code: query.get('code') ?? '', error: query.get('error') ?? '' };
  window.history.replaceState(null, '', '/#/settings');
}

/** The return captured at start-up, given once (then forgotten). */
export function takeYoutubeReturn(): YoutubeReturn | null {
  const found = pending;
  pending = null;
  return found;
}
