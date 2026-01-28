"use client";

import React from "react";
import AuthGuard from "../components/AuthGuard";
import Header from "../components/Header";
import Tabs from "../components/Tabs";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthGuard>
      {/* Margens laterais reduzidas (≈1/3 do padrão anterior) para ampliar a área útil */}
      <div style={{ maxWidth: 1400, margin: "0 auto", padding: "0 6px" }}>
        <Header />
        <Tabs />
        {children}
      </div>
    </AuthGuard>
  );
}
