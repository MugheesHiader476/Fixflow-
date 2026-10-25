import Link from "next/link";
import { ShieldCheck } from "lucide-react";

export function Brand() {
  return (
    <Link href="/" className="ff-brand" aria-label="ProofOS home">
      <span className="ff-brand-mark" aria-hidden="true"><ShieldCheck size={26} strokeWidth={2} /></span>
      <span>ProofOS<span className="text-accent">.</span></span>
    </Link>
  );
}
