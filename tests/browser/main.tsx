import { createRoot } from "react-dom/client";
import DebugSessionPage from "@/app/page";
import SourcesPage from "@/app/sources/page";
import HistoryPage from "@/app/history/page";
import SavedPage from "@/app/saved/page";
import SettingsPage from "@/app/settings/page";
import ConnectorsPage from "@/app/connectors/page";
import ProjectsPage from "@/app/projects/page";
import ApprovalsPage from "@/app/approvals/page";
import { ProjectWorkspaceView } from "@/components/proofos/workspace";
import { ThemeProvider } from "@/components/layout/theme-provider";
import { ToastProvider } from "@/components/ui/toast";
import { AccountShell } from "@/components/layout/account-shell";
import "@/app/globals.css";

function TestAccount() {
  return <AccountShell><div className="rounded-xl border border-border p-8 text-sm text-muted">Clerk account form is mocked in this visual test.<br />Live authentication is checked separately.</div></AccountShell>;
}
function TestProject() { return <ProjectWorkspaceView projectId={window.location.pathname.split("/")[2]} />; }
const pages: Record<string, typeof DebugSessionPage> = { "/": DebugSessionPage, "/sources": SourcesPage, "/history": HistoryPage, "/saved": SavedPage, "/settings": SettingsPage, "/connectors": ConnectorsPage, "/projects": ProjectsPage, "/approvals": ApprovalsPage, "/account": TestAccount };
const Page = /^\/projects\/[^/]+$/.test(window.location.pathname) ? TestProject : pages[window.location.pathname] ?? DebugSessionPage;
createRoot(document.getElementById("root")!).render(<ThemeProvider><ToastProvider><Page /></ToastProvider></ThemeProvider>);
