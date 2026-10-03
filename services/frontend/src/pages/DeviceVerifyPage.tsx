import { redirectToSignIn } from "@/lib/auth-utils";
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle,
  Loader2,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import { apiClient } from "@/lib/api-clean";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

type DeviceVerifyStatus =
  | "idle"
  | "loading"
  | "pending"
  | "approved"
  | "denied"
  | "expired"
  | "error"
  | "unknown";

type DeviceVerifyResponse = {
  status?: string;
  user_code?: string;
  client_name?: string;
  client_id?: string;
  agent_name?: string;
  scope?: string | string[];
  scopes?: string[];
  requested_scopes?: string[];
  expires_in?: number;
  expires_at?: string;
};

const normalizeStatus = (value?: string | null): DeviceVerifyStatus => {
  const status = (value || "").toLowerCase().trim();
  if (!status) return "pending";
  if (["approved", "authorized", "accepted"].includes(status))
    return "approved";
  if (["denied", "rejected"].includes(status)) return "denied";
  if (["expired", "invalid"].includes(status)) return "expired";
  if (["pending", "new", "awaiting"].includes(status)) return "pending";
  return "unknown";
};

const formatScopes = (value?: string | string[]) => {
  if (!value) return [] as string[];
  if (Array.isArray(value)) return value.filter(Boolean);
  return value
    .split(/[ ,]+/)
    .map((item) => item.trim())
    .filter(Boolean);
};

type DeviceVerifyPageProps = {
  userToken: string | null;
  username?: string | null;
};

