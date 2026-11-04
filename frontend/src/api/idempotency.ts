/**
 * One Idempotency-Key per user *intent* (B5/B7: `[A-Za-z0-9_.-]{16,128}`, visible ASCII).
 * An explicit retry of the same intent reuses the key; a new intent gets a new key.
 */
export type RandomSource = (bytes: Uint8Array<ArrayBuffer>) => Uint8Array<ArrayBuffer>;

const defaultRandom: RandomSource = (bytes) => crypto.getRandomValues(bytes);

export function newIdempotencyKey(random: RandomSource = defaultRandom): string {
  const bytes = random(new Uint8Array(new ArrayBuffer(16)));
  return "ui2-" + Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

export const IDEMPOTENCY_KEY_PATTERN = /^[A-Za-z0-9_.-]{16,128}$/;

export type IntentState = "new" | "sent" | "accepted" | "rejected" | "unknown";

export class MutationIntent<B> {
  readonly key: string;
  readonly body: B;
  state: IntentState = "new";
  attempts = 0;

  constructor(body: B, random?: RandomSource) {
    this.body = body;
    this.key = newIdempotencyKey(random);
  }
}
