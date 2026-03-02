import { useState, type ReactNode } from "react";
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
    if (typeof window !== "undefined") {
      window.sessionStorage.removeItem(STORAGE_KEY);
    }
  };

  return (
    <OperatorAuthContext.Provider value={{ token, setToken, clearToken }}>
      {children}
    </OperatorAuthContext.Provider>
  );
}
