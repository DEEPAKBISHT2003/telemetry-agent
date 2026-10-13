import React from "react";
import { Database, Inbox } from "lucide-react";

interface EmptyStateProps {
  title?: string;
  description?: string;
  actionText?: string;
  onAction?: () => void;
}

export const EmptyState: React.FC<EmptyStateProps> = ({
  title = "No Telemetry Recorded",
  description = "No telemetry events match the selected filters or date window. Events will appear once workstations ingest telemetry batches.",
  actionText,
  onAction,
}) => {
  return (
    <div
      className="glass-panel"
      style={{
        padding: "48px 24px",
        textAlign: "center",
        maxWidth: "480px",
        margin: "32px auto",
      }}
    >
      <div
        style={{
          width: "48px",
          height: "48px",
          borderRadius: "50%",
          background: "rgba(255, 255, 255, 0.05)",
          color: "var(--text-muted)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          margin: "0 auto 16px",
        }}
      >
        <Inbox size={24} />
      </div>

      <h3 style={{ fontSize: "1.05rem", fontWeight: 600, marginBottom: "8px" }}>
        {title}
      </h3>

      <p style={{ fontSize: "0.85rem", color: "var(--text-secondary)", marginBottom: actionText ? "20px" : "0" }}>
        {description}
      </p>

      {actionText && onAction && (
        <button onClick={onAction} className="btn btn-secondary" style={{ margin: "0 auto" }}>
          <Database size={15} />
          <span>{actionText}</span>
        </button>
      )}
    </div>
  );
};
