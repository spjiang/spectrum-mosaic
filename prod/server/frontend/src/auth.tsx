import React, { createContext, useContext, useMemo, useState } from "react";
import { api, getToken, setToken } from "./api";
import { rolesFromToken } from "./roles";

const USER_KEY = "mosaic_user";
const ROLES_KEY = "mosaic_roles";

function readStoredRoles(token: string | null): string[] {
  try {
    const raw = localStorage.getItem(ROLES_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) return parsed.map(String);
    }
  } catch {
    /* ignore */
  }
  return rolesFromToken(token);
}

type AuthState = {
  token: string | null;
  roles: string[];
  username: string | null;
  login: (u: string, p: string) => Promise<void>;
  logout: () => void;
};

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setTok] = useState<string | null>(getToken());
  const [roles, setRoles] = useState<string[]>(() => readStoredRoles(getToken()));
  const [username, setUsername] = useState<string | null>(() => localStorage.getItem(USER_KEY));
  const value = useMemo<AuthState>(
    () => ({
      token,
      roles,
      username,
      async login(u, p) {
        const r = await api.login(u, p);
        setToken(r.access_token);
        setTok(r.access_token);
        const nextRoles = r.roles || [];
        setRoles(nextRoles);
        localStorage.setItem(ROLES_KEY, JSON.stringify(nextRoles));
        localStorage.setItem(USER_KEY, u);
        setUsername(u);
      },
      logout() {
        setToken(null);
        setTok(null);
        setRoles([]);
        localStorage.removeItem(USER_KEY);
        localStorage.removeItem(ROLES_KEY);
        setUsername(null);
      },
    }),
    [token, roles, username]
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth() {
  const v = useContext(Ctx);
  if (!v) throw new Error("AuthProvider missing");
  return v;
}
