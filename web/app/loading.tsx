export default function Loading() {
  return <div className="page-wrap" aria-label="Loading"><div className="skeleton title" /><div className="skeleton strip" /><div className="metric-grid">{Array.from({ length: 4 }, (_, index) => <div className="skeleton metric-card" key={index} />)}</div><div className="skeleton panel-block" /></div>;
}
