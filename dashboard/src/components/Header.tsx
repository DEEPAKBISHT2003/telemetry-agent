import React from "react";
import { Calendar, Key, RefreshCw, CheckCircle2 } from "lucide-react";
import type { DateRangePreset, NavigationTab } from "../types/analytics";

interface HeaderProps {
  activeTab: NavigationTab;
  dateRange: DateRangePreset;
  onSelectDateRange: (preset: DateRangePreset) => void;
  onRefresh: () => void;
  onOpenAuth: () => void;
  hasAuth: boolean;
  authPreview: string;
}

const titles: Record<NavigationTab, { title: string; subtitle: string }> = {
  overview: {
    title: "Operations Console",
    subtitle: "High-level developer activity, AI run metrics, and token accounting",
  },
  developers: {
    title: "Developers Directory",
    subtitle: "Authorized team roster with activity classification and token consumption",
  },
  models: {
    title: "AI Models Analytics",
    subtitle: "Model distribution, context caching efficiency, and token comparisons",
  },
  sessions: {
    title: "Workstation Sessions",
    subtitle: "Active and completed IDE execution sessions across repositories",
  },
  devices: {
    title: "Enrolled Devices",
    subtitle: "Hardware registry, authentication tokens, and reporting heartbeat status",
  },
};

export const Header: React.FC<HeaderProps> = ({
  activeTab,
  dateRange,
  onSelectDateRange,
  onRefresh,
  onOpenAuth,
  hasAuth,
  authPreview,
}) => {
  const current = titles[activeTab];

  return (
    <header className="top-header">
      <div>
        <h1 style={{ fontSize: "1.25rem", fontWeight: 700, letterSpacing: "-0.01em" }}>
          {current.title}
        </h1>
        <p style={{ fontSize: "0.8rem", color: "var(--text-secondary)", marginTop: "2px" }}>
          {current.subtitle}
        </p>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
        {/* Date Preset Selector */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            background: "rgba(15, 23, 42, 0.8)",
            border: "1px solid var(--border-subtle)",
            borderRadius: "var(--radius-md)",
            padding: "3px",
          }}
        >
          <Calendar size={14} style={{ margin: "0 8px", color: "var(--text-muted)" }} />
          {(["7d", "30d", "90d"] as DateRangePreset[]).map((preset) => (
            <button
              key={preset}
              onClick={() => onSelectDateRange(preset)}
              style={{
                background: dateRange === preset ? "rgba(6, 182, 212, 0.15)" : "transparent",
                color: dateRange === preset ? "var(--accent-cyan)" : "var(--text-secondary)",
                border: "none",
                borderRadius: "var(--radius-sm)",
                padding: "4px 10px",
                fontSize: "0.78rem",
                fontWeight: 600,
                cursor: "pointer",
                transition: "all 0.15s ease",
              }}
            >
              {preset.toUpperCase()}
            </button>
          ))}
        </div>

        {/* Refresh button */}
        <button
          onClick={onRefresh}
          className="btn btn-secondary"
          title="Refresh Data"
          style={{ padding: "8px 10px" }}
        >
          <RefreshCw size={15} />
        </button>

        {/* Auth configuration button */}
        <button
          onClick={onOpenAuth}
          className="btn btn-secondary"
          style={{
            borderColor: hasAuth ? "rgba(16, 185, 129, 0.3)" : "rgba(245, 158, 11, 0.3)",
          }}
        >
          {hasAuth ? (
            <>
              <CheckCircle2 size={15} color="var(--accent-emerald)" />
              <span style={{ fontSize: "0.8rem", fontFamily: "var(--font-mono)" }}>
                {authPreview}
              </span>
            </>
          ) : (
            <>
              <Key size={15} color="var(--accent-amber)" />
              <span style={{ fontSize: "0.8rem" }}>Set Auth Key</span>
            </>
          )}
        </button>
      </div>
    </header>
  );
};
