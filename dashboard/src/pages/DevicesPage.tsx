import React, { useEffect, useState } from "react";
import { Laptop } from "lucide-react";
import { api } from "../api/client";
import type { DeviceAnalyticsSummary } from "../types/analytics";
import { LoadingState } from "../components/LoadingState";
import { ErrorState } from "../components/ErrorState";
import { EmptyState } from "../components/EmptyState";
import { formatNumber, formatDateTime, truncate } from "../utils/format";

interface DevicesPageProps {
  refreshTrigger: number;
}

export const DevicesPage: React.FC<DevicesPageProps> = ({ refreshTrigger }) => {
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [devices, setDevices] = useState<DeviceAnalyticsSummary[]>([]);

  const loadDevices = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.getDevices();
      setDevices(res.items || []);
    } catch (err: any) {
      setError(err?.message || "Failed to load enrolled devices.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadDevices();
  }, [refreshTrigger]);

  if (loading) return <LoadingState message="Listing registered developer workstations..." />;
  if (error) return <ErrorState message={error} onRetry={loadDevices} />;
  if (devices.length === 0) {
    return (
      <EmptyState
        title="No Enrolled Devices"
        description="No workstations have been claimed under this organization yet. Complete enrollment via the enrollment API."
      />
    );
  }

  return (
    <div style={{ padding: "28px 32px" }}>
      <div className="glass-panel" style={{ padding: "4px" }}>
        <div style={{ padding: "16px 20px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <h2 style={{ fontSize: "1rem", fontWeight: 700 }}>Enrolled Workstations Inventory</h2>
            <p style={{ fontSize: "0.78rem", color: "var(--text-muted)" }}>
              Registered hardware endpoints with daemon version and telemetry heartbeat
            </p>
          </div>
          <div style={{ fontSize: "0.82rem", color: "var(--text-secondary)" }}>
            Total Devices: <strong>{devices.length}</strong>
          </div>
        </div>

        <div className="table-container">
          <table className="data-table">
            <thead>
              <tr>
                <th>Hostname</th>
                <th>Assigned Developer</th>
                <th>Device ID</th>
                <th>Platform</th>
                <th>Agent Version</th>
                <th>Total Ingested Events</th>
                <th>Last Active (Heartbeat)</th>
                <th>Lifecycle Status</th>
              </tr>
            </thead>
            <tbody>
              {devices.map((d) => (
                <tr key={d.device_id}>
                  <td>
                    <div style={{ display: "flex", alignItems: "center", gap: "8px", fontWeight: 600 }}>
                      <Laptop size={16} color="var(--accent-cyan)" />
                      <span>{d.hostname}</span>
                    </div>
                  </td>
                  <td>
                    <div style={{ fontWeight: 600 }}>{d.developer_name}</div>
                  </td>
                  <td>
                    <span className="mono-text" title={d.device_id}>
                      {truncate(d.device_id, 12)}
                    </span>
                  </td>
                  <td>
                    <span style={{ textTransform: "capitalize", background: "rgba(255,255,255,0.06)", padding: "2px 8px", borderRadius: "4px", fontSize: "0.78rem" }}>
                      {d.platform}
                    </span>
                  </td>
                  <td>
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: "0.8rem", color: "var(--accent-emerald)" }}>
                      v{d.agent_version}
                    </span>
                  </td>
                  <td>
                    <span style={{ fontWeight: 600, fontFamily: "var(--font-mono)" }}>
                      {formatNumber(d.total_events)}
                    </span>
                  </td>
                  <td style={{ fontSize: "0.8rem", color: "var(--text-secondary)" }}>
                    {d.last_seen_at ? formatDateTime(d.last_seen_at) : "Never Reported"}
                  </td>
                  <td>
                    <span
                      className={`badge ${
                        d.status === "ACTIVE"
                          ? "badge-active"
                          : d.status === "REVOKED"
                          ? "badge-inactive"
                          : "badge-role"
                      }`}
                    >
                      {d.status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
