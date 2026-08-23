import { useState, type FormEvent } from "react";
import { useNavigate } from "@tanstack/react-router";

import { useSession } from "../auth/SessionProvider";
import { PUBLIC_SYNTHETIC_CREDENTIAL_REFERENCE } from "../auth/sessionClient";

const PUBLIC_SYNTHETIC_TENANT_ID = "10000000-0000-4000-8000-000000000001";
export const PUBLIC_SYNTHETIC_MATTER_ID =
  "20000000-0000-4000-8000-000000000101";
export const PUBLIC_SYNTHETIC_MATTER_PATH = `/matters/${PUBLIC_SYNTHETIC_MATTER_ID}`;

export function publicSyntheticPreviewEnabled(value: unknown): boolean {
  return value === "1";
}

const PUBLIC_SYNTHETIC_PREVIEW_ENABLED =
  import.meta.env.VITE_SKLEGAL_PUBLIC_SYNTHETIC_PREVIEW === "1";

export function SignInPage() {
  const [failed, setFailed] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const { signIn } = useSession();
  const navigate = useNavigate();

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setFailed(false);
    setSubmitting(true);
    try {
      await signIn(
        PUBLIC_SYNTHETIC_CREDENTIAL_REFERENCE,
        PUBLIC_SYNTHETIC_TENANT_ID,
      );
      await navigate({
        to: "/matters/$matterId",
        params: { matterId: PUBLIC_SYNTHETIC_MATTER_ID },
      });
    } catch {
      setFailed(true);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section aria-labelledby="sl-sign-in-heading">
      <h1 id="sl-sign-in-heading">Sign in</h1>
      <p>
        Sign-in establishes a bounded server session. Browser route guards are
        usability controls only; the API authorizes every request again.
      </p>
      {PUBLIC_SYNTHETIC_PREVIEW_ENABLED ? (
        <form onSubmit={submit} data-public-synthetic-preview="enabled">
          <button type="submit" className="sl-button" disabled={submitting}>
            {submitting ? "Starting session" : "Start public-synthetic session"}
          </button>
        </form>
      ) : (
        <p>Internal authentication is unavailable in this build.</p>
      )}
      {failed ? (
        <p role="alert">Sign-in failed. No session was retained.</p>
      ) : null}
    </section>
  );
}
