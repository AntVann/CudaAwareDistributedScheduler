import { createContext, useContext } from "react";
import type { MeResponse } from "./api/client";

export type OperatorAuthContextValue = {
  token: string;
  setToken: (token: string) => void;
  clearToken: () => void;
  me: MeResponse | null;
  meError: string | null;
  loadingMe: boolean;
};

export const OperatorAuthContext = createContext<OperatorAuthContextValue | null>(null);

export function useOperatorAuth() {
  const context = useContext(OperatorAuthContext);
  if (!context) {
    throw new Error("useOperatorAuth must be used within OperatorAuthProvider");
  }
  return context;
}
