import type { ReactNode } from "react";
import Image from "next/image";
import { ArrowUpRight, BookOpen, History, Bookmark } from "lucide-react";
import { Brand } from "./brand";

export function AccountShell({ children }: { children: ReactNode }) {
  return (
    <main className="ff-account">
      <div className="ff-account-story">
        <Image src="/images/developer-workspace.webp" alt="An atmospheric software development workspace" fill preload sizes="(max-width: 900px) 100vw, 55vw" className="ff-hero-image" />
        <div className="ff-hero-shade" />
        <div className="ff-account-brand"><Brand /><span>YOUR KNOWLEDGE WORKSPACE <ArrowUpRight size={16} /></span></div>
        <div className="ff-account-copy"><p className="ff-eyebrow">A little context. A lot of clarity.</p><h1>Your sources.<br />Clearer answers.</h1><p>Bring your documents, data, and authorized apps together. Ask questions and inspect the evidence behind each answer.</p></div>
        <div className="ff-account-benefits"><span><BookOpen size={17} />Your sources</span><span><History size={17} />Your conversations</span><span><Bookmark size={17} />Your saved answers</span></div>
      </div>
      <div className="ff-account-form"><div className="ff-account-welcome"><p className="ff-eyebrow">WELCOME TO FIXFLOW</p><h2>Make room for clarity.</h2><p>Sign in or create an account to open your workspace.</p></div>{children}<p className="ff-account-note">Private sources. Answers with evidence.</p></div>
    </main>
  );
}
