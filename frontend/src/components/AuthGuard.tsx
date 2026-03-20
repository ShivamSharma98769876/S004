"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { getAuth } from "@/lib/api_client";

export default function AuthGuard({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const isLoginPage = pathname === "/login";
    const auth = getAuth();

    if (isLoginPage) {
      if (auth) router.replace("/dashboard");
      setReady(true);
      return;
    }

    if (!auth) {
      router.replace("/login");
      return;
    }

    setReady(true);
  }, [pathname, router]);

  if (!ready) {
    return (
      <div className="auth-loading">
        <span>Loading…</span>
      </div>
    );
  }

  return <>{children}</>;
}
