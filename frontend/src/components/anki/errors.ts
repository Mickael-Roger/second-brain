import { ApiError } from "@/lib/api";

export function errorMessage(error: Error): string {
  if (error instanceof ApiError && typeof error.detail === "object" && error.detail
      && "detail" in error.detail && typeof error.detail.detail === "string") return error.detail.detail;
  return error.message;
}
