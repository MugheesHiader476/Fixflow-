"use client";

import { Component, lazy, Suspense, useEffect, useRef, useState, type ReactNode } from "react";

const Scene = lazy(() => import("./knowledge-scene"));

function StaticVisual() {
  return <div className="ff-visual-fallback"><span /><span /><span /><i /></div>;
}

class VisualBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? <StaticVisual /> : this.props.children; }
}

export function KnowledgeVisual() {
  const root = useRef<HTMLDivElement>(null);
  const [ready, setReady] = useState(false);
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    const connection = (navigator as Navigator & { connection?: { saveData?: boolean } }).connection;
    if (connection?.saveData || typeof window.matchMedia !== "function") return;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    const small = window.matchMedia("(max-width: 700px)");
    let observer: IntersectionObserver | undefined;
    const update = () => {
      observer?.disconnect();
      if (reduced.matches || small.matches || !window.IntersectionObserver) { setReady(false); return; }
      observer = new IntersectionObserver(([entry]) => setReady(entry.isIntersecting), { rootMargin: "80px" });
      if (root.current) observer.observe(root.current);
    };
    update();
    reduced.addEventListener("change", update);
    small.addEventListener("change", update);
    return () => { observer?.disconnect(); reduced.removeEventListener("change", update); small.removeEventListener("change", update); };
  }, []);

  return <div ref={root} className="ff-knowledge-visual" aria-hidden="true">
    {ready && !unavailable ? <VisualBoundary><Suspense fallback={<StaticVisual />}><Scene onUnavailable={() => setUnavailable(true)} /></Suspense></VisualBoundary> : <StaticVisual />}
  </div>;
}
