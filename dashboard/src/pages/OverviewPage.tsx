import React, { useEffect, useState } from "react";
import {
  Users,
  Laptop,
  Sparkles,
  Wrench,
  Terminal,
  Coins,
} from "lucide-react";
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  CartesianGrid,
  BarChart,
  Bar,
  Cell,
} from "recharts";
import { api } from "../api/client";
import type {
  AnalyticsOverviewResponse,
  DateRangePreset,
  DeveloperSummary,
  ModelAnalyticsSummary,
  TimeseriesBucket,
} from "../types/analytics";
import { KPICard } from "../components/KPICard";
import { LoadingState } from "../components/LoadingState";
import { ErrorState } from "../components/ErrorState";
import { EmptyState } from "../components/EmptyState";
import { formatNumber, formatTokens, formatDate } from "../utils/format";

interface OverviewPageProps {
  dateRange: DateRangePreset;
  refreshTrigger: number;
  onNavigateToDevelopers: () => void;
  onNavigateToModels: () => void;
}

export const OverviewPage: React.FC<OverviewPageProps> = ({
  dateRange,
  refreshTrigger,
  onNavigateToDevelopers,
  onNavigateToModels,
}) => {
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [overview, setOverview] = useState<AnalyticsOverviewResponse | null>(null);
  const [timeseries, setTimeseries] = useState<TimeseriesBucket[]>([]);
  const [models, setModels] = useState<ModelAnalyticsSummary[]>([]);
  const [topDevs, setTopDevs] = useState<DeveloperSummary[]>([]);

  const calculateDateBounds = (preset: DateRangePreset): { start_date?: string; end_date?: string } => {
    const end = new Date();
    const start = new Date();
    if (preset === "7d") start.setDate(end.getDate() - 7);
    else if (preset === "30d") start.setDate(end.getDate() - 30);
    else if (preset === "90d") start.setDate(end.getDate() - 90);
    else return {};

    return {
      start_date: start.toISOString(),
      end_date: end.toISOString(),
    };
  };

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      const bounds = calculateDateBounds(dateRange);
      const [overviewData, timeseriesData, modelsData, devsData] = await Promise.all([
        api.getOverview(bounds),
        api.getTimeseries(bounds),
        api.getModels(bounds),
        api.getDevelopers({ ...bounds, page: 1, page_size: 5 }),
      ]);

      setOverview(overviewData);
      setTimeseries(timeseriesData.buckets || []);
      setModels(modelsData.items || []);
      setTopDevs(devsData.items || []);
    } catch (err: any) {
      setError(err?.message || "Failed to load telemetry overview.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, [dateRange, refreshTrigger]);

  if (loading) return <LoadingState message="Aggregating telemetry KPIs and usage trends..." />;
  if (error) return <ErrorState message={error} onRetry={loadData} />;
  if (!overview || overview.total_events === 0) {
    return (
      <EmptyState
        title="Zero Telemetry Events Ingested"
        description="The telemetry database currently has no records for the selected time window. Once workstations send event batches, metrics will appear here."
      />
    );
  }

  const modelColors = ["#06b6d4", "#8b5cf6", "#10b981", "#f59e0b", "#ec4899", "#6366f1"];

  return (
    <div style={{ padding: "28px 32px" }}>
      {/* KPI Cards Row */}
      <div className="kpi-grid">
        <KPICard
          title="Developers"
          value={formatNumber(overview.total_developers)}
          subtitle={`${overview.active_developers} active with recent telemetry`}
          icon={<Users size={20} />}
          accentColor="cyan"
        />

        <KPICard
          title="Enrolled Devices"
          value={formatNumber(overview.enrolled_devices)}
          subtitle={`${overview.active_devices} reporting workstations`}
          icon={<Laptop size={20} />}
          accentColor="violet"
        />

        <KPICard
          title="AI Runs"
          value={formatNumber(overview.total_ai_runs)}
          subtitle={`Across ${formatNumber(overview.total_events)} total events`}
          icon={<Sparkles size={20} />}
          accentColor="emerald"
        />

        <KPICard
          title="Tool Calls"
          value={formatNumber(overview.total_tool_calls)}
          subtitle="Agent tool & terminal executions"
          icon={<Wrench size={20} />}
          accentColor="amber"
        />

        <KPICard
          title="Total Tokens"
          value={formatTokens(overview.tokens.total_tokens)}
          subtitle="All input, cached, and output tokens"
          icon={<Coins size={20} />}
          accentColor="cyan"
          breakdown={[
            { label: "Input", value: formatTokens(overview.tokens.input_tokens) },
            { label: "Cached", value: formatTokens(overview.tokens.cached_input_tokens) },
            { label: "Output", value: formatTokens(overview.tokens.output_tokens) },
          ]}
        />

        <KPICard
          title="Unique Sessions"
          value={formatNumber(overview.total_sessions)}
          subtitle="Active IDE coding sessions"
          icon={<Terminal size={20} />}
          accentColor="violet"
        />
      </div>

      {/* Main Charts Row */}
      <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: "20px", marginBottom: "28px" }}>
        {/* Time-series Chart */}
        <div className="glass-panel" style={{ padding: "22px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "18px" }}>
            <div>
              <h2 style={{ fontSize: "1rem", fontWeight: 700 }}>Daily Token Consumption</h2>
              <p style={{ fontSize: "0.78rem", color: "var(--text-muted)" }}>
                Input, Cached Input, and Output token trends over time
              </p>
            </div>
          </div>

          <div style={{ height: "280px", width: "100%" }}>
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={timeseries} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                <defs>
                  <linearGradient id="colorInput" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#06b6d4" stopOpacity={0.4} />
                    <stop offset="95%" stopColor="#06b6d4" stopOpacity={0} />
                  </linearGradient>
                  <linearGradient id="colorOutput" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#8b5cf6" stopOpacity={0.4} />
                    <stop offset="95%" stopColor="#8b5cf6" stopOpacity={0} />
                  </linearGradient>
                  <linearGradient id="colorCached" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#10b981" stopOpacity={0.4} />
                    <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255, 255, 255, 0.05)" />
                <XAxis dataKey="timestamp" stroke="#64748b" fontSize={11} tickFormatter={(val) => val.slice(5)} />
                <YAxis stroke="#64748b" fontSize={11} tickFormatter={formatTokens} />
                <Tooltip
                  content={({ active, payload, label }) => {
                    if (active && payload && payload.length) {
                      return (
                        <div className="recharts-custom-tooltip">
                          <p style={{ fontWeight: 600, marginBottom: "4px" }}>Date: {label}</p>
                          {payload.map((entry, index) => (
                            <p key={index} style={{ color: entry.color }}>
                              {entry.name}: {formatTokens(Number(entry.value))}
                            </p>
                          ))}
                        </div>
                      );
                    }
                    return null;
                  }}
                />
                <Legend iconType="circle" wrapperStyle={{ fontSize: "12px", paddingTop: "10px" }} />
                <Area type="monotone" dataKey="input_tokens" name="Input Tokens" stroke="#06b6d4" fillOpacity={1} fill="url(#colorInput)" />
                <Area type="monotone" dataKey="output_tokens" name="Output Tokens" stroke="#8b5cf6" fillOpacity={1} fill="url(#colorOutput)" />
                <Area type="monotone" dataKey="cached_input_tokens" name="Cached Read" stroke="#10b981" fillOpacity={1} fill="url(#colorCached)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Model Distribution Chart */}
        <div className="glass-panel" style={{ padding: "22px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "18px" }}>
            <div>
              <h2 style={{ fontSize: "1rem", fontWeight: 700 }}>Model Distribution</h2>
              <p style={{ fontSize: "0.78rem", color: "var(--text-muted)" }}>
                Total token volume by AI model
              </p>
            </div>
            <button
              onClick={onNavigateToModels}
              style={{ background: "transparent", border: "none", color: "var(--accent-cyan)", fontSize: "0.78rem", cursor: "pointer", fontWeight: 600 }}
            >
              View All
            </button>
          </div>

          <div style={{ height: "280px", width: "100%" }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={models.slice(0, 5)} layout="vertical" margin={{ top: 5, right: 20, left: 20, bottom: 5 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255, 255, 255, 0.05)" horizontal={false} />
                <XAxis type="number" stroke="#64748b" fontSize={11} tickFormatter={formatTokens} />
                <YAxis dataKey="model_name" type="category" stroke="#64748b" fontSize={10} width={100} tickFormatter={(val) => val.length > 14 ? val.slice(0, 12) + ".." : val} />
                <Tooltip
                  content={({ active, payload }) => {
                    if (active && payload && payload.length) {
                      const item = payload[0].payload as ModelAnalyticsSummary;
                      return (
                        <div className="recharts-custom-tooltip">
                          <p style={{ fontWeight: 700 }}>{item.model_name}</p>
                          <p style={{ color: "var(--accent-cyan)" }}>Tokens: {formatNumber(item.total_tokens)}</p>
                          <p style={{ color: "var(--accent-emerald)" }}>AI Runs: {formatNumber(item.ai_run_count)}</p>
                        </div>
                      );
                    }
                    return null;
                  }}
                />
                <Bar dataKey="total_tokens" radius={[0, 4, 4, 0]}>
                  {models.slice(0, 5).map((_, index) => (
                    <Cell key={`cell-${index}`} fill={modelColors[index % modelColors.length]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      {/* Top Developers Table */}
      <div className="glass-panel" style={{ padding: "22px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
          <div>
            <h2 style={{ fontSize: "1rem", fontWeight: 700 }}>Top Active Developers</h2>
            <p style={{ fontSize: "0.78rem", color: "var(--text-muted)" }}>
              Developers ranked by token consumption and AI interaction frequency
            </p>
          </div>
          <button
            onClick={onNavigateToDevelopers}
            className="btn btn-secondary"
            style={{ fontSize: "0.8rem", padding: "6px 12px" }}
          >
            <span>View All Developers</span>
          </button>
        </div>

        <div className="table-container">
          <table className="data-table">
            <thead>
              <tr>
                <th>Developer</th>
                <th>Role</th>
                <th>Teams</th>
                <th>AI Runs</th>
                <th>Total Tokens</th>
                <th>Input / Cached / Output</th>
                <th>Last Active</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {topDevs.map((dev) => (
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
                        <span key={idx} style={{ fontSize: "0.78rem", background: "rgba(255,255,255,0.06)", padding: "2px 8px", borderRadius: "4px", marginRight: "4px" }}>
                          {t}
                        </span>
                      ))
                    ) : (
                      <span style={{ color: "var(--text-muted)" }}>—</span>
                    )}
                  </td>
                  <td>
                    <span style={{ fontWeight: 600, fontFamily: "var(--font-mono)" }}>
                      {formatNumber(dev.ai_run_count)}
                    </span>
                  </td>
                  <td>
                    <span style={{ fontWeight: 700, color: "var(--accent-cyan)", fontFamily: "var(--font-mono)" }}>
                      {formatTokens(dev.total_tokens)}
                    </span>
                  </td>
                  <td style={{ fontSize: "0.78rem", fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>
                    {formatTokens(dev.input_tokens)} / {formatTokens(dev.cached_tokens)} / {formatTokens(dev.output_tokens)}
                  </td>
                  <td style={{ fontSize: "0.8rem", color: "var(--text-secondary)" }}>
                    {dev.last_seen_at ? formatDate(dev.last_seen_at) : "Never"}
                  </td>
                  <td>
                    <span className={`badge ${dev.is_active ? "badge-active" : "badge-inactive"}`}>
                      {dev.is_active ? "Active" : "Inactive"}
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
