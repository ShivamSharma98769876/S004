"use client";

import { useMemo, useState } from "react";
import AppFrame from "@/components/AppFrame";

const RULE_ROWS = [
  { rule: "Daily Drawdown", threshold: "INR 15,000", current: "INR 4,380", status: "PASS" },
  { rule: "Per Trade Risk", threshold: "INR 3,000", current: "INR 1,420", status: "PASS" },
  { rule: "Exposure Utilization", threshold: "75%", current: "61%", status: "PASS" },
];

type RuleCol = "rule" | "threshold" | "current" | "status";

export default function RiskPage() {
  const [sortCol, setSortCol] = useState<RuleCol>("rule");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");

  const sortedRows = useMemo(() => {
    const arr = [...RULE_ROWS];
    const mul = sortDir === "asc" ? 1 : -1;
    arr.sort((a, b) => {
      const va = a[sortCol];
      const vb = b[sortCol];
      return String(va).localeCompare(String(vb), undefined, { numeric: true }) * mul;
    });
    return arr;
  }, [sortCol, sortDir]);

  const handleSort = (col: RuleCol) => {
    if (sortCol === col) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortCol(col);
      setSortDir("asc");
    }
  };

  return (
    <AppFrame title="Risk Management" subtitle="Monitor portfolio drawdown, limits, and runtime risk gates.">
      <section className="summary-grid">
        <div className="summary-card">
          <div className="summary-label">Daily Loss Limit</div>
          <div className="summary-value">INR 15,000</div>
        </div>
        <div className="summary-card">
          <div className="summary-label">Current Drawdown</div>
          <div className="summary-value">INR 4,380</div>
        </div>
        <div className="summary-card">
          <div className="summary-label">Max Open Positions</div>
          <div className="summary-value">5</div>
        </div>
        <div className="summary-card">
          <div className="summary-label">Risk Gate Status</div>
          <div className="summary-value">PASS</div>
        </div>
      </section>

      <section className="table-card">
        <div className="panel-title">Rule Status</div>
        <div className="table-wrap">
          <table className="market-table">
            <thead>
              <tr>
                <th className="sortable-th" onClick={() => handleSort("rule")}>Rule {sortCol === "rule" ? (sortDir === "asc" ? "↑" : "↓") : ""}</th>
                <th className="sortable-th" onClick={() => handleSort("threshold")}>Threshold {sortCol === "threshold" ? (sortDir === "asc" ? "↑" : "↓") : ""}</th>
                <th className="sortable-th" onClick={() => handleSort("current")}>Current {sortCol === "current" ? (sortDir === "asc" ? "↑" : "↓") : ""}</th>
                <th className="sortable-th" onClick={() => handleSort("status")}>Status {sortCol === "status" ? (sortDir === "asc" ? "↑" : "↓") : ""}</th>
              </tr>
            </thead>
            <tbody>
              {sortedRows.map((row) => (
                <tr key={row.rule}>
                  <td>{row.rule}</td>
                  <td>{row.threshold}</td>
                  <td>{row.current}</td>
                  <td>
                    <span className="chip chip-status-active">{row.status}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </AppFrame>
  );
}
