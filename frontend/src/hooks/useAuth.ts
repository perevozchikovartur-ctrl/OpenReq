import { useState, useCallback, useEffect } from "react";
import type { User } from "@/types";
import { authApi, usersApi } from "@/api/endpoints";

const IS_STANDALONE = import.meta.env.VITE_STANDALONE === "true";

export function useAuth() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchUser = useCallback(async () => {
    if (IS_STANDALONE) {
      setUser({
        id: "local",
        email: "local@openreq",
        username: "local",
        full_name: "Local User",
        is_active: true,
        instance_role: "admin",
      });
      setLoading(false);
      return;
    }
    // The backend redirects here after completing the OIDC authorization-code flow.
    const callbackToken = window.location.pathname === "/oidc/callback"
      ? new URLSearchParams(window.location.search).get("token")
      : null;
    if (callbackToken) {
      localStorage.setItem("openreq-token", callbackToken);
      window.history.replaceState({}, "", "/");
    }
    const token = callbackToken || localStorage.getItem("openreq-token");
    if (!token) {
      setLoading(false);
      return;
    }
    try {
      const { data } = await usersApi.me();
      setUser(data);
    } catch {
      // Never clear the token automatically — network hiccup, server restart,
      // whatever. The token never expires so it will work again once the
      // server is back. Only the explicit Logout button removes the token.
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchUser();
  }, [fetchUser]);

  const login = useCallback(async (email: string, password: string) => {
    if (IS_STANDALONE) return;
    const { data } = await authApi.login(email, password);
    localStorage.setItem("openreq-token", data.access_token);
    await fetchUser();
  }, [fetchUser]);

  const logout = useCallback(() => {
    if (IS_STANDALONE) return;
    localStorage.removeItem("openreq-token");
    setUser(null);
  }, []);

  return { user, loading, login, logout, refetchUser: fetchUser };
}
