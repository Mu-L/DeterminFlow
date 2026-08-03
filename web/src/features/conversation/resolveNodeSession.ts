/**
 * Resolve the current node attempt without letting a stale runtime snapshot
 * permanently mask the newer session returned by the authoritative REST read.
 */
export function resolveNodeSessionId(
  runtimeSessionId: string | null,
  restoredSessionId: string | null,
  runtimeSessionIdAtRestore: string | null,
): string | null {
  if (!restoredSessionId) return runtimeSessionId;

  // A runtime change after the REST request is newer than that response. This
  // keeps live attempt switches immediate while the follow-up REST read runs.
  if (
    runtimeSessionId &&
    runtimeSessionId !== runtimeSessionIdAtRestore
  ) {
    return runtimeSessionId;
  }

  // Otherwise the REST response is the authoritative attempt. In particular,
  // it must replace an old task snapshot after a retry.
  return restoredSessionId;
}
