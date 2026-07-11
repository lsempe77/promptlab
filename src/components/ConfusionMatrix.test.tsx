import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ConfusionMatrix } from "./ConfusionMatrix";
import type { Confusion } from "../api";

// --------------------------------------------------------------------------- //
// Null / loading state
// --------------------------------------------------------------------------- //

describe("ConfusionMatrix", () => {
  it("shows Loading when confusion is null", () => {
    render(<ConfusionMatrix confusion={null} />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  // ----------------------------------------------------------------------- //
  // List confusion
  // ----------------------------------------------------------------------- //

  const listConfusion: Confusion = {
    type: "list",
    tp: 10,
    fp: 2,
    fn: 3,
    precision: 10 / 12,
    recall: 10 / 13,
    sensitivity: 10 / 13,
    specificity: null,
    f1: 0.8,
    f2: 0.769,
    n: 13,
  };

  it("renders TP/FP/FN counts for list type", () => {
    render(<ConfusionMatrix confusion={listConfusion} />);
    expect(screen.getByText("10")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("renders precision and recall as percentages", () => {
    const { container } = render(<ConfusionMatrix confusion={listConfusion} />);
    const statValues = container.querySelectorAll(".stat-value");
    // precision = 10/12 ≈ 83.3%
    expect(statValues[3].textContent).toContain("83.3%");
    // sensitivity (recall) = 10/13 ≈ 76.9%
    expect(statValues[4].textContent).toContain("76.9%");
  });

  it("shows n/a for specificity when null", () => {
    render(<ConfusionMatrix confusion={listConfusion} />);
    expect(screen.getByText("n/a")).toBeInTheDocument();
  });

  it("shows specificity as percentage when not null", () => {
    const conf: Confusion = { ...listConfusion, specificity: 0.95 };
    render(<ConfusionMatrix confusion={conf} />);
    expect(screen.getByText("95.0%")).toBeInTheDocument();
  });

  it("renders F1 and F2 values", () => {
    const { container } = render(<ConfusionMatrix confusion={listConfusion} />);
    const statValues = container.querySelectorAll(".stat-value");
    expect(statValues[6].textContent).toBe("0.800");
    expect(statValues[7].textContent).toBe("0.769");
  });

  it("shows open-vocabulary note when specificity is null", () => {
    render(<ConfusionMatrix confusion={listConfusion} />);
    expect(screen.getByText(/open-vocabulary field/)).toBeInTheDocument();
  });

  // ----------------------------------------------------------------------- //
  // Categorical confusion
  // ----------------------------------------------------------------------- //

  const catConfusion: Confusion = {
    type: "categorical",
    truth_labels: ["health", "education"],
    pred_labels: ["health", "education", "(other)", "(none)"],
    matrix: [[5, 1, 0, 0], [0, 3, 0, 1]],
    accuracy: 8 / 10,
    kappa: 0.75,
    sensitivity: 0.85,
    specificity: 0.90,
    f2: 0.82,
    n: 10,
  };

  it("renders accuracy percentage for categorical", () => {
    render(<ConfusionMatrix confusion={catConfusion} />);
    expect(screen.getByText("80.0%")).toBeInTheDocument();
  });

  it("renders kappa value", () => {
    render(<ConfusionMatrix confusion={catConfusion} />);
    expect(screen.getByText("0.750")).toBeInTheDocument();
  });

  it("renders dash for null kappa", () => {
    const conf: Confusion = { ...catConfusion, kappa: null };
    render(<ConfusionMatrix confusion={conf} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("renders confusion table with truth labels as rows", () => {
    const { container } = render(<ConfusionMatrix confusion={catConfusion} />);
    const rows = container.querySelectorAll(".confusion-table tbody tr");
    expect(rows).toHaveLength(2);
    expect(rows[0].querySelector("th")?.textContent).toBe("health");
    expect(rows[1].querySelector("th")?.textContent).toBe("education");
  });

  it("renders confusion table with pred labels as columns", () => {
    const { container } = render(<ConfusionMatrix confusion={catConfusion} />);
    const headers = container.querySelectorAll(".confusion-table thead th");
    // first th is empty (corner), then the 4 pred labels
    expect(headers).toHaveLength(5); // 1 corner + 4 labels
    expect(headers[1].textContent).toBe("health");
    expect(headers[2].textContent).toBe("education");
  });

  it("renders cell counts", () => {
    const { container } = render(<ConfusionMatrix confusion={catConfusion} />);
    const cells = container.querySelectorAll(".confusion-table tbody td");
    // 2 rows x 4 columns = 8 cells
    expect(cells).toHaveLength(8);
    expect(cells[0].textContent).toBe("5"); // health-health
    expect(cells[1].textContent).toBe("1"); // health-education
  });

  it("renders empty string for zero cells", () => {
    const { container } = render(<ConfusionMatrix confusion={catConfusion} />);
    const cells = container.querySelectorAll(".confusion-table tbody td");
    expect(cells[2].textContent).toBe(""); // health-other = 0
  });

  it("shows 'No references' message when n === 0", () => {
    const conf: Confusion = { ...catConfusion, n: 0 };
    render(<ConfusionMatrix confusion={conf} />);
    expect(screen.getByText(/No references processed yet/)).toBeInTheDocument();
  });
});
