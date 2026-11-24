// Small canvas drawing helpers: colours, rounded rectangles and line-art weather pictograms.
import type { SkyKind } from './sky';

/** '#rgb' / '#rrggbb' + alpha → 'rgba(...)'. Other CSS colours are returned unchanged. */
export function withAlpha(color: string, alpha: number): string {
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(color.trim());
  if (!m) return color;
  const hex = m[1].length === 3 ? m[1].split('').map(c => c + c).join('') : m[1];
  const n = parseInt(hex, 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha.toFixed(3)})`;
}

export function roundRectPath(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  const rr = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.arcTo(x + w, y, x + w, y + h, rr);
  ctx.arcTo(x + w, y + h, x, y + h, rr);
  ctx.arcTo(x, y + h, x, y, rr);
  ctx.arcTo(x, y, x + w, y, rr);
  ctx.closePath();
}

const SUN = '#ffd166';
const CLOUD = '#cfe8ff';
const WATER = '#5ec8ff';
const BOLT = '#ffb547';

function sun(ctx: CanvasRenderingContext2D, x: number, y: number, s: number) {
  ctx.strokeStyle = SUN;
  ctx.fillStyle = SUN;
  ctx.beginPath(); ctx.arc(x, y, s * 0.26, 0, Math.PI * 2); ctx.fill();
  ctx.beginPath();
  for (let i = 0; i < 8; i++) {
    const a = (i / 8) * Math.PI * 2;
    ctx.moveTo(x + Math.cos(a) * s * 0.38, y + Math.sin(a) * s * 0.38);
    ctx.lineTo(x + Math.cos(a) * s * 0.5, y + Math.sin(a) * s * 0.5);
  }
  ctx.stroke();
}

function moon(ctx: CanvasRenderingContext2D, x: number, y: number, s: number) {
  ctx.save();
  ctx.beginPath();
  ctx.rect(x - s, y - s, s * 2, s * 2);
  ctx.arc(x + s * 0.2, y - s * 0.14, s * 0.3, 0, Math.PI * 2);
  ctx.clip('evenodd');
  ctx.fillStyle = '#e8eeff';
  ctx.beginPath(); ctx.arc(x, y, s * 0.36, 0, Math.PI * 2); ctx.fill();
  ctx.restore();
}

function cloud(ctx: CanvasRenderingContext2D, x: number, y: number, s: number, fill = 'rgba(207,232,255,.22)') {
  ctx.beginPath();
  ctx.moveTo(x - s * 0.42, y + s * 0.2);
  ctx.arc(x - s * 0.24, y + s * 0.02, s * 0.2, Math.PI * 0.75, Math.PI * 1.5);
  ctx.arc(x + s * 0.02, y - s * 0.08, s * 0.25, Math.PI * 1.1, Math.PI * 1.95);
  ctx.arc(x + s * 0.28, y + s * 0.06, s * 0.17, Math.PI * 1.45, Math.PI * 0.5);
  ctx.closePath();
  ctx.fillStyle = fill;
  ctx.fill();
  ctx.strokeStyle = CLOUD;
  ctx.stroke();
}

/** Line-art pictogram centred on (x, y) inside an s×s box. */
export function drawSkyGlyph(ctx: CanvasRenderingContext2D, kind: SkyKind, x: number, y: number, s: number, night = false) {
  ctx.save();
  ctx.lineWidth = Math.max(1, s / 11);
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  switch (kind) {
    case 'clear':
      if (night) moon(ctx, x, y, s); else sun(ctx, x, y, s);
      break;
    case 'partly':
      if (night) moon(ctx, x - s * 0.16, y - s * 0.16, s * 0.8); else sun(ctx, x - s * 0.16, y - s * 0.16, s * 0.75);
      cloud(ctx, x + s * 0.06, y + s * 0.08, s * 0.85, 'rgba(10,30,60,.9)');
      break;
    case 'cloudy':
      cloud(ctx, x, y, s);
      break;
    case 'fog':
      ctx.strokeStyle = CLOUD;
      ctx.beginPath();
      for (let i = -1; i <= 1; i++) {
        const off = i === 0 ? s * 0.08 : 0;
        ctx.moveTo(x - s * 0.4 + off, y + i * s * 0.22);
        ctx.lineTo(x + s * 0.4 - (i === 1 ? s * 0.12 : 0), y + i * s * 0.22);
      }
      ctx.stroke();
      break;
    case 'drizzle':
    case 'rain':
    case 'snow':
    case 'storm': {
      cloud(ctx, x, y - s * 0.12, s * 0.9);
      if (kind === 'storm') {
        ctx.strokeStyle = BOLT;
        ctx.beginPath();
        ctx.moveTo(x + s * 0.04, y + s * 0.1);
        ctx.lineTo(x - s * 0.08, y + s * 0.3);
        ctx.lineTo(x + s * 0.06, y + s * 0.3);
        ctx.lineTo(x - s * 0.06, y + s * 0.5);
        ctx.stroke();
      } else if (kind === 'snow') {
        ctx.fillStyle = '#ffffff';
        for (const dx of [-0.2, 0, 0.2]) {
          ctx.beginPath(); ctx.arc(x + dx * s, y + s * (dx === 0 ? 0.42 : 0.3), s * 0.055, 0, Math.PI * 2); ctx.fill();
        }
      } else {
        ctx.strokeStyle = WATER;
        ctx.beginPath();
        const drops = kind === 'rain' ? [-0.2, 0, 0.2] : [-0.12, 0.12];
        const len = kind === 'rain' ? 0.2 : 0.09;
        for (const dx of drops) {
          ctx.moveTo(x + dx * s, y + s * 0.22);
          ctx.lineTo(x + (dx - 0.06) * s, y + s * (0.22 + len));
        }
        ctx.stroke();
      }
      break;
    }
    default:
      ctx.strokeStyle = CLOUD;
      ctx.beginPath(); ctx.arc(x, y, s * 0.3, 0, Math.PI * 2); ctx.stroke();
  }
  ctx.restore();
}
