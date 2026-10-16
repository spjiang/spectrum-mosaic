import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "./api";
import { useAuth } from "./auth";

type RbacState = {
  mapping: Record<string, string[]>;
  reload: () => Promise<void>;
};

const Ctx = createContext<RbacState | null>(null);

export function RbacProvider({ children }: { children: ReactNode }) {
  const { token } = useAuth();
  const [mapping, setMapping] = useState<Record<string, string[]>>({});

  const reload = useCallback(async () => {
    if (!token) {
      setMapping({});
      return;
    }
    try {
      const data = await api.rbac();
      setMapping(data.menus || {});
    } catch {
      setMapping({});
    }
  }, [token]);

  useEffect(() => {
    reload().catch(() => undefined);
  }, [reload]);

  return <Ctx.Provider value={{ mapping, reload }}>{children}</Ctx.Provider>;
}

export function useRbac() {
  const v = useContext(Ctx);
  if (!v) throw new Error("RbacProvider missing");
  return v;
}
