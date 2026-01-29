// app/components/PasswordInput.tsx
"use client";

import React, { useState } from "react";

export default function PasswordInput(props: {
  name: string;
  placeholder?: string;
  autoComplete?: string;
  inputStyle?: React.CSSProperties;
}) {
  const { name, placeholder, autoComplete, inputStyle } = props;
  const [show, setShow] = useState(false);

  return (
    <div style={{ position: "relative", width: "100%" }}>
      <input
        name={name}
        type={show ? "text" : "password"}
        autoComplete={autoComplete}
        placeholder={placeholder}
        style={{
          ...inputStyle,
          paddingRight: 44, // espaço para o botão
        }}
      />

      <button
        type="button"
        onClick={() => setShow((v) => !v)}
        title={show ? "Ocultar senha" : "Mostrar senha"}
        aria-label={show ? "Ocultar senha" : "Mostrar senha"}
        style={{
          position: "absolute",
          right: 8,
          top: "50%",
          transform: "translateY(-50%)",
          height: 30,
          width: 34,
          borderRadius: 8,
          border: "1px solid #e5e7eb",
          background: "#fff",
          cursor: "pointer",
          fontSize: 14,
          lineHeight: "30px",
        }}
      >
        {show ? "🙈" : "👁️"}
      </button>
    </div>
  );
}
