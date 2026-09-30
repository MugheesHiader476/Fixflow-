import Image from "next/image";
import Link from "next/link";
import { ArrowDown, ArrowUpRight, BookOpen, Bookmark, History } from "lucide-react";

export function WorkspaceHero() {
  return (
    <section className="ff-hero" aria-labelledby="workspace-title">
      <Image src="/images/developer-workspace.webp" alt="A softly lit developer workspace overlooking the city at blue hour" fill preload sizes="(max-width: 768px) 100vw, 94vw" className="ff-hero-image" />
      <div className="ff-hero-shade" />
      <div className="ff-hero-content">
        <p className="ff-eyebrow"><span aria-hidden="true" />A little context. A lot of clarity.</p>
        <h1 id="workspace-title">Less searching.<br />More building.</h1>
        <p>Your code. Your documentation. One clear workspace.<br className="hidden sm:block" /> Find the context you need to take the next step.</p>
        <div className="ff-hero-actions">
          <a className="ff-pill ff-pill-white" href="#workspace">Start a session <ArrowUpRight size={18} /></a>
          <Link className="ff-hero-link" href="/sources">Explore your knowledge <ArrowUpRight size={16} /></Link>
        </div>
      </div>
      <div className="ff-hero-bottom"><span>BUILT FOR THE WAY DEVELOPERS THINK</span><a href="#workspace" aria-label="Scroll to workspace"><ArrowDown size={18} /></a></div>
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
        <p>Add your guides, runbooks, and reference docs. FixFlow searches them for evidence relevant to your problem.</p>
        <Link href="/sources" className="ff-text-link">Add documentation <ArrowUpRight size={17} /></Link>
      </div>
    </aside>
  );
}

const WORKFLOWS = [
  { number: "01", icon: BookOpen, title: "Your knowledge, together.", description: "Bring your documentation into one place. Search the details that matter to your code.", href: "/sources", action: "Knowledge sources" },
  { number: "02", icon: History, title: "Pick up the thread.", description: "Every session keeps your inputs and conversation, ready for your next investigation.", href: "/history", action: "Debug history" },
  { number: "03", icon: Bookmark, title: "Keep what works.", description: "Save useful findings and references. Make the next debugging session a little easier.", href: "/saved", action: "Saved solutions" },
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
