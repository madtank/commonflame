import { ArrowLeft, LogOut, ShieldCheck } from "lucide-react";
import { AdminPage } from "@/pages/AdminPage";
import { Logo } from "@/components/Logo";
import { Button } from "@/components/ui/button";

type ModernAdminPageProps = {
  username?: string | null;
  onLogout?: () => void;
  isAdminUser: boolean;
  isAdminValidated: boolean;
};

export default function ModernAdminPage({
  username,
  onLogout,
  isAdminUser,
  isAdminValidated,
}: ModernAdminPageProps) {
  return (
    <div className="relative min-h-screen overflow-x-hidden bg-[#040914] text-white">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_18%_18%,rgba(59,130,246,0.24),transparent_24%),radial-gradient(circle_at_82%_18%,rgba(99,102,241,0.2),transparent_24%),radial-gradient(circle_at_70%_78%,rgba(251,191,36,0.14),transparent_28%),linear-gradient(180deg,#040914_0%,#091428_48%,#0b1020_100%)]" />
      <div className="absolute inset-0 opacity-35 [background-image:radial-gradient(rgba(255,255,255,0.34)_0.7px,transparent_0.7px)] [background-size:34px_34px]" />

      <div className="relative z-10 mx-auto flex min-h-screen w-full max-w-6xl flex-col px-4 py-4 sm:px-6 lg:px-8">
        <header className="rounded-[28px] border border-cyan-300/20 bg-slate-950/65 px-4 py-4 backdrop-blur-2xl">
          <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
            <div className="flex items-start gap-4">
              <Logo
                size="sm"
                showText
                className="border-white/10 bg-white/[0.04] shadow-[0_18px_42px_-28px_rgba(14,165,233,0.55)]"
              />
              <div className="space-y-2">
                <div className="inline-flex items-center gap-2 rounded-full border border-cyan-300/20 bg-cyan-400/10 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.24em] text-cyan-100">
                  <ShieldCheck className="h-3.5 w-3.5" />
                  Admin Console
                </div>
                <div>
                  <h1 className="text-2xl font-semibold text-white">
                    Manage users without the legacy shell
                  </h1>
                  <p className="mt-1 text-sm text-slate-300">
                    Review accounts, promote members to Plus, and keep admin
                    work in the modern Waystation Platform surface.
                  </p>
                </div>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-3">
              {username ? (
                <div className="rounded-2xl border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-slate-200">
                  Signed in as <span className="font-medium text-white">{username}</span>
                </div>
              ) : null}
              <Button
                asChild
                variant="outline"
                className="border-white/15 bg-white/[0.04] text-white hover:bg-white/10 hover:text-white"
              >
                <a href="/ax">
                  <ArrowLeft className="mr-2 h-4 w-4" />
                  Back to platform
                </a>
              </Button>
              {onLogout ? (
                <Button
                  variant="outline"
                  onClick={onLogout}
                  className="border-white/15 bg-white/[0.04] text-white hover:bg-white/10 hover:text-white"
                >
                  <LogOut className="mr-2 h-4 w-4" />
                  Sign out
                </Button>
              ) : null}
            </div>
          </div>
        </header>

        <main className="flex-1 py-6">
          <div className="rounded-[30px] border border-white/10 bg-slate-950/50 p-4 backdrop-blur-2xl sm:p-6">
            {isAdminUser && isAdminValidated ? (
              <AdminPage />
            ) : isAdminUser && !isAdminValidated ? (
              <div className="flex min-h-[18rem] items-center justify-center">
                <div className="text-center">
                  <div className="mx-auto h-10 w-10 animate-spin rounded-full border-b-2 border-cyan-300" />
                  <p className="mt-4 text-sm text-slate-300">
                    Validating admin access...
                  </p>
                </div>
              </div>
            ) : (
              <div className="flex min-h-[18rem] items-center justify-center">
                <div className="max-w-md rounded-3xl border border-rose-400/20 bg-rose-500/10 px-6 py-5 text-center">
                  <p className="text-lg font-semibold text-white">
                    Admin access required
                  </p>
                  <p className="mt-2 text-sm text-rose-100/85">
                    This surface is reserved for verified admins. Return to the
                    main platform if you need a different workspace.
                  </p>
                </div>
              </div>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}
