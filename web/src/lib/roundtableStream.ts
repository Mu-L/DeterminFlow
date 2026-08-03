interface RoundtableEventIdentity {
  type: string;
  roundtable_id?: string;
  roundtable_revision?: number;
}

export function getRoundtableEventRevision(
  event: RoundtableEventIdentity,
): number | null {
  return Number.isSafeInteger(event.roundtable_revision) &&
    (event.roundtable_revision || 0) >= 0
    ? event.roundtable_revision!
    : null;
}

export function eventsAfterRoundtableSnapshot<T extends RoundtableEventIdentity>(
  snapshotRevision: number,
  events: readonly T[],
): T[] {
  return events.filter((event) => {
    const revision = getRoundtableEventRevision(event);
    return revision !== null && revision > snapshotRevision;
  });
}

export interface RoundtableReplay<T> {
  events: T[];
  pending: T[];
  hasGap: boolean;
}

/** Select a strictly contiguous replay after a REST watermark. */
export function contiguousRoundtableReplay<T extends RoundtableEventIdentity>(
  snapshotRevision: number,
  events: readonly T[],
): RoundtableReplay<T> {
  const pending = eventsAfterRoundtableSnapshot(snapshotRevision, events)
    .slice()
    .sort(
      (left, right) =>
        (getRoundtableEventRevision(left) || 0) -
        (getRoundtableEventRevision(right) || 0),
    );
  let expectedRevision = snapshotRevision + 1;
  const contiguous: T[] = [];
  for (const event of pending) {
    const revision = getRoundtableEventRevision(event);
    if (revision === null || revision < expectedRevision) continue;
    if (revision > expectedRevision) {
      return { events: [], pending, hasGap: true };
    }
    contiguous.push(event);
    expectedRevision += 1;
  }
  return { events: contiguous, pending: [], hasGap: false };
}

export function shouldBufferRoundtableEvent(
  loadingRoundtableId: string | null,
  event: RoundtableEventIdentity,
): boolean {
  return Boolean(
    loadingRoundtableId && event.roundtable_id === loadingRoundtableId,
  );
}

export function shouldHandleRoundtableEvent(
  activeRoundtableId: string | null,
  event: RoundtableEventIdentity,
): boolean {
  if (!event.roundtable_id) return true;
  return activeRoundtableId === event.roundtable_id;
}
