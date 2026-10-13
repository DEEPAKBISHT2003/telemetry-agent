import React, { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, GitBranch, FolderGit2 } from "lucide-react";
import { api } from "../api/client";
import type { DateRangePreset, SessionListResponse } from "../types/analytics";
import { LoadingState } from "../components/LoadingState";
import { ErrorState } from "../components/ErrorState";
import { EmptyState } from "../components/EmptyState";
import { formatTokens, formatNumber, formatDateTime, truncate } from "../utils/format";

interface SessionsPageProps {
  dateRange: DateRangePreset;
  refreshTrigger: number;
}

export const SessionsPage: React.FC<SessionsPageProps> = ({ dateRange, refreshTrigger }) => {
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<SessionListResponse | null>(null);
  const [page, setPage] = useState<number>(1);
  const pageSize = 15;

  const calculateDateBounds = () => {
    const end = new Date();
    const start = new Date();
    if (dateRange === "7d") start.setDate(end.getDate() - 7);
    else if (dateRange === "30d") start.setDate(end.getDate() - 30);
    else if (dateRange === "90d") start.setDate(end.getDate() - 90);
    else return {};

    return {
      start_date: start.toISOString(),
      end_date: end.toISOString(),
    };
  };

  const loadSessions = async () => {
    setLoading(true);
    setError(null);
    try {
      const bounds = calculateDateBounds();
      const res = await api.getSessions({
        ...bounds,
        page,
        page_size: pageSize,
      });
      setData(res);
    } catch (err: any) {
      setError(err?.message || "Failed to load telemetry sessions.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadSessions();
  }, [dateRange, page, refreshTrigger]);

  if (loading) return <LoadingState message="Querying active and historical coding sessions..." />;
  if (error) return <ErrorState message={error} onRetry={loadSessions} />;
  if (!data || data.items.length === 0) {
    return (
      <EmptyState
        title="No Sessions Found"
        description="No workstation IDE sessions recorded in this time range."
      />
    );
  }

  return (
    <div style={{ padding: "28px 32px" }}>
      <div className="glass-panel" style={{ padding: "4px" }}>
        <div style={{ padding: "16px 20px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <h2 style={{ fontSize: "1rem", fontWeight: 700 }}>IDE Workstation Sessions</h2>
            <p style={{ fontSize: "0.78rem", color: "var(--text-muted)" }}>
              Grouped by unique session ID with developer identity and repository attribution
            </p>
          </div>
          <div style={{ fontSize: "0.82rem", color: "var(--text-secondary)" }}>
            Total Sessions: <strong>{data.total_count}</strong>
          </div>
        </div>

        <div className="table-container">
          <table className="data-table">
            <thead>
              <tr>
                <th>Session ID</th>
                <th>Developer</th>
                <th>Repository</th>
                <th>Branch</th>
                <th>Primary Model</th>
                <th>AI Runs</th>
                <th>Event Count</th>
                <th>Total Tokens</th>
                <th>Started</th>
                <th>Last Active</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((s) => (
                <tr key={s.session_id}>
                  <td>
                    <span className="mono-text" title={s.session_id}>
                      {truncate(s.session_id, 16)}
                    </span>
                  </td>
                  <td>
                    <div style={{ fontWeight: 600 }}>{s.developer_name}</div>
                  </td>
                  <td>
                    {s.repository_name ? (
                      <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                        <FolderGit2 size={14} color="var(--accent-cyan)" />
                        <span>{s.repository_name}</span>
                      </div>
                    ) : (
                      <span style={{ color: "var(--text-muted)" }}>—</span>
                    )}
                  </td>
                  <td>
                    {s.branch ? (
                      <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                        <GitBranch size={13} color="var(--accent-violet)" />
                        <span style={{ background: "rgba(255,255,255,0.06)", padding: "2px 6px", borderRadius: "4px", fontSize: "0.78rem" }}>
                          {s.branch}
                        </span>
                      </div>
                    ) : (
                      <span style={{ color: "var(--text-muted)" }}>—</span>
                    )}
                  </td>
                  <td>
                    {s.primary_model ? (
                      <span className="badge badge-model">{s.primary_model}</span>
                    ) : (
                      <span style={{ color: "var(--text-muted)" }}>—</span>
                    )}
                  </td>
                  <td>
                    <span style={{ fontWeight: 600, fontFamily: "var(--font-mono)" }}>
                      {formatNumber(s.ai_run_count)}
                    </span>
                  </td>
                  <td>
                    <span style={{ fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>
                      {formatNumber(s.event_count)}
                    </span>
                  </td>
                  <td>
                    <span style={{ fontWeight: 700, color: "var(--accent-cyan)", fontFamily: "var(--font-mono)" }}>
                      {formatTokens(s.total_tokens)}
                    </span>
                  </td>
                  <td style={{ fontSize: "0.78rem", color: "var(--text-secondary)" }}>
                    {formatDateTime(s.started_at)}
                  </td>
                  <td style={{ fontSize: "0.78rem", color: "var(--text-secondary)" }}>
                    {formatDateTime(s.last_activity_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {data.total_pages > 1 && (
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              padding: "16px 20px",
              borderTop: "1px solid var(--border-subtle)",
            }}
          >
            <div style={{ fontSize: "0.82rem", color: "var(--text-secondary)" }}>
              Page <strong>{data.page}</strong> of <strong>{data.total_pages}</strong>
            </div>
            <div style={{ display: "flex", gap: "8px" }}>
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={data.page <= 1}
                className="btn btn-secondary"
                style={{ opacity: data.page <= 1 ? 0.4 : 1, cursor: data.page <= 1 ? "not-allowed" : "pointer" }}
              >
                <ChevronLeft size={16} />
                <span>Previous</span>
              </button>
              <button
                onClick={() => setPage((p) => Math.min(data.total_pages, p + 1))}
                disabled={data.page >= data.total_pages}
                className="btn btn-secondary"
                style={{ opacity: data.page >= data.total_pages ? 0.4 : 1, cursor: data.page >= data.total_pages ? "not-allowed" : "pointer" }}
              >
                <span>Next</span>
                <ChevronRight size={16} />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
