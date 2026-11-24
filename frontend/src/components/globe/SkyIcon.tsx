// Weather pictogram (lucide) for a sky family; decorative, the label carries the meaning.
import { Cloud, CloudDrizzle, CloudFog, CloudLightning, CloudMoon, CloudRain, CloudSnow, CloudSun, Moon, Sun, Thermometer } from 'lucide-react';
import type { SkyKind } from './sky';

const TONES: Record<SkyKind, string> = {
  clear: '#ffd166', partly: '#ffe08a', cloudy: '#cfe8ff', fog: '#b9c9dd', drizzle: '#7fd3ff',
  rain: '#5ec8ff', snow: '#ffffff', storm: '#ffb547', unknown: '#7f9cc0',
};

export default function SkyIcon({ kind, night = false, size = 20 }: { kind: SkyKind; night?: boolean; size?: number }) {
  const props = { size, color: night && (kind === 'clear' || kind === 'partly') ? '#dfe7ff' : TONES[kind], 'aria-hidden': true as const, strokeWidth: 1.8 };
  switch (kind) {
    case 'clear': return night ? <Moon {...props} /> : <Sun {...props} />;
    case 'partly': return night ? <CloudMoon {...props} /> : <CloudSun {...props} />;
    case 'cloudy': return <Cloud {...props} />;
    case 'fog': return <CloudFog {...props} />;
    case 'drizzle': return <CloudDrizzle {...props} />;
    case 'rain': return <CloudRain {...props} />;
    case 'snow': return <CloudSnow {...props} />;
    case 'storm': return <CloudLightning {...props} />;
    default: return <Thermometer {...props} />;
  }
}
