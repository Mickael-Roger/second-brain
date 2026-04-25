import { useQuery } from "@tanstack/react-query";

import { api, ApiError, type MeResponse } from "./api";

export function useMe() {
  return useQuery<MeResponse | null>({
    queryKey: ["me"],
    queryFn: async () => {
      try {
        return await api.get<MeResponse>("/api/auth/me");
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return null;
        throw e;
      }
    },
    staleTime: 30_000,
  });
}

export async function login(username: string, password: string) {
  return api.post<MeResponse>("/api/auth/login", { username, password });
}

export async function logout() {
  return api.post<{ ok: boolean }>("/api/auth/logout");
}
