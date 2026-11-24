// Intended use: tunable behavior. Playback pacing, how many requests one dot stands
// for, the latency-to-travel-time mapping, layout proportions, and keyboard steps.

export interface SlowWindow {
  from: number;
  to: number;
  minutesPerSecond: number;
}

export const config = {
  playback: {
    startMinute: 830,
    minutesPerSecond: 12,
    slowWindows: [{ from: 852, to: 935, minutesPerSecond: 1.6 }] as readonly SlowWindow[],
    rampMinutes: 6,
    reducedStepMinutes: 15,
    reducedStepSeconds: 1.2,
  },
  keyboard: { stepMinutes: 5, bigStepMinutes: 60 },
  particles: {
    requestsPerDot: 36,
    maxDots: 2600,
    travelBaseSeconds: 0.75,
    travelSecondsPerDecade: 1.05,
    retryTravelSeconds: 4,
    queueSpacing: 5,
    warmSeconds: 9,
    warmStepSeconds: 0.1,
    followReach: 14,
  },
  layout: {
    columns: 6,
    columnGaps: [0.85, 0.85, 1, 1, 1.5],
    nodeThickness: 8,
    nodeGap: 14,
    laneGap: 18,
    labelSpan: 28,
    labelGap: 8,
    pileReferenceSeconds: { requests: 2.5, messages: 3600 },
    pileShare: 0.55,
    capacityShownAbove: 0.6,
    calloutClearance: 12,
    calloutGrid: { columns: 9, rows: 7 },
    calloutDistanceWeight: 8,
    portraitAspect: 1.15,
    portraitBelow: 600,
    shortLabelsBelow: 260,
  },
  trace: { minScaleMs: 50, slowWaitMs: 500 },
  timeline: { labelGap: 12, axisHours: [0, 3, 6, 9, 12, 15, 18, 21, 24], majorEveryHours: 6 },
  tour: { skipAheadMinutes: 90, leadMinutes: 6, storageKey: 'request-flow-replay:walkthrough' },
  model: {
    hourlyNoise: 0.035,
    jobRampMinutes: 3,
  },
} as const;
