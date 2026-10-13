import React from "react";
import { AlertTriangle, RotateCcw } from "lucide-react";

interface ErrorStateProps {
  message: string;
  onRetry?: () => void;
}

export const ErrorState: React.FC<ErrorStateProps> = ({ message, onRetry }) => {
  const isAuthError = message.toLowerCase().includes("auth") || message.toLowerCase().includes("401");

  return (
    <div
      className="glass-panel"
      style={{
        margin: "32px auto",
        maxWidth: "540px",
        padding: "32px",
        textAlign: "center",
        borderColor: "rgba(244, 63, 94, 0.3)",
        background: "rgba(30, 20, 30, 0.6)",
      }}
    >
      <div
        style={{
          width: "48px",
          height: "48px",
          borderRadius: "50%",
          background: "rgba(244, 63, 94, 0.15)",
          color: "var(--accent-rose)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          margin: "0 auto 16px",
        }}
      >
        <AlertTriangle size={24} />
      </div>

      <h3 style={{ fontSize: "1.1rem", fontWeight: 700, marginBottom: "8px" }}>
        {isAuthError ? "Authentication Required" : "Failed to Load Data"}
      </h3>

      <p style={{ fontSize: "0.86rem", color: "var(--text-secondary)", marginBottom: "20px" }}>
        {message}
      </p>

      {onRetry && (
        <button onClick={onRetry} className="btn btn-primary" style={{ margin: "0 auto" }}>
          <RotateCcw size={15} />
          <span>Retry Request</span>
        </button>
      )}
    </div>
  );
};
