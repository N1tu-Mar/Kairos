import { ErrorState } from "@/components/states";
import { ApiError } from "@/lib/api";

/** Renders an ApiError with a hint the reader can act on. */
export function ApiErrorState({
  error,
  what,
}: {
  error: unknown;
  what: string;
}) {
  if (error instanceof ApiError) {
    return (
      <ErrorState
        title={`Could not load ${what}`}
        message={error.userMessage}
        hint={
          error.kind === "unreachable" ? (
            <>
              Start the backend with{" "}
              <code className="font-mono text-xs">
                uv run uvicorn api.main:app --reload
              </code>{" "}
              and confirm <code className="font-mono text-xs">KAIROS_API_URL</code>{" "}
              points at it.
            </>
          ) : (
            <span className="font-mono text-xs break-words">{error.message}</span>
          )
        }
      />
    );
  }
  return (
    <ErrorState
      title={`Could not load ${what}`}
      message={
        error instanceof Error ? error.message : "An unexpected error occurred."
      }
    />
  );
}
