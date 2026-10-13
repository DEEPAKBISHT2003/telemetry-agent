import React from "react";
import {
  LayoutDashboard,
  Users,
  Cpu,
  Terminal,
  Laptop,
  ShieldCheck,
} from "lucide-react";
import type { NavigationTab } from "../types/analytics";

interface SidebarProps {
  activeTab: NavigationTab;
  onSelectTab: (tab: NavigationTab) => void;
}

export const Sidebar: React.FC<SidebarProps> = ({ activeTab, onSelectTab }) => {
  const navItems: { id: NavigationTab; label: string; icon: React.ReactNode }[] = [
    { id: "overview", label: "Overview", icon: <LayoutDashboard size={18} /> },
    { id: "developers", label: "Developers", icon: <Users size={18} /> },
    { id: "models", label: "AI Models", icon: <Cpu size={18} /> },
    { id: "sessions", label: "Sessions", icon: <Terminal size={18} /> },
    { id: "devices", label: "Devices", icon: <Laptop size={18} /> },
  ];

  return (
    <aside className="sidebar">
      <div style={{ padding: "24px 20px 20px", display: "flex", alignItems: "center", gap: "10px" }}>
        <div
          style={{
            width: "34px",
            height: "34px",
            borderRadius: "8px",
            background: "linear-gradient(135deg, #06b6d4, #8b5cf6)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            boxShadow: "0 0 16px rgba(6, 182, 212, 0.4)",
          }}
        >
          <ShieldCheck size={20} color="#ffffff" />
        </div>
        <div>
          <div style={{ fontWeight: 800, fontSize: "1.05rem", letterSpacing: "-0.02em" }}>
            Antigravity
          </div>
          <div style={{ fontSize: "0.72rem", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
            Telemetry Core
          </div>
        </div>
      </div>

      <nav style={{ marginTop: "14px", flex: 1 }}>
        {navItems.map((item) => (
          <button
            key={item.id}
            onClick={() => onSelectTab(item.id)}
            className={`nav-item ${activeTab === item.id ? "active" : ""}`}
          >
            {item.icon}
            <span>{item.label}</span>
          </button>
        ))}
      </nav>

      <div style={{ padding: "18px 20px", borderTop: "1px solid var(--border-subtle)", fontSize: "0.75rem", color: "var(--text-muted)" }}>
        <div>Phase 5.6 Analytics</div>
        <div style={{ marginTop: "2px", color: "var(--accent-cyan)", fontFamily: "var(--font-mono)" }}>v0.1.2 Backend</div>
      </div>
    </aside>
  );
};
