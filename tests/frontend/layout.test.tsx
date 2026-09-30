import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppShell } from "@/components/layout/app-shell";
import { RightPanel } from "@/components/layout/right-panel";
import { ThemeProvider, useTheme } from "@/components/layout/theme-provider";
import { ASYNCIO_DIAGNOSIS } from "./fixtures/diagnosis";

const api = vi.hoisted(() => ({
  checkBackendHealth: vi.fn(),
  listSessions: vi.fn(),
}));
const navigation = vi.hoisted(() => ({ pathname: "/sources" }));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  checkBackendHealth: api.checkBackendHealth,
  listSessions: api.listSessions,
}));
vi.mock("next/navigation", () => ({ usePathname: () => navigation.pathname }));
vi.mock("next/link", () => ({
  default: ({ children, href, ...properties }: { children: ReactNode; href: string }) => (
    <a href={href} {...properties}>{children}</a>
  ),
}));
vi.mock("@clerk/nextjs", () => ({
  Show: ({ children }: { children: ReactNode }) => <>{children}</>,
  SignInButton: ({ children }: { children: ReactNode }) => <>{children}</>,
  SignUpButton: ({ children }: { children: ReactNode }) => <>{children}</>,
  UserButton: () => <button>User account</button>,
}));

function ThemeState() {
  const { dark, toggle } = useTheme();
  return <button onClick={toggle}>{dark ? "Dark" : "Light"}</button>;
}

beforeEach(() => {
  localStorage.clear();
  Object.defineProperty(HTMLDialogElement.prototype, "showModal", { configurable: true, value: function (this: HTMLDialogElement) { this.setAttribute("open", ""); } });
  Object.defineProperty(HTMLDialogElement.prototype, "close", { configurable: true, value: function (this: HTMLDialogElement) { this.removeAttribute("open"); } });
  api.checkBackendHealth.mockReset();
  api.listSessions.mockReset();
  vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
    callback(performance.now());
    return 1;
  });
  vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => undefined);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("application layout", () => {
  it("loads backend and recent-session status and handles shell controls", async () => {
    api.checkBackendHealth.mockResolvedValue({ status: "ok", service: "fixflow-api" });
    api.listSessions.mockResolvedValue([
      {
        id: "session-1",
        title: "Async failure",
        technology: ["Python"],
        createdAt: new Date().toISOString(),
        status: "resolved",
        confidence: 92,
        errorMessage: "event loop",
      },
    ]);
    const togglePanel = vi.fn();

    render(
      <ThemeProvider>
        <AppShell onToggleRightPanel={togglePanel} rightPanel={<aside>Context details</aside>}>
          Workspace content
        </AppShell>
      </ThemeProvider>
    );

    expect(await screen.findByText("Connected")).toBeDefined();
    expect(await screen.findByText("Async failure")).toBeDefined();
    expect(screen.getByText("Documentation")).toBeDefined();
    expect(screen.getByText("Context details")).toBeDefined();

    fireEvent.click(screen.getByRole("button", { name: "Open menu" }));
    const menu = screen.getByRole("dialog", { name: "Workspace menu" });
    expect(within(menu).getByRole("link", { name: /Knowledge Sources/ }).getAttribute("aria-current")).toBe("page");
    expect(within(menu).getByRole("link", { name: /Async failure/ }).getAttribute("href")).toBe("/?session=session-1");
    fireEvent.click(screen.getByRole("button", { name: "Close menu" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Toggle context panel" }));
    expect(togglePanel).toHaveBeenCalledOnce();
  });

  it("shows an offline state when health and history requests fail", async () => {
    api.checkBackendHealth.mockRejectedValue(new Error("offline"));
    api.listSessions.mockRejectedValue(new Error("offline"));

    render(
      <ThemeProvider>
        <AppShell>Workspace content</AppShell>
      </ThemeProvider>
    );

    expect(await screen.findByText("Offline")).toBeDefined();
    expect(screen.queryByText("Async failure")).toBeNull();
  });

  it("switches right-panel tabs and only links valid external sources", () => {
    const onClose = vi.fn();
    render(
      <RightPanel
        diagnosis={ASYNCIO_DIAGNOSIS}
        open
        onClose={onClose}
        files={["worker.py"]}
        repoUrl="https://github.com/example/project"
        techs={["Python", "asyncio"]}
      />
    );

    const links = screen.getAllByRole("link");
    expect(links.every((link) => link.getAttribute("rel") === "noopener noreferrer")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Context" }));
    expect(screen.getByText("92%")).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "Project" }));
    expect(screen.getByText("worker.py")).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "Close panel" }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("uses the editorial light theme by default and keeps dark mode available", async () => {
    render(<ThemeProvider><ThemeState /></ThemeProvider>);
    expect(await screen.findByRole("button", { name: "Light" })).toBeDefined();
    expect(document.documentElement.dataset.theme).toBe("light");
    fireEvent.click(screen.getByRole("button", { name: "Light" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("restores and persists the selected theme", async () => {
    localStorage.setItem("ff-theme", "light");
    render(
      <ThemeProvider>
        <ThemeState />
      </ThemeProvider>
    );

    expect(await screen.findByRole("button", { name: "Light" })).toBeDefined();
    expect(document.documentElement.dataset.theme).toBe("light");
    fireEvent.click(screen.getByRole("button", { name: "Light" }));
    await waitFor(() => expect(localStorage.getItem("ff-theme")).toBe("dark"));
  });
});
