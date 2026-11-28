/**
 * Pure conversation-identity rules for the assistant widget.
 *
 * Keeping these rules outside React means the privacy behaviour can be tested without a
 * browser: a profile change must produce a fresh neutral conversation, while re-applying
 * the same identity must leave the current conversation untouched.
 */

/**
 * W5: the server's view of the constraints this conversation has carried, e.g.
 * ``{ country: "Canada", degree_level: "MASTER" }``.
 *
 * This is a read-only projection of server-owned state. The widget never sends these values and
 * is never their authority: the backend derives every constraint from the student's own words or
 * from their verified profile, and a profile switch clears it. It is typed here so a future
 * change to the contract is caught at compile time rather than discovered at runtime.
 */
export type AssistantContext = {
  version: number;
  constraints: Record<string, string>;
  /** Slots the student explicitly ruled out, e.g. ["country"] after "I don't want Canada". */
  excluded: string[];
};

export type Turn = {
  role: "user" | "assistant";
  text: string;
  sources?: Record<string, any>[];
  suggestions?: string[];
  grounded?: boolean;
  escalated?: boolean;
  pending?: boolean;
};

export const OPENING_TURN: Turn = {
  role: "assistant",
  grounded: true,
  text:
    "I answer from Scholara's verified database only — universities, programmes, entry requirements, " +
    "real costs, intakes, scholarships and PhD supervisors. If we have not verified something, I will " +
    "say so instead of guessing.",
  suggestions: [
    "Masters in computer science with IELTS 6.5",
    "What does a year in Canada actually cost?",
    "Fully funded PhD supervisors in machine learning",
    "Which countries suit a 25 lakh taka budget?",
  ],
};

export function initialTurns(): Turn[] {
  return [OPENING_TURN];
}

/** Opaque in-memory identity key. Never rendered, persisted or logged. */
export function identityOf(profileToken: string | null): string {
  return profileToken ? `profile:${profileToken}` : "anonymous";
}

export function identityChanged(previous: string, next: string): boolean {
  return previous !== next;
}

export type ResetState = {
  turns: Turn[];
  busy: boolean;
  input: string;
};

/** State after a real identity transition: no questions, answers, errors or pending work. */
export function resetForIdentity(): ResetState {
  return { turns: initialTurns(), busy: false, input: "" };
}

/**
 * Whether a response still belongs to the identity and session generation that started it.
 *
 * Success, error and finally paths all use this before touching visible state. A response
 * from Student A can therefore never be appended after the browser has moved to Student B.
 */
export function requestIsCurrent(
  startIdentity: string,
  startEpoch: number,
  currentIdentity: string,
  currentEpoch: number,
): boolean {
  return startIdentity === currentIdentity && startEpoch === currentEpoch;
}
