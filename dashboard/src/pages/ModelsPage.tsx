import React, { useEffect, useState } from "react";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  CartesianGrid,
} from "recharts";
import { api } from "../api/client";
import type { DateRangePreset, ModelAnalyticsSummary } from "../types/analytics";
import { LoadingState } from "../components/LoadingState";
import { ErrorState } from "../components/ErrorState";
import { EmptyState } from "../components/EmptyState";
import { formatNumber, formatTokens, formatDateTime } from "../utils/format";

interface ModelsPageProps {
  dateRange: DateRangePreset;
  refreshTrigger: number;
}

export const ModelsPage: React.FC<ModelsPageProps> = ({ dateRange, refreshTrigger }) => {
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [models, setModels] = useState<ModelAnalyticsSummary[]>([]);

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

  const loadModels = async () => {
    setLoading(true);
    setError(null);
    try {
      const bounds = calculateDateBounds();
      const res = await api.getModels(bounds);
      setModels(res.items || []);
    } catch (err: any) {
      setError(err?.message || "Failed to load model analytics.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadModels();
  }, [dateRange, refreshTrigger]);

  if (loading) return <LoadingState message="Aggregating model tokens and caching statistics..." />;
  if (error) return <ErrorState message={error} onRetry={loadModels} />;
  if (models.length === 0) {
    return (
      <EmptyState
        title="No Model Invocations"
        description="No model events have been recorded in this time range. Events will show as models are queried."
      />
    );
  }

  return (
    <div style={{ padding: "28px 32px" }}>
      {/* Chart Section */}
      <div className="glass-panel" style={{ padding: "24px", marginBottom: "28px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "20px" }}>
          <div>
            <h2 style={{ fontSize: "1.05rem", fontWeight: 700 }}>Token Consumption Breakdown by Model</h2>
            <p style={{ fontSize: "0.8rem", color: "var(--text-muted)" }}>
              Comparing Uncached Input, Cached Input Read, and Output tokens
            </p>
          </div>
        </div>

        <div style={{ height: "320px", width: "100%" }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={models} margin={{ top: 10, right: 20, left: 0, bottom: 20 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255, 255, 255, 0.05)" />
              <XAxis dataKey="model_name" stroke="#64748b" fontSize={11} tick={{ fill: "#94a3b8" }} />
              <YAxis stroke="#64748b" fontSize={11} tickFormatter={formatTokens} />
              <Tooltip
                content={({ active, payload, label }) => {
                  if (active && payload && payload.length) {
                    return (
                      <div className="recharts-custom-tooltip">
                        <p style={{ fontWeight: 700, marginBottom: "4px" }}>{label}</p>
                        {payload.map((entry, idx) => (
                          <p key={idx} style={{ color: entry.color }}>
                            {entry.name}: {formatNumber(Number(entry.value))}
                          </p>
                        ))}
                      </div>
                    );
                  }
                  return null;
                }}
              />
              <Legend wrapperStyle={{ fontSize: "12px", paddingTop: "12px" }} />
              <Bar dataKey="input_tokens" name="Uncached Input" fill="#06b6d4" radius={[4, 4, 0, 0]} />
              <Bar dataKey="cached_input_tokens" name="Cached Input" fill="#10b981" radius={[4, 4, 0, 0]} />
              <Bar dataKey="output_tokens" name="Output Tokens" fill="#8b5cf6" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Model Analytics Table */}
      <div className="glass-panel" style={{ padding: "20px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
          <div>
            <h2 style={{ fontSize: "1rem", fontWeight: 700 }}>Model Specifications & Metrics</h2>
            <p style={{ fontSize: "0.78rem", color: "var(--text-muted)" }}>
              Total events, run counts, and telemetry coverage across active AI models
            </p>
          </div>
        </div>

        <div className="table-container">
          <table className="data-table">
            <thead>
              <tr>
                <th>Model Identifier</th>
                <th>AI Runs</th>
                <th>Total Invocations</th>
                <th>Total Tokens</th>
                <th>Input Tokens</th>
                <th>Cached Read</th>
                <th>Output Tokens</th>
                <th>Thinking Tokens</th>
                <th>Estimated Cost</th>
                <th>Last Active</th>
              </tr>
            </thead>
            <tbody>
              {models.map((m) => (
                <tr key={m.model_name}>
                  <td>
                    <span className="badge badge-model">{m.model_name}</span>
                  </td>
                  <td>
                    <span style={{ fontWeight: 600, fontFamily: "var(--font-mono)" }}>
                      {formatNumber(m.ai_run_count)}
                    </span>
                  </td>
                  <td>
                    <span style={{ fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>
                      {formatNumber(m.total_events)}
                    </span>
                  </td>
                  <td>
                    <span style={{ fontWeight: 700, color: "var(--accent-cyan)", fontFamily: "var(--font-mono)" }}>
                      {formatTokens(m.total_tokens)}
                    </span>
                  </td>
                  <td style={{ fontFamily: "var(--font-mono)" }}>{formatTokens(m.input_tokens)}</td>
                  <td style={{ fontFamily: "var(--font-mono)", color: "var(--accent-emerald)" }}>
                    {formatTokens(m.cached_input_tokens)}
                  </td>
                  <td style={{ fontFamily: "var(--font-mono)", color: "var(--accent-violet)" }}>
                    {formatTokens(m.output_tokens)}
                  </td>
                  <td>
                    {m.thinking_tokens !== null && m.thinking_tokens !== undefined ? (
                      <span style={{ fontFamily: "var(--font-mono)" }}>{formatTokens(m.thinking_tokens)}</span>
                    ) : (
                      <span style={{ color: "var(--text-muted)", fontSize: "0.76rem" }} title="Not emitted by model provider">
                        Unavailable
                      </span>
                    )}
                  </td>
                  <td>
                    {m.cost_usd !== null && m.cost_usd !== undefined ? (
                      <span style={{ fontFamily: "var(--font-mono)", color: "var(--accent-amber)" }}>
                        ${Number(m.cost_usd).toFixed(4)}
                      </span>
                    ) : (
                      <span style={{ color: "var(--text-muted)", fontSize: "0.76rem" }} title="Authoritative billing connector not configured">
                        Unavailable
                      </span>
                    )}
                  </td>
                  <td style={{ fontSize: "0.8rem", color: "var(--text-secondary)" }}>
                    {m.last_used_at ? formatDateTime(m.last_used_at) : "—"}
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
