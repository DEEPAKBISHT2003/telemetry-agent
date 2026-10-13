import React, { useState } from "react";
import { Key, ShieldAlert, X, Check } from "lucide-react";
import { getStoredAuth, setStoredAuth, clearStoredAuth } from "../api/client";

interface AuthModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSave: () => void;
}

export const AuthModal: React.FC<AuthModalProps> = ({ isOpen, onClose, onSave }) => {
  const [tokenInput, setTokenInput] = useState(getStoredAuth());
  const [copiedDevKey, setCopiedDevKey] = useState(false);

  if (!isOpen) return null;

  const handleSave = (e: React.FormEvent) => {
    e.preventDefault();
    setStoredAuth(tokenInput);
    onSave();
    onClose();
  };

  const handleClear = () => {
    clearStoredAuth();
    setTokenInput("");
    onSave();
    onClose();
  };

  const fillDefaultDevAdminKey = () => {
    const key = "test_bootstrap_key_123";
    setTokenInput(key);
    setCopiedDevKey(true);
    setTimeout(() => setCopiedDevKey(false), 2000);
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "18px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <div
              style={{
                width: "32px",
                height: "32px",
                borderRadius: "8px",
                background: "rgba(6, 182, 212, 0.15)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                color: "var(--accent-cyan)",
              }}
            >
              <Key size={18} />
            </div>
            <div>
              <h3 style={{ fontSize: "1.1rem", fontWeight: 700 }}>API Authentication</h3>
              <p style={{ fontSize: "0.78rem", color: "var(--text-muted)" }}>
                Configure credentials for analytics API access
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            style={{
              background: "transparent",
              border: "none",
              color: "var(--text-muted)",
              cursor: "pointer",
            }}
          >
            <X size={20} />
          </button>
        </div>

        <div
          style={{
            background: "rgba(139, 92, 246, 0.08)",
            border: "1px solid rgba(139, 92, 246, 0.2)",
            borderRadius: "var(--radius-md)",
            padding: "12px 16px",
            marginBottom: "20px",
            fontSize: "0.82rem",
            color: "#e2e8f0",
            display: "flex",
            alignItems: "flex-start",
            gap: "10px",
          }}
        >
          <ShieldAlert size={18} style={{ color: "var(--accent-violet)", flexShrink: 0, marginTop: "2px" }} />
          <div>
            <strong>Role-Scoped Access Control:</strong>
            <ul style={{ marginLeft: "18px", marginTop: "4px", lineHeight: "1.5" }}>
              <li><strong>Bootstrap Admin Key:</strong> Full global visibility across all tenants.</li>
              <li><strong>Device Token (<code className="mono-text">dvt_...</code>):</strong> Scoped by developer role (<code className="mono-text">ORG_ADMIN</code>, <code className="mono-text">TEAM_LEAD</code>, <code className="mono-text">DEVELOPER</code>, <code className="mono-text">AUDITOR</code>).</li>
            </ul>
          </div>
        </div>

        <form onSubmit={handleSave}>
          <div style={{ marginBottom: "16px" }}>
            <label style={{ display: "block", fontSize: "0.82rem", fontWeight: 600, marginBottom: "6px" }}>
              Authentication Credential (Admin Key or Bearer Token)
            </label>
            <input
              type="password"
              className="input-field"
              value={tokenInput}
              onChange={(e) => setTokenInput(e.target.value)}
              placeholder="e.g. test_bootstrap_key_123 or dvt_..."
              style={{ width: "100%", fontFamily: "var(--font-mono)" }}
            />
          </div>

          <div style={{ display: "flex", gap: "10px", marginBottom: "24px" }}>
            <button
              type="button"
              onClick={fillDefaultDevAdminKey}
              className="btn btn-secondary"
              style={{ fontSize: "0.78rem" }}
            >
              {copiedDevKey ? <Check size={14} color="var(--accent-emerald)" /> : null}
              <span>Use Test Bootstrap Admin Key</span>
            </button>
            {tokenInput && (
              <button
                type="button"
                onClick={handleClear}
                className="btn btn-secondary"
                style={{ fontSize: "0.78rem", color: "var(--accent-rose)" }}
              >
                Clear
              </button>
            )}
          </div>

          <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px" }}>
            <button type="button" onClick={onClose} className="btn btn-secondary">
              Cancel
            </button>
            <button type="submit" className="btn btn-primary">
              Save Credential
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
