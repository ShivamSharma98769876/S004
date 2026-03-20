export default function Loading() {
  return (
    <main className="loading-shell" aria-busy="true" aria-live="polite">
      <div className="skeleton topbar" />
      <div className="skeleton heading" />
      <div className="skeleton-row">
        <div className="skeleton card" />
        <div className="skeleton card" />
        <div className="skeleton card" />
        <div className="skeleton card" />
      </div>
      <div className="skeleton table" />
    </main>
  );
}
