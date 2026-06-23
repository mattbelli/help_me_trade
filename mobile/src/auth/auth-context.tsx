import { apiFetch, ApiError } from '@/api/client';
import { clearToken, getToken, setToken } from '@/auth/token-store';
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

export interface AuthContextValue {
  token: string | null;
  isLoading: boolean;
  signIn: (token: string) => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setTokenState] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    void (async () => {
      try {
        const stored = await getToken();
        if (!cancelled) {
          setTokenState(stored);
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  const signIn = useCallback(async (nextToken: string) => {
    const trimmed = nextToken.trim();
    if (!trimmed) {
      throw new Error('API token is required');
    }

    // Validate against an authenticated route before persisting.
    try {
      await apiFetch('/api/watchlist', { token: trimmed });
    } catch (err) {
      if (
        err instanceof ApiError &&
        (err.status === 401 || err.status === 403)
      ) {
        throw new Error('Invalid API token');
      }
      throw err;
    }

    await setToken(trimmed);
    setTokenState(trimmed);
  }, []);

  const signOut = useCallback(async () => {
    await clearToken();
    setTokenState(null);
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ token, isLoading, signIn, signOut }),
    [token, isLoading, signIn, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

/** Access auth state and sign-in/out actions. Must be used within AuthProvider. */
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth must be used within AuthProvider');
  }
  return ctx;
}
