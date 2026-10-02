import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "./api";
import type { Meta, Settings } from "./types";

export interface Route {
  path: string;
  params: URLSearchParams;
}

function parseHash(): Route {
  const raw = window.location.hash.replace(/^#/, "") || "/today";
  const [path, query = ""] = raw.split("?");
  return { path: path || "/today", params: new URLSearchParams(query) };
}

export function useRoute(): [Route, (path: string, params?: Record<string, string>) => void] {
  const [route, setRoute] = useState<Route>(parseHash);
  useEffect(() => {
    const onChange = () => setRoute(parseHash());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  const navigate = useCallback((path: string, params?: Record<string, string>) => {
    const qs = params && Object.keys(params).length ? `?${new URLSearchParams(params).toString()}` : "";
    window.location.hash = `${path}${qs}`;
  }, []);
  return [route, navigate];
}

interface AppState {
  meta: Meta | null;
  settings: Settings | null;
  loadError: string | null;
  saveSettings: (patch: Record<string, unknown>) => Promise<Settings>;
  refreshSettings: () => Promise<void>;
  refreshMeta: () => Promise<void>;
  toast: (message: string) => void;
  navigate: (path: string, params?: Record<string, string>) => void;
}

const Ctx = createContext<AppState | null>(null);

export function AppProvider({ children, navigate }: { children: ReactNode; navigate: AppState["navigate"] }) {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [toastMsg, setToastMsg] = useState<string | null>(null);

  const refreshMeta = useCallback(async () => {
    try {
      setMeta(await api.meta());
      setLoadError(null);
    } catch (e) {
      setLoadError((e as Error).message);
    }
  }, []);
  const refreshSettings = useCallback(async () => {
    try {
      setSettings(await api.settings());
    } catch (e) {
      setLoadError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    void refreshMeta();
    void refreshSettings();
  }, [refreshMeta, refreshSettings]);

  const saveSettings = useCallback(async (patch: Record<string, unknown>) => {
    const s = await api.saveSettings(patch);
    setSettings(s);
    return s;
  }, []);

  const toast = useCallback((message: string) => {
    setToastMsg(message);
    window.setTimeout(() => setToastMsg((cur) => (cur === message ? null : cur)), 4500);
  }, []);

  const value = useMemo(
    () => ({ meta, settings, loadError, saveSettings, refreshSettings, refreshMeta, toast, navigate }),
    [meta, settings, loadError, saveSettings, refreshSettings, refreshMeta, toast, navigate],
  );
  return (
    <Ctx.Provider value={value}>
      {children}
      {toastMsg ? (
        <div className="toast" role="status">
          {toastMsg}
        </div>
      ) : null}
    </Ctx.Provider>
  );
}

export function useApp(): AppState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useApp outside provider");
  return v;
}

/** Run an async action, tracking loading and error state. */
export function useAction<A extends unknown[], R>(fn: (...args: A) => Promise<R>) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = useCallback(
    async (...args: A): Promise<R | undefined> => {
      setLoading(true);
      setError(null);
      try {
        return await fn(...args);
      } catch (e) {
        setError((e as Error).message);
        return undefined;
      } finally {
        setLoading(false);
      }
    },
    [fn],
  );
  return { run, loading, error, setError };
}
