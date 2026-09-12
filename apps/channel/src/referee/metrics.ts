import { readFile } from "node:fs/promises";
import type { ReportMetrics } from "./types";

const HEADERS = ["period", "revenue_aed", "closed_deals", "open_tickets"] as const;

function percent(from: number, change: number): number | null {
  return from === 0 ? null : (change / from) * 100;
}

export function parseMetricsCsv(csv: string): ReportMetrics {
  const rows = csv.trim().split(/\r?\n/).map((row) => row.split(",").map((cell) => cell.trim()));
  if (rows.length !== 3 || rows[0]?.join(",") !== HEADERS.join(",")) throw new Error("Report input must contain the supported metrics CSV headers and exactly two rows.");
  const [from, to] = rows.slice(1).map((row) => {
    if (row.length !== HEADERS.length || !/^\d{4}-\d{2}$/.test(row[0] ?? "")) throw new Error("Report input has an invalid period or column count.");
    const numbers = row.slice(1).map(Number);
    if (numbers.some((item) => !Number.isFinite(item))) throw new Error("Report input contains a non-finite metric.");
    return { period: row[0] as string, revenue: numbers[0] as number, deals: numbers[1] as number, tickets: numbers[2] as number };
  });
  if (!from || !to || from.period >= to.period) throw new Error("Report periods must be ordered and distinct.");
  const revenueChange = to.revenue - from.revenue;
  const dealsChange = to.deals - from.deals;
  const ticketsChange = to.tickets - from.tickets;
  return {
    fromPeriod: from.period, toPeriod: to.period,
    revenueAed: { from: from.revenue, to: to.revenue, change: revenueChange, percent: percent(from.revenue, revenueChange) },
    closedDeals: { from: from.deals, to: to.deals, change: dealsChange, percent: percent(from.deals, dealsChange) },
    openTickets: { from: from.tickets, to: to.tickets, change: ticketsChange, percent: percent(from.tickets, ticketsChange) },
  };
}

export async function readMetricsFile(filePath: string): Promise<ReportMetrics> {
  return parseMetricsCsv(await readFile(filePath, "utf8"));
}

export function formatPercent(value: number | null): string {
  return value === null ? "n/a" : `${value.toFixed(0)}%`;
}

export function renderMetricsTable(metrics: ReportMetrics): string {
  return [
    "| Metric | From | To | Change | % |",
    "| --- | ---: | ---: | ---: | ---: |",
    `| Revenue (AED) | ${metrics.revenueAed.from.toLocaleString("en-US")} | ${metrics.revenueAed.to.toLocaleString("en-US")} | ${metrics.revenueAed.change.toLocaleString("en-US")} | ${formatPercent(metrics.revenueAed.percent)} |`,
    `| Closed deals | ${metrics.closedDeals.from} | ${metrics.closedDeals.to} | ${metrics.closedDeals.change} | ${formatPercent(metrics.closedDeals.percent)} |`,
    `| Open tickets | ${metrics.openTickets.from} | ${metrics.openTickets.to} | ${metrics.openTickets.change} | ${formatPercent(metrics.openTickets.percent)} |`,
  ].join("\n");
}
