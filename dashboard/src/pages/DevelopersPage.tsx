import React, { useEffect, useState } from "react";
import {
  Search,
  ChevronLeft,
  ChevronRight,
  User,
  Cpu,
  Terminal,
  X,
  ExternalLink,
} from "lucide-react";
import { api } from "../api/client";
import type {
  DateRangePreset,
  DeveloperDetailResponse,
  DeveloperListResponse,
} from "../types/analytics";
import { LoadingState } from "../components/LoadingState";
import { ErrorState } from "../components/ErrorState";
import { EmptyState } from "../components/EmptyState";
import { formatTokens, formatNumber, formatDate, formatDateTime, truncate } from "../utils/format";

interface DevelopersPageProps {
  dateRange: DateRangePreset;
  refreshTrigger: number;
}

export const DevelopersPage: React.FC<DevelopersPageProps> = ({ dateRange, refreshTrigger }) => {
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<DeveloperListResponse | null>(null);

  // Filters & Pagination
  const [search, setSearch] = useState<string>("");
  const [page, setPage] = useState<number>(1);
  const pageSize = 15;

  // Selected developer for detail modal
  const [selectedDevId, setSelectedDevId] = useState<string | null>(null);
  const [detailLoading, setDetailLoading] = useState<boolean>(false);
  const [detailData, setDetailData] = useState<DeveloperDetailResponse | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

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

  const loadDevelopers = async () => {
    setLoading(true);
    setError(null);
    try {
      const bounds = calculateDateBounds();
      const res = await api.getDevelopers({
        ...bounds,
        search: search.trim() || undefined,
        page,
        page_size: pageSize,
      });
      setData(res);
    } catch (err: any) {
      setError(err?.message || "Failed to load developers directory.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadDevelopers();
  }, [dateRange, page, refreshTrigger]);

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setPage(1);
    loadDevelopers();
  };

  const openDetailModal = async (devId: string) => {
    setSelectedDevId(devId);
    setDetailLoading(true);
    setDetailError(null);
    try {
      const bounds = calculateDateBounds();
      const res = await api.getDeveloperDetail(devId, bounds);
      setDetailData(res);
    } catch (err: any) {
      setDetailError(err?.message || "Failed to fetch developer details.");
    } finally {
      setDetailLoading(false);
    }
  };

  const closeDetailModal = () => {
    setSelectedDevId(null);
    setDetailData(null);
    setDetailError(null);
  };

  return (
    <div style={{ padding: "28px 32px" }}>
      {/* Top Filter Bar */}
      <div
        className="glass-panel"
        style={{
          padding: "16px 20px",
          marginBottom: "24px",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: "16px",
        }}
      >
        <form onSubmit={handleSearchSubmit} style={{ display: "flex", gap: "10px", alignItems: "center", flex: "1 1 300px", maxWidth: "450px" }}>
          <div style={{ position: "relative", width: "100%" }}>
            <Search size={16} style={{ position: "absolute", left: "12px", top: "50%", transform: "translateY(-50%)", color: "var(--text-muted)" }} />
            <input
              type="text"
              className="input-field"
              placeholder="Search by name or email..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              style={{ width: "100%", paddingLeft: "36px" }}
            />
          </div>
          <button type="submit" className="btn btn-secondary">
            Search
          </button>
        </form>

        <div style={{ fontSize: "0.82rem", color: "var(--text-secondary)" }}>
          Showing <strong>{data?.items.length || 0}</strong> of <strong>{data?.total_count || 0}</strong> registered developers
        </div>
      </div>

      {/* Main Table or Loading/Error/Empty */}
      {loading ? (
        <LoadingState message="Loading developers roster and metrics..." />
      ) : error ? (
        <ErrorState message={error} onRetry={loadDevelopers} />
      ) : !data || data.items.length === 0 ? (
        <EmptyState
          title="No Developers Found"
          description={search ? `No developers match '${search}'.` : "No enrolled developers found in this scope."}
          actionText={search ? "Clear Search" : undefined}
          onAction={() => {
            setSearch("");
            setPage(1);
            loadDevelopers();
          }}
        />
      ) : (
        <div className="glass-panel" style={{ padding: "4px" }}>
          <div className="table-container">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Developer</th>
                  <th>Role</th>
                  <th>Teams</th>
                  <th>Total Tokens</th>
                  <th>Input / Cached / Output</th>
                  <th>AI Runs</th>
                  <th>Total Events</th>
                  <th>Last Seen</th>
                  <th>Status</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((dev) => (
                  <tr key={dev.developer_id}>
                    <td>
                      <div style={{ fontWeight: 600 }}>{dev.full_name}</div>
                      <div style={{ fontSize: "0.76rem", color: "var(--text-muted)" }}>{dev.email}</div>
                    </td>
                    <td>
                      <span className="badge badge-role">{dev.role}</span>
                    </td>
                    <td>
                      {dev.teams.length > 0 ? (
                        dev.teams.map((t, idx) => (
                          <span
                            key={idx}
                            style={{
                              fontSize: "0.75rem",
                              background: "rgba(255,255,255,0.06)",
                              padding: "2px 8px",
                              borderRadius: "4px",
                              marginRight: "4px",
                            }}
                          >
                            {t}
                          </span>
                        ))
                      ) : (
                        <span style={{ color: "var(--text-muted)" }}>—</span>
                      )}
                    </td>
                    <td>
                      <span style={{ fontWeight: 700, color: "var(--accent-cyan)", fontFamily: "var(--font-mono)" }}>
                        {formatTokens(dev.total_tokens)}
                      </span>
                    </td>
                    <td style={{ fontSize: "0.78rem", fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>
                      {formatTokens(dev.input_tokens)} / {formatTokens(dev.cached_tokens)} / {formatTokens(dev.output_tokens)}
                    </td>
                    <td>
                      <span style={{ fontWeight: 600, fontFamily: "var(--font-mono)" }}>
                        {formatNumber(dev.ai_run_count)}
                      </span>
                    </td>
                    <td>
                      <span style={{ fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>
                        {formatNumber(dev.event_count)}
                      </span>
                    </td>
                    <td style={{ fontSize: "0.8rem", color: "var(--text-secondary)" }}>
                      {dev.last_seen_at ? formatDate(dev.last_seen_at) : "Never"}
                    </td>
                    <td>
                      <span className={`badge ${dev.is_active ? "badge-active" : "badge-inactive"}`}>
                        {dev.is_active ? "Active" : "Inactive"}
                      </span>
                    </td>
                    <td>
                      <button
                        onClick={() => openDetailModal(dev.developer_id)}
                        className="btn btn-secondary"
                        style={{ padding: "4px 10px", fontSize: "0.75rem" }}
                      >
                        <span>Details</span>
                        <ExternalLink size={12} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination Controls */}
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
      )}

      {/* Developer Detail Modal */}
      {selectedDevId && (
        <div className="modal-overlay" onClick={closeDetailModal}>
          <div className="modal-content" style={{ maxWidth: "780px" }} onClick={(e) => e.stopPropagation()}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "20px" }}>
              <div style={{ display: "flex", gap: "12px", alignItems: "center" }}>
                <div
                  style={{
                    width: "42px",
                    height: "42px",
                    borderRadius: "10px",
                    background: "rgba(6, 182, 212, 0.15)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    color: "var(--accent-cyan)",
                  }}
                >
                  <User size={22} />
                </div>
                <div>
                  <h3 style={{ fontSize: "1.2rem", fontWeight: 700 }}>
                    {detailData?.developer.full_name || "Developer Profile"}
                  </h3>
                  <p style={{ fontSize: "0.82rem", color: "var(--text-secondary)" }}>
                    {detailData?.developer.email} • Role: {detailData?.developer.role}
                  </p>
                </div>
              </div>
              <button onClick={closeDetailModal} style={{ background: "transparent", border: "none", color: "var(--text-muted)", cursor: "pointer" }}>
                <X size={20} />
              </button>
            </div>

            {detailLoading ? (
              <LoadingState message="Fetching developer usage profile..." />
            ) : detailError ? (
              <ErrorState message={detailError} />
            ) : detailData ? (
              <div>
                {/* Metric Strip */}
                <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "12px", marginBottom: "24px" }}>
                  <div style={{ background: "rgba(255, 255, 255, 0.03)", padding: "12px", borderRadius: "var(--radius-md)", border: "1px solid var(--border-subtle)" }}>
                    <div style={{ fontSize: "0.72rem", color: "var(--text-muted)", textTransform: "uppercase" }}>Total Tokens</div>
                    <div style={{ fontSize: "1.2rem", fontWeight: 700, color: "var(--accent-cyan)", fontFamily: "var(--font-mono)" }}>
                      {formatTokens(detailData.developer.total_tokens)}
                    </div>
                  </div>
                  <div style={{ background: "rgba(255, 255, 255, 0.03)", padding: "12px", borderRadius: "var(--radius-md)", border: "1px solid var(--border-subtle)" }}>
                    <div style={{ fontSize: "0.72rem", color: "var(--text-muted)", textTransform: "uppercase" }}>AI Runs</div>
                    <div style={{ fontSize: "1.2rem", fontWeight: 700, fontFamily: "var(--font-mono)" }}>
                      {formatNumber(detailData.developer.ai_run_count)}
                    </div>
                  </div>
                  <div style={{ background: "rgba(255, 255, 255, 0.03)", padding: "12px", borderRadius: "var(--radius-md)", border: "1px solid var(--border-subtle)" }}>
                    <div style={{ fontSize: "0.72rem", color: "var(--text-muted)", textTransform: "uppercase" }}>Workstations</div>
                    <div style={{ fontSize: "1.2rem", fontWeight: 700, fontFamily: "var(--font-mono)" }}>
                      {detailData.enrolled_devices_count}
                    </div>
                  </div>
                  <div style={{ background: "rgba(255, 255, 255, 0.03)", padding: "12px", borderRadius: "var(--radius-md)", border: "1px solid var(--border-subtle)" }}>
                    <div style={{ fontSize: "0.72rem", color: "var(--text-muted)", textTransform: "uppercase" }}>Last Active</div>
                    <div style={{ fontSize: "0.85rem", fontWeight: 600, marginTop: "4px" }}>
                      {detailData.developer.last_seen_at ? formatDate(detailData.developer.last_seen_at) : "None"}
                    </div>
                  </div>
                </div>

                {/* Models Section */}
                <div style={{ marginBottom: "24px" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "10px" }}>
                    <Cpu size={16} color="var(--accent-violet)" />
                    <h4 style={{ fontSize: "0.95rem", fontWeight: 600 }}>AI Models Utilized</h4>
                  </div>
                  {detailData.models.length === 0 ? (
                    <div style={{ fontSize: "0.82rem", color: "var(--text-muted)" }}>No model invocations recorded.</div>
                  ) : (
                    <div style={{ display: "flex", flexWrap: "wrap", gap: "10px" }}>
                      {detailData.models.map((m, idx) => (
                        <div
                          key={idx}
                          style={{
                            background: "rgba(139, 92, 246, 0.08)",
                            border: "1px solid rgba(139, 92, 246, 0.25)",
                            padding: "8px 14px",
                            borderRadius: "var(--radius-md)",
                            fontSize: "0.82rem",
                          }}
                        >
                          <span style={{ fontWeight: 600, fontFamily: "var(--font-mono)", color: "var(--text-primary)" }}>{m.model_name}</span>
                          <span style={{ color: "var(--text-muted)", margin: "0 6px" }}>•</span>
                          <span style={{ color: "var(--accent-cyan)", fontFamily: "var(--font-mono)" }}>{formatTokens(m.total_tokens)}</span> tokens
                          <span style={{ color: "var(--text-muted)", margin: "0 6px" }}>•</span>
                          <span style={{ color: "var(--accent-emerald)" }}>{m.run_count}</span> runs
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                {/* Recent Sessions Section */}
                <div>
                  <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "10px" }}>
                    <Terminal size={16} color="var(--accent-cyan)" />
                    <h4 style={{ fontSize: "0.95rem", fontWeight: 600 }}>Recent AI Coding Sessions</h4>
                  </div>
                  {detailData.recent_sessions.length === 0 ? (
                    <div style={{ fontSize: "0.82rem", color: "var(--text-muted)" }}>No sessions recorded in this period.</div>
                  ) : (
                    <div className="table-container" style={{ border: "1px solid var(--border-subtle)", borderRadius: "var(--radius-md)" }}>
                      <table className="data-table" style={{ fontSize: "0.8rem" }}>
                        <thead>
                          <tr>
                            <th>Session ID</th>
                            <th>Repository</th>
                            <th>Branch</th>
                            <th>Tokens</th>
                            <th>AI Runs</th>
                            <th>Last Activity</th>
                          </tr>
                        </thead>
                        <tbody>
                          {detailData.recent_sessions.map((s, idx) => (
                            <tr key={idx}>
                              <td>
                                <span className="mono-text" title={s.session_id}>
                                  {truncate(s.session_id, 14)}
                                </span>
                              </td>
                              <td>{s.repository_name || "—"}</td>
                              <td>
                                <span style={{ background: "rgba(255,255,255,0.06)", padding: "2px 6px", borderRadius: "4px" }}>
                                  {s.branch || "—"}
                                </span>
                              </td>
                              <td style={{ fontFamily: "var(--font-mono)", color: "var(--accent-cyan)", fontWeight: 600 }}>
                                {formatTokens(s.total_tokens)}
                              </td>
                              <td>{s.ai_run_count}</td>
                              <td style={{ color: "var(--text-secondary)" }}>
                                {s.last_activity_at ? formatDateTime(s.last_activity_at) : "—"}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              </div>
            ) : null}
          </div>
        </div>
      )}
    </div>
  );
};
