/** Tiny runtime guards for response parsing; a failed guard becomes a `malformed` ApiError. */
export class ShapeError extends Error {}

export function record(value: unknown, where: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new ShapeError(where);
  return value as Record<string, unknown>;
}

export function str(value: unknown, where: string): string {
  if (typeof value !== "string") throw new ShapeError(where);
  return value;
}

export function optStr(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

export function bool(value: unknown, where: string): boolean {
  if (typeof value !== "boolean") throw new ShapeError(where);
  return value;
}

export function int(value: unknown, where: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) throw new ShapeError(where);
  return value;
}

export function list(value: unknown, where: string): unknown[] {
  if (!Array.isArray(value)) throw new ShapeError(where);
  return value;
}

export function oneOf<T extends string>(value: unknown, allowed: readonly T[], where: string): T {
  if (typeof value !== "string" || !(allowed as readonly string[]).includes(value)) throw new ShapeError(where);
  return value as T;
}
