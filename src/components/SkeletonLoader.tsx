/**
 * SkeletonLoader — shimmer placeholder preserving layout while async data loads.
 * Replaces the old pattern of flashing "Loading…" text which causes layout shift.
 *
 * Usage:
 *   {loading ? <SkeletonLoader lines={3} /> : <ActualContent />}
 */

export function SkeletonLoader({ lines = 3, height }: { lines?: number; height?: string }) {
  return (
    <div className="skeleton" role="status" aria-label="Loading data">
      {Array.from({ length: lines }, (_, i) => (
        <div
          key={i}
          className="skeleton__line"
          style={{
            width: i === lines - 1 ? "60%" : "100%",
            height: height ?? "1rem",
          }}
        />
      ))}
    </div>
  );
}
