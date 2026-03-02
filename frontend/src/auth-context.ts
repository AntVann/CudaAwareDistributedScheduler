import { createContext, useContext } from "react";

type OperatorAuthContextValue = {
  token: string;
  setToken: (token: string) => void;
  clearToken: () => void;
};

export const OperatorAuthContext = createContext<OperatorAuthContextValue | null>(null);

export function useOperatorAuth() {
  const context = useContext(OperatorAuthContext);
  if (!context) {
    throw new Error("useOperatorAuth must be used within OperatorAuthProvider");
  }
  return context;
}
