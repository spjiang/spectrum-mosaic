import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "./api";
import { useAuth } from "./auth";
import type { Features } from "./roles";

type RbacState = {
  mapping: Record<string, string[]>;
  /** 后端开关决定的功能。推扫未开启时相关菜单一律不显示。 */
  features: Features;
  /** 首次加载完成前为 false，依赖开关的路由此时先不做判断 */
  ready: boolean;
  reload: () => Promise<void>;
};

const FEATURES_KEY = "mosaic_features";

function cachedFeatures(): Features {
  try {
    const raw = JSON.parse(localStorage.getItem(FEATURES_KEY) || "{}");
    return raw && typeof raw === "object" ? raw : {};
  } catch {
    return {};
  }
}

const Ctx = createContext<RbacState | null>(null);

export function RbacProvider({ children }: { children: ReactNode }) {
  const { token } = useAuth();
  const [mapping, setMapping] = useState<Record<string, string[]>>({});
  const [features, setFeatures] = useState<Features>(cachedFeatures);
  const [ready, setReady] = useState(false);

  const reload = useCallback(async () => {
    if (!token) {
      setMapping({});
      setFeatures({});
      setReady(false);
      return;
    }
    const [menus, pushbroom] = await Promise.allSettled([api.rbac(), api.pushbroomStatus()]);
    setMapping(menus.status === "fulfilled" ? menus.value.menus || {} : {});
    const next = { pushbroom: pushbroom.status === "fulfilled" && pushbroom.value.enabled };
    localStorage.setItem(FEATURES_KEY, JSON.stringify(next));
    setFeatures(next);
    setReady(true);
  }, [token]);

  useEffect(() => {
    reload().catch(() => undefined);
  }, [reload]);

  return <Ctx.Provider value={{ mapping, features, ready, reload }}>{children}</Ctx.Provider>;
}

export function useRbac() {
  const v = useContext(Ctx);
  if (!v) throw new Error("RbacProvider missing");
  return v;
}
