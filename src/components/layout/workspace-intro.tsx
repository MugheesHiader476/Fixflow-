import Image from "next/image";
import Link from "next/link";
import { ArrowDown, ArrowUpRight, BookOpen, Bookmark, History } from "lucide-react";

export function WorkspaceHero() {
  return (
    <section className="ff-hero" aria-labelledby="hero-title">
      <Image src="/images/developer-workspace.webp" alt="A softly lit developer workspace overlooking the city at blue hour" fill sizes="(max-width: 768px) 100vw, (max-width: 1200px) 75vw, 1100px" className="ff-hero-image" />
      <div className="ff-hero-shade" />
      <div className="ff-hero-content">
        <p className="ff-eyebrow"><span aria-hidden="true" />A little context. A lot of clarity.</p>
        <h2 id="hero-title">Your knowledge.<br />Structured content.</h2>
        <p>Your documents, data, and connected apps.<br className="hidden sm:block" /> Inspect and parse content for your next workflow.</p>
        <div className="ff-hero-actions">
          <a className="ff-pill ff-pill-white" href="#workspace">Open workspace <ArrowUpRight size={18} /></a>
          <Link className="ff-hero-link" href="/sources">Explore your knowledge <ArrowUpRight size={16} /></Link>
        </div>
      </div>
      <div className="ff-hero-bottom"><span>FROM YOUR SOURCES TO YOUR NEXT STEP</span><a href="#workspace" aria-label="Scroll to workspace"><ArrowDown size={18} /></a></div>
    </section>
  );
}

export function WorkspaceGuide() {
  return (
    <aside className="ff-workspace-guide">
      <div className="ff-guide-image"><Image src="/images/documentation-desk.webp" alt="Code and technical notebooks together on a sunlit desk" fill sizes="(max-width: 900px) 100vw, 30vw" /></div>
      <div className="ff-guide-content">
        <BookOpen size={21} strokeWidth={1.5} />
        <h3>Good context.<br />Better next steps.</h3>
        <p>Add documents, policies, spreadsheets, or code. Connect an authorized app to bring in its selected content.</p>
        <Link href="/sources" className="ff-text-link">Add a source <ArrowUpRight size={17} /></Link>
      </div>
    </aside>
  );
}

const WORKFLOWS = [
  { number: "01", icon: BookOpen, title: "Your knowledge, together.", description: "Bring your documentation into one place. Parse content while preserving its structure.", href: "/sources", action: "Knowledge sources" },
  { number: "02", icon: History, title: "Pick up the thread.", description: "Revisit your earlier conversations and their original references, all in one place.", href: "/history", action: "Debug history" },
  { number: "03", icon: Bookmark, title: "Keep what works.", description: "Save useful findings and references. Keep the useful details close at hand.", href: "/saved", action: "Saved solutions" },
];

export function WorkspaceFeatures() {
  return (
    <section className="ff-features" aria-label="Explore your workspace">
      {WORKFLOWS.map((item) => (
        <Link key={item.number} href={item.href} className="ff-feature">
          <div className="ff-feature-top"><item.icon size={24} strokeWidth={1.4} /><span>{item.number}</span></div>
          <h3>{item.title}</h3><p>{item.description}</p>
          <span className="ff-text-link">{item.action}<ArrowUpRight size={17} /></span>
        </Link>
      ))}
    </section>
  );
}
