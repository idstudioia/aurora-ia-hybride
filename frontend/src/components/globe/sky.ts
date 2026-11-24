// WMO weather codes (as returned by Open-Meteo) → French label and pictogram family.
// Only used to present a code the backend actually returned; never to fill a missing value.

export type SkyKind = 'clear' | 'partly' | 'cloudy' | 'fog' | 'drizzle' | 'rain' | 'snow' | 'storm' | 'unknown';

const WMO_LABELS: Record<number, string> = {
  0: 'Ciel dégagé', 1: 'Plutôt dégagé', 2: 'Partiellement nuageux', 3: 'Couvert',
  45: 'Brouillard', 48: 'Brouillard givrant',
  51: 'Bruine légère', 53: 'Bruine modérée', 55: 'Bruine dense',
  56: 'Bruine verglaçante légère', 57: 'Bruine verglaçante dense',
  61: 'Pluie faible', 63: 'Pluie modérée', 65: 'Pluie forte',
  66: 'Pluie verglaçante faible', 67: 'Pluie verglaçante forte',
  71: 'Neige faible', 73: 'Neige modérée', 75: 'Neige forte', 77: 'Grains de neige',
  80: 'Averses faibles', 81: 'Averses modérées', 82: 'Averses violentes',
  85: 'Averses de neige faibles', 86: 'Averses de neige fortes',
  95: 'Orage', 96: 'Orage avec grêle faible', 99: 'Orage avec grêle forte',
};

export const wmoLabel = (code: number | null | undefined): string =>
  code === null || code === undefined ? '' : WMO_LABELS[code] ?? '';

export function skyKindFromCode(code: number | null | undefined): SkyKind {
  if (code === null || code === undefined || Number.isNaN(code)) return 'unknown';
  if (code <= 1) return 'clear';
  if (code === 2) return 'partly';
  if (code === 3) return 'cloudy';
  if (code === 45 || code === 48) return 'fog';
  if (code >= 51 && code <= 57) return 'drizzle';
  if ((code >= 61 && code <= 67) || (code >= 80 && code <= 82)) return 'rain';
  if ((code >= 71 && code <= 77) || code === 85 || code === 86) return 'snow';
  if (code >= 95 && code <= 99) return 'storm';
  return 'unknown';
}

/** Fallback when the backend sent a label but no code. */
export function skyKindFromLabel(label: string): SkyKind {
  const l = label.toLowerCase();
  if (/orage|thunder|storm/.test(l)) return 'storm';
  if (/neige|snow/.test(l)) return 'snow';
  if (/bruine|drizzle/.test(l)) return 'drizzle';
  if (/pluie|averse|rain|shower/.test(l)) return 'rain';
  if (/brouillard|brume|fog|mist/.test(l)) return 'fog';
  if (/partiel|éclaircie|eclaircie|partly/.test(l)) return 'partly';
  if (/couvert|nuag|cloud|overcast/.test(l)) return 'cloudy';
  if (/dégagé|degage|ensoleill|clair|clear|sunny/.test(l)) return 'clear';
  return 'unknown';
}

/** Display colour for a temperature in °C (presentation scale only). */
export function temperatureColor(t: number | null): string {
  if (t === null) return '#7f9cc0';
  if (t <= -10) return '#9db8ff';
  if (t <= 0) return '#6fb6ff';
  if (t <= 10) return '#22c8ff';
  if (t <= 18) return '#2ee6a8';
  if (t <= 25) return '#d6e85a';
  if (t <= 32) return '#ffb547';
  return '#ff6b7a';
}

/** Display colour for an earthquake magnitude (presentation scale only). */
export function magnitudeColor(m: number | null): string {
  if (m === null) return '#7f9cc0';
  if (m < 3) return '#2ee6a8';
  if (m < 4.5) return '#ffd166';
  if (m < 6) return '#ff9f5a';
  return '#ff5470';
}
