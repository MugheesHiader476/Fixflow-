import { useSyncExternalStore } from "react";
const subscribe = (callback: () => void) => {
  window.addEventListener("popstate", callback);
  return () => window.removeEventListener("popstate", callback);
};
export function usePathname() { return useSyncExternalStore(subscribe, () => window.location.pathname); }
export function useSearchParams() {
  const search = useSyncExternalStore(subscribe, () => window.location.search);
  return new URLSearchParams(search);
}
const router = {
  replace(url: string) { window.history.replaceState(null, "", url); window.dispatchEvent(new PopStateEvent("popstate")); },
};
export function useRouter() { return router; }
