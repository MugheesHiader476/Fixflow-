import { GitBranch, Hash, Mail, Triangle } from "lucide-react";
import type { ConnectorProvider } from "@/lib/connector-contracts";

const ICONS = { gmail: Mail, github: GitBranch, google_drive: Triangle, slack: Hash };
export function ProviderIcon({ provider }: { provider: ConnectorProvider }) {
  const Icon = ICONS[provider];
  return <span className={"ff-provider-icon ff-provider-" + provider} aria-hidden="true"><Icon size={25} strokeWidth={1.6} /></span>;
}
