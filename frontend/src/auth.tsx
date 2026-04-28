import { useEffect, useState, type ReactNode } from "react";
import { fetchMe, type MeResponse } from "./api/client";
import { OperatorAuthContext } from "./auth-context";

const STORAGE_KEY = "operator_api_token";

function loadStoredToken(): string {
  if (typeof window === "undefined") {
    return "";
  }
  return window.sessionStorage.getItem(STORAGE_KEY) ?? "";
}

export function OperatorAuthProvider({ children }: { children: ReactNode }) {
  const [token, setTokenState] = useState(loadStoredToken);
  const [me, setMe] = useState<MeResponse | null>(null);
  const [meError, setMeError] = useState<string | null>(null);
  const [loadingMe, setLoadingMe] = useState(false);

  const setToken = (nextToken: string) => {
    const trimmed = nextToken.trim();
    setTokenState(trimmed);
    if (typeof window !== "undefined") {
      if (trimmed) {
        window.sessionStorage.setItem(STORAGE_KEY, trimmed);
      } else {
        window.sessionStorage.removeItem(STORAGE_KEY);
      }
    }
  };

  const clearToken = () => {
    setTokenState("");
    setMe(null);
    setMeError(null);
    if (typeof window !== "undefined") {
      window.sessionStorage.removeItem(STORAGE_KEY);
    }
  };

  useEffect(() => {
    let cancelled = false;
    if (!token) {
      setMe(null);
      setMeError(null);
      return;
    }

    async function loadMe() {
      setLoadingMe(true);
      try {
        const identity = await fetchMe(token);
        if (!cancelled) {
          setMe(identity);
          setMeError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setMe(null);
          setMeError(err instanceof Error ? err.message : "Failed to validate token");
        }
      } finally {
        if (!cancelled) {
          setLoadingMe(false);
        }
      }
    }

    void loadMe();
    return () => {
      cancelled = true;
    };
  }, [token]);

  return (
    <OperatorAuthContext.Provider value={{ token, setToken, clearToken, me, meError, loadingMe }}>
      {children}
    </OperatorAuthContext.Provider>
  );
}
