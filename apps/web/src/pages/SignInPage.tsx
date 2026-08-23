import { useState, type FormEvent } from "react";
import { useNavigate } from "@tanstack/react-router";

import { useSession } from "../auth/SessionProvider";
import { PUBLIC_SYNTHETIC_CREDENTIAL_REFERENCE } from "../auth/sessionClient";

const PUBLIC_SYNTHETIC_TENANT_ID = "10000000-0000-4000-8000-000000000001";

export function SignInPage() {
  const [failed, setFailed] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const { signIn } = useSession();
  const navigate = useNavigate();
  const dev = import.meta.env.DEV;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setFailed(false);
    setSubmitting(true);
    try {
      await signIn(
        PUBLIC_SYNTHETIC_CREDENTIAL_REFERENCE,
        PUBLIC_SYNTHETIC_TENANT_ID,
      );
      await navigate({ to: "/" });
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
      {dev ? (
        <form onSubmit={submit}>
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
