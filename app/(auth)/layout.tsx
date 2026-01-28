"use client";

import React from "react";
import AuthGuard from "../components/AuthGuard";
import Header from "../components/Header";
import Tabs from "../components/Tabs";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthGuard>
      {/*
        Layout global: reduz margens laterais para dar mais área útil às tabelas.
        - maxWidth: 100% (antes 1200)
        - padding lateral: 6px (antes 16px)
      */}
      <div style={{ maxWidth: "80%", margin: "0 auto", padding: "0 6px" }}>
        <Header />
        <Tabs />
        {children}
      </div>
    </AuthGuard>
  );
}
