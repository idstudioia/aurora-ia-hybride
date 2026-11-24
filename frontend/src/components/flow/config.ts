// Adapté de Request Flow Replay — Copyright (c) 2026 Cliff Pyles — licence MIT, voir third_party/request-flow-replay/LICENSE.txt
// Tunable behaviour of the task-flow replay: playback pacing, the duration-to-travel-time
// mapping, layout proportions and keyboard steps. No data lives here.

export const flowConfig = {
  playback: {
    /** A whole window plays in about this many seconds at ×1 when nothing happens. */
    windowSeconds: 48,
    /** Near real activity the cursor slows to this many real seconds per second (honest axis, slower pace). */
    activityRate: 6,
    /** Slow window padding around each run, in real milliseconds. */
    activityPadMs: 2500,
    /** Ramp into / out of a slow window, in seconds of playback at base speed. */
    rampSeconds: 0.4,
    speeds: [1, 4, 16] as const,
    /** prefers-reduced-motion: the cursor jumps one real event every this many seconds. */
    reducedStepSeconds: 1.2,
    /** GSAP scrub tween for jumps. */
    scrubSeconds: 0.45,
    scrubEase: 'power2.inOut',
    /** Jumps land this long before a run starts so its dot is seen leaving Entrée. */
    leadMs: 400,
  },
  particles: {
    // Same log mapping as the original travelSeconds(): durations span < 1 ms to minutes.
    travelBaseSeconds: 0.75,
    travelSecondsPerDecade: 1.05,
    lingerSeconds: 1.8,
    pileWidth: 14,
    followReach: 14,
    maxDots: 400,
    radius: 4,
  },
  layout: {
    columns: 6,
    columnGaps: [1, 1, 1.05, 1, 1],
    nodeThickness: 10,
    nodeGap: 20,
    minNode: 6,
    laneGap: 12,
    loopGap: 4,
    labelSpan: 30,
    labelGap: 7,
    labelRoom: 132,
    padLeft: 34,
    padTop: 16,
    /** One passage never draws thicker than this, so a single run stays a ribbon, not a slab. */
    maxPassagePx: 16,
    minWidth: 640,
  },
  trace: { minScaleMs: 50 },
  live: { refreshDebounceMs: 700, slideEveryMs: 30_000 },
  data: { runsLimit: 200, detailConcurrency: 5 },
} as const;

export type WindowId = 'hour' | 'today' | 'all';
export type FlowMode = 'live' | 'replay';

export const WINDOW_LABELS: Record<WindowId, string> = {
  hour: 'Dernière heure',
  today: "Aujourd'hui",
  all: 'Tout',
};
