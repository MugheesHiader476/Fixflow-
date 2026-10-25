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
        <div className="ff-account-brand"><Brand /><span>FROM EVIDENCE TO ACCEPTANCE <ArrowUpRight size={16} /></span></div>
        <div className="ff-account-copy"><p className="ff-eyebrow">Make completed work count.</p><h1>Clear evidence.<br />Client confidence.</h1><p>Bring contractual requirements and delivery evidence together. Keep verification, customer acceptance and invoice readiness clear.</p></div>
        <div className="ff-account-benefits"><span><BookOpen size={17} />Your sources</span><span><History size={17} />Your conversations</span><span><Bookmark size={17} />Your saved answers</span></div>
      </div>
      <div className="ff-account-form"><div className="ff-account-welcome"><p className="ff-eyebrow">WELCOME TO PROOFOS</p><h2>Turn work into acceptance.</h2><p>Sign in or create an account to open your private workspace.</p></div>{children}<p className="ff-account-note">Your sources. Your evidence. Your approval.</p></div>
    </main>
  );
}
