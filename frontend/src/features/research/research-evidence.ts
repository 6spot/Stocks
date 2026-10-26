import type { AnalysisEvidenceRef } from "@/api/schemas";

export function referenceKey(ref: AnalysisEvidenceRef): string {
  return JSON.stringify([ref.document_id, ref.chunk_id]);
}

// Keep the first occurrence in frozen input order for every citation surface.
export function uniqueEvidenceReferences<T extends AnalysisEvidenceRef>(refs: T[]): T[] {
  const seen = new Set<string>();
  return refs.filter((ref) => {
    const key = referenceKey(ref);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
