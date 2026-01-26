"use client";

import React from "react";
import AuthGuard from "../components/AuthGuard";
import Header from "../components/Header";
import Tabs from "../components/Tabs";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthGuard>
      <div style={{ maxWidth: 1200, margin: "0 auto", padding: "0 16px" }}>
        <Header />
        <Tabs />
        {children}
      </div>
    </AuthGuard>
  );
}
