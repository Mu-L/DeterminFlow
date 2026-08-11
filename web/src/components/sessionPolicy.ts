import type { Session } from "../types";

export function canDeleteMainSession(
  session: Session,
  activeMainSessionId: string | null,
): boolean {
  return session.session_id !== activeMainSessionId;
}