export default function DeviceVerifyPage({
  userToken,
  username,
}: DeviceVerifyPageProps) {
  const userCode = useMemo(() => {
    const params = new URLSearchParams(window.location.search);
    return params.get("user_code") || "";
  }, []);

  const [status, setStatus] = useState<DeviceVerifyStatus>(
    userCode ? "loading" : "error",
  );
  const [verification, setVerification] = useState<DeviceVerifyResponse | null>(
    null,
  );
  const [errorMessage, setErrorMessage] = useState<string | null>(
    userCode ? null : "Missing user code. Please check your device link.",
  );
  const [pendingAction, setPendingAction] = useState<"approve" | "deny" | null>(
    null,
  );

  const isAuthenticated = Boolean(userToken);

  const displayCode = useMemo(() => {
    if (verification?.user_code) return verification.user_code;
    return userCode;
  }, [userCode, verification?.user_code]);

  const agentLabel =
    verification?.agent_name ||
    verification?.client_name ||
    verification?.client_id ||
    "Unknown agent";

  const scopeList = useMemo(() => {
    const scopes =
      verification?.scope ||
      verification?.scopes ||
      verification?.requested_scopes ||
      [];
    return formatScopes(scopes as string | string[]);
  }, [
    verification?.scope,
    verification?.scopes,
    verification?.requested_scopes,
  ]);

  const fetchVerification = useCallback(async () => {
    if (!userCode) return;
    setStatus("loading");
    setErrorMessage(null);
    try {
      const response = await apiClient.get("/oauth/device/verify", {
        params: { user_code: userCode },
      });
      const data = (response.data || {}) as DeviceVerifyResponse;
      setVerification(data);
      setStatus(normalizeStatus(data.status));
    } catch (error: any) {
      const detail = error?.response?.data?.detail;
      let message: string;
      if (typeof detail === "string") {
        message = detail;
      } else if (detail?.msg) {
        message = detail.msg;
      } else if (Array.isArray(detail)) {
        message = detail.map((d: any) => d.msg || String(d)).join(", ");
      } else {
        message = error?.message || "Unable to load this device request.";
      }
      setErrorMessage(message);
      setStatus("error");
    }
  }, [userCode]);

  useEffect(() => {
    fetchVerification();
  }, [fetchVerification]);

  const handleLogin = async () => {
    const redirectTarget =
      window.location.pathname + window.location.search + window.location.hash;
    await redirectToSignIn(redirectTarget);
  };

  const handleDecision = async (approved: boolean) => {
    if (!userCode) return;
    setPendingAction(approved ? "approve" : "deny");
    setErrorMessage(null);
    try {
      const formData = new URLSearchParams();
      formData.append("user_code", userCode);
      formData.append("approved", String(approved));
      if (userToken) {
        formData.append("jwt_token", userToken);
      }
      const response = await apiClient.post("/oauth/device/approve", formData, {
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        withCredentials: true,
      });
      const data = (response.data || {}) as DeviceVerifyResponse;
      setVerification((prev) => ({ ...prev, ...data }));
      setStatus(
        normalizeStatus(data.status || (approved ? "approved" : "denied")),
      );
    } catch (error: any) {
      const detail = error?.response?.data?.detail;
      let message: string;
      if (typeof detail === "string") {
        message = detail;
      } else if (detail?.msg) {
        message = detail.msg;
      } else if (Array.isArray(detail)) {
        message = detail.map((d: any) => d.msg || String(d)).join(", ");
      } else {
        message = error?.message || "Request failed. Please try again.";
      }
      setErrorMessage(message);
      setStatus("error");
    } finally {
      setPendingAction(null);
    }
  };

  const statusTitle = useMemo(() => {
    switch (status) {
      case "approved":
        return "Access approved";
      case "denied":
        return "Access denied";
      case "expired":
        return "Code expired";
      case "error":
        return "We hit a snag";
      case "loading":
        return "Checking code";
      case "unknown":
        return "Needs review";
      default:
        return "Awaiting approval";
    }
  }, [status]);

  const statusDescription = useMemo(() => {
    switch (status) {
      case "approved":
        return "You can close this window. The device will finish signing in.";
      case "denied":
        return "This sign-in request has been denied.";
      case "expired":
        return "The code is no longer valid. Please restart the flow.";
      case "error":
        return (
          errorMessage || "Something went wrong while loading this request."
        );
      case "loading":
        return "Fetching the request details…";
      case "unknown":
        return "Review the details below before deciding.";
      default:
        return "Confirm the code and approve the request if it looks right.";
    }
  }, [status, errorMessage]);

  return (
    <div className="min-h-screen bg-slate-950 text-white">
      <div className="mx-auto w-full max-w-3xl px-4 py-12 space-y-8">
        <header className="space-y-2">
          <p className="text-xs uppercase tracking-[0.35em] text-slate-400">
            Device Authorization
          </p>
          <h1 className="text-3xl md:text-4xl font-semibold">
            Approve a device sign-in
          </h1>
          <p className="text-slate-300">
            Match the code shown on your device to confirm the request.
          </p>
        </header>

        <div className="bg-slate-900/70 border border-slate-800 rounded-3xl p-6 md:p-8 shadow-2xl">
          <div className="flex items-start gap-3">
            {status === "loading" ? (
              <Loader2 className="h-6 w-6 text-slate-300 animate-spin" />
            ) : status === "approved" ? (
              <CheckCircle className="h-6 w-6 text-emerald-400" />
            ) : status === "denied" || status === "expired" ? (
              <XCircle className="h-6 w-6 text-rose-400" />
            ) : status === "error" ? (
              <AlertTriangle className="h-6 w-6 text-amber-400" />
            ) : (
              <ShieldCheck className="h-6 w-6 text-cyan-400" />
            )}
            <div className="space-y-1">
              <h2 className="text-xl font-semibold">{statusTitle}</h2>
              <p className="text-sm text-slate-300">{statusDescription}</p>
            </div>
          </div>

          <div className="mt-6 rounded-2xl border border-slate-800 bg-slate-950/60 p-5">
            <p className="text-xs uppercase tracking-[0.2em] text-slate-400">
              User code
            </p>
            <div className="mt-2 text-2xl md:text-3xl font-mono tracking-[0.35em] text-white">
              {displayCode || "—"}
            </div>
          </div>

          <div className="mt-6 grid gap-4 md:grid-cols-2">
            <div className="rounded-2xl border border-slate-800 bg-slate-950/50 p-4">
              <p className="text-xs uppercase tracking-[0.2em] text-slate-400">
                Agent
              </p>
              <p className="mt-2 text-lg font-semibold text-slate-100">
                {agentLabel}
              </p>
            </div>
            <div className="rounded-2xl border border-slate-800 bg-slate-950/50 p-4">
              <p className="text-xs uppercase tracking-[0.2em] text-slate-400">
                Scopes
              </p>
              <div className="mt-2 flex flex-wrap gap-2">
                {scopeList.length > 0 ? (
                  scopeList.map((scope) => (
                    <Badge
                      key={scope}
                      variant="secondary"
                      className="bg-slate-800 text-slate-100 border border-slate-700"
                    >
                      {scope}
                    </Badge>
                  ))
                ) : (
                  <span className="text-sm text-slate-300">
                    No scopes requested
                  </span>
                )}
              </div>
            </div>
          </div>

          {verification?.expires_at && (
            <p className="mt-4 text-xs text-slate-400">
              Expires at {new Date(verification.expires_at).toLocaleString()}
            </p>
          )}

          {isAuthenticated ? (
            <div className="mt-6 space-y-4">
              <div className="flex items-center gap-2 text-sm text-slate-300">
                <ShieldCheck className="h-4 w-4 text-emerald-400" />
                Signed in as {username || "User"}
              </div>

              <div className="flex flex-col sm:flex-row gap-3">
                <Button
                  onClick={() => handleDecision(true)}
                  disabled={
                    pendingAction !== null ||
                    status === "approved" ||
                    status === "denied" ||
                    status === "expired" ||
                    status === "loading"
                  }
                  className="flex-1 bg-emerald-500 text-white hover:bg-emerald-400"
                >
                  {pendingAction === "approve" ? (
                    <span className="inline-flex items-center gap-2">
                      <Loader2 className="h-4 w-4 animate-spin" />
                      Approving…
                    </span>
                  ) : (
                    "Approve"
                  )}
                </Button>
                <Button
                  variant="outline"
                  onClick={() => handleDecision(false)}
                  disabled={
                    pendingAction !== null ||
                    status === "approved" ||
                    status === "denied" ||
                    status === "expired" ||
                    status === "loading"
                  }
                  className="flex-1 border-rose-400/60 text-rose-200 hover:bg-rose-500/10"
                >
                  {pendingAction === "deny" ? (
                    <span className="inline-flex items-center gap-2">
                      <Loader2 className="h-4 w-4 animate-spin" />
                      Denying…
                    </span>
                  ) : (
                    "Deny"
                  )}
                </Button>
              </div>
            </div>
          ) : (
            <div className="mt-6 rounded-2xl border border-amber-500/30 bg-amber-500/10 p-4">
              <div className="flex items-start gap-3">
                <AlertTriangle className="h-5 w-5 text-amber-300" />
                <div>
                  <p className="text-sm font-semibold text-amber-100">
                    Sign in to continue
                  </p>
                  <p className="text-sm text-amber-200/80">
                    You need to authenticate before you can approve this
                    request.
                  </p>
                </div>
              </div>
              <Button
                onClick={handleLogin}
                className="mt-4 w-full bg-white text-slate-900 hover:bg-slate-100"
              >
                Sign in to Waystation
              </Button>
            </div>
          )}

          {status === "error" && errorMessage ? (
            <p className="mt-4 text-sm text-amber-300">{errorMessage}</p>
          ) : null}

          <div className="mt-6 flex items-center justify-between text-xs text-slate-500">
            <span>Need to re-check? Refresh the page.</span>
            <button
              type="button"
              onClick={fetchVerification}
              className="text-cyan-300 hover:text-cyan-200"
            >
              Refresh
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
