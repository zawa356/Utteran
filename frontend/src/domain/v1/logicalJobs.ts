/**
 * Logical jobs (UI-3-0 Design Freeze §7, owner decision 2026-11-16).
 *
 * B7 keeps its retry contract: a retry is a *new* internal job with the same snapshot, linked to the
 * job it retries by `retry_of`. The screen shows one row per logical job = one `retry_of` chain; the
 * state and the artifacts shown are those of the newest internal job of the chain. A `rerun` is a
 * different logical job (new snapshot); `rerun_of` only says where it came from.
 *
 * Pure: no I/O, no clock. Internal job ids stay the unit for the API, CAS and output directories.
 */

import type { JobV1 } from "../../api/v1Resources";

export interface LogicalJob {
  /** Internal id of the first job of the chain that is present in the input (stable row key). */
  readonly rootId: string;
  /** Internal jobs of this logical job, oldest first. Never empty. */
  readonly attempts: readonly JobV1[];
  /** The newest internal job: its status, error and artifacts are what the row shows. */
  readonly latest: JobV1;
  /** Internal ids of every job in the chain, oldest first (for per-job actions such as hide). */
  readonly jobIds: readonly string[];
  /** The logical job this one was re-run from, when that job is present; else the raw id or null. */
  readonly rerunOfRootId: string | null;
  /** True when the chain refers to a job that is not in the input (hidden, filtered or other page). */
  readonly incomplete: boolean;
}

/**
 * Groups jobs (in list order, oldest first) into logical jobs, in the order of each chain's first job.
 *
 * Fail-safe rules: a job whose `retry_of` is not in the input starts its own row (marked incomplete);
 * a cycle or a duplicate id never loops or drops a job (the offending link is ignored); when one job
 * has several retries they all belong to the same logical job, ordered as listed.
 */
export function groupLogicalJobs(jobs: readonly JobV1[]): LogicalJob[] {
  const byId = new Map<string, JobV1>();
  const order: JobV1[] = [];
  for (const job of jobs) {
    if (byId.has(job.id)) continue; // duplicate id: keep the first occurrence only
    byId.set(job.id, job);
    order.push(job);
  }
  const rootOf = new Map<string, string>();
  const incomplete = new Set<string>();
  const findRoot = (job: JobV1): string => {
    const seen = new Set<string>([job.id]);
    let current = job;
    for (;;) {
      const known = rootOf.get(current.id);
      if (known !== undefined) return known;
      const parentId = current.retryOf;
      if (parentId === null) return current.id;
      const parent = byId.get(parentId);
      if (parent === undefined) {
        incomplete.add(current.id);
        return current.id;
      }
      if (seen.has(parent.id)) return current.id; // cycle: cut it here
      seen.add(parent.id);
      current = parent;
    }
  };
  const chains = new Map<string, JobV1[]>();
  for (const job of order) {
    const root = findRoot(job);
    rootOf.set(job.id, root);
    const chain = chains.get(root);
    if (chain === undefined) chains.set(root, [job]);
    else chain.push(job);
  }
  const result: LogicalJob[] = [];
  for (const [rootId, chain] of chains) {
    // The chain's first listed job is the row's anchor; the root may be listed later only if the
    // server order was not creation order, so sort the root to the front defensively.
    const attempts = [...chain].sort((a, b) => Number(b.id === rootId) - Number(a.id === rootId));
    const root = attempts[0]!;
    const rerunOf = root.rerunOf;
    result.push({
      rootId,
      attempts,
      latest: attempts[attempts.length - 1]!,
      jobIds: attempts.map((job) => job.id),
      rerunOfRootId: rerunOf === null ? null : (rootOf.get(rerunOf) ?? rerunOf),
      incomplete: attempts.some((job) => incomplete.has(job.id)),
    });
  }
  return result;
}

/** The logical job that contains this internal job id, if any. */
export function logicalJobOf(groups: readonly LogicalJob[], jobId: string): LogicalJob | null {
  return groups.find((group) => group.jobIds.includes(jobId)) ?? null;
}
