import Link from "next/link";
import { ArrowUpRight } from "lucide-react";

export function Brand() {
  return (
    <Link href="/" className="ff-brand" aria-label="FixFlow home">
      <span className="ff-brand-mark" aria-hidden="true"><ArrowUpRight size={26} strokeWidth={2.5} /></span>
      <span>fixflow<span className="text-accent">.</span></span>
    </Link>
  );
}
