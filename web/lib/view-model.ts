export type ResultStatusInput = {
  matched: boolean;
  exception_reason: string | null;
};

export function formatCurrency(value: number): string {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    minimumFractionDigits: 2,
  }).format(value);
}

export function formatPercent(value: number): string {
  return new Intl.NumberFormat("en-IN", {
    style: "percent",
    maximumFractionDigits: 1,
  }).format(value);
}

export function resultStatus(result: ResultStatusInput): "Matched" | "Exception" | "Pending" {
  if (result.matched) return "Matched";
  if (result.exception_reason) return "Exception";
  return "Pending";
}

export function readableLabel(value: string): string {
  return value.replaceAll("_", " ").toLowerCase().replace(/^./, (letter) => letter.toUpperCase());
}
