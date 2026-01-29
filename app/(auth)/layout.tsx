import React from "react";
import Header from "../components/Header";
import Tabs from "../components/Tabs";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ maxWidth: "85%", margin: "0 auto", padding: "0 6px" }}>
      <Header />
      <Tabs />
      {children}
    </div>
  );
}
