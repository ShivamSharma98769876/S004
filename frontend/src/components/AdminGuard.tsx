"use client";

import { useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import { isAdmin } from "@/lib/api_client";

export default function AdminGuard({ children }: { children: ReactNode }) {
  const router = useRouter();

  useEffect(() => {
    if (!isAdmin()) {
      router.replace("/dashboard");
    }
  }, [router]);

  if (!isAdmin()) {
    return (
      <div className="auth-loading">
        <span>Redirecting…</span>
      </div>
    );
  }

  return <>{children}</>;
}
