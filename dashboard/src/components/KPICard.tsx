import React from "react";

interface KPICardProps {
  title: string;
  value: string | number;
  subtitle?: string;
  icon: React.ReactNode;
  accentColor?: "cyan" | "violet" | "emerald" | "amber" | "rose";
  breakdown?: { label: string; value: string | number }[];
}

export const KPICard: React.FC<KPICardProps> = ({
  title,
  value,
  subtitle,
  icon,
  accentColor = "cyan",
  breakdown,
}) => {
  const colorMap = {
    cyan: "var(--accent-cyan)",
    violet: "var(--accent-violet)",
    emerald: "var(--accent-emerald)",
    amber: "var(--accent-amber)",
    rose: "var(--accent-rose)",
  };

  const activeColor = colorMap[accentColor];

  return (
    <div className="glass-panel kpi-card">
      <div className="kpi-header">
        <span className="kpi-title">{title}</span>
        <div className="kpi-icon-wrap" style={{ color: activeColor }}>
          {icon}
        </div>
      </div>

      <div className="kpi-value">{value}</div>

      {subtitle && <div className="kpi-subtext">{subtitle}</div>}

      {breakdown && breakdown.length > 0 && (
        <div
          style={{
            marginTop: "14px",
            paddingTop: "12px",
            borderTop: "1px solid var(--border-subtle)",
            display: "flex",
            flexWrap: "wrap",
            gap: "12px",
          }}
        >
          {breakdown.map((item, idx) => (
            <div key={idx} style={{ fontSize: "0.76rem" }}>
              <span style={{ color: "var(--text-muted)" }}>{item.label}: </span>
              <span style={{ color: "var(--text-primary)", fontWeight: 600, fontFamily: "var(--font-mono)" }}>
                {item.value}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
