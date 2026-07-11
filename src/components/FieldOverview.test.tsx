import { describe, expect, it, vi, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { FieldOverview } from "./FieldOverview";
import type { FieldInfo, StageStatus } from "../api";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      stageStatus: vi.fn(),
    },
  };
});

import { api } from "../api";

const mockStageStatus = vi.mocked(api.stageStatus);

const fields: FieldInfo[] = [
  { name: "sector_name", label: "Sector", value_type: "single_categorical", taxonomy_key: "sectors", description: "" },
  { name: "authors", label: "Author names", value_type: "list_text", taxonomy_key: null, description: "" },
];

function makeStageStatus(overrides: Partial<StageStatus> = {}): StageStatus {
  return {
    references: 100,
    stages: [100],
    stage_target: 100,
    final_stage: 100,
    gate_threshold: 0.9,
    models: [],
    n_models_evaluated: 0,
    n_models_judged: 0,
    n_models_passing: 0,
    n_needs_review: 0,
    n_judged: 0,
    prompt_versions: 0,
    prompt_versions_accepted: 0,
    ...overrides,
  };
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("FieldOverview", () => {
  it("renders nothing when fields list is empty", () => {
    const { container } = render(
      <FieldOverview project="dep" fields={[]} selectedField={null} onSelectField={vi.fn()} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders field labels", async () => {
    mockStageStatus.mockResolvedValue(makeStageStatus());
    render(
      <FieldOverview project="dep" fields={fields} selectedField={null} onSelectField={vi.fn()} />,
    );
    await waitFor(() => {
      expect(screen.getByText("Sector")).toBeInTheDocument();
      expect(screen.getByText("Author names")).toBeInTheDocument();
    });
  });

  it("shows gate percentage in subtitle", async () => {
    mockStageStatus.mockResolvedValue(makeStageStatus());
    render(
      <FieldOverview project="dep" fields={fields} selectedField={null} onSelectField={vi.fn()} />,
    );
    await waitFor(() => {
      expect(screen.getByText(/90%/)).toBeInTheDocument();
    });
  });

  it("shows 'No data yet' when stage status has no models", async () => {
    mockStageStatus.mockResolvedValue(makeStageStatus({ models: [] }));
    render(
      <FieldOverview project="dep" fields={fields} selectedField={null} onSelectField={vi.fn()} />,
    );
    await waitFor(() => {
      expect(screen.getAllByText(/No data yet/)).toHaveLength(2);
    });
  });

  it("shows 'Good enough to use' when a model passes the gate", async () => {
    mockStageStatus.mockResolvedValue(
      makeStageStatus({
        n_models_passing: 1,
        n_models_evaluated: 2,
        models: [
          { model_id: "a", gate_metric_name: "accuracy", gate_metric: 0.95, precision: null, recall: null, f1: null, accuracy: 0.95, kappa: null, n: 100, llm_judged_accuracy: null, n_judged: 0, gate_passed: true },
          { model_id: "b", gate_metric_name: "accuracy", gate_metric: 0.80, precision: null, recall: null, f1: null, accuracy: 0.80, kappa: null, n: 100, llm_judged_accuracy: null, n_judged: 0, gate_passed: false },
        ],
      }),
    );
    render(
      <FieldOverview project="dep" fields={fields} selectedField={null} onSelectField={vi.fn()} />,
    );
    await waitFor(() => {
      expect(screen.getAllByText(/Good enough to use/)).toHaveLength(2);
    });
  });

  it("calls onSelectField when a row is clicked", async () => {
    mockStageStatus.mockResolvedValue(makeStageStatus());
    const onSelectField = vi.fn();
    render(
      <FieldOverview project="dep" fields={fields} selectedField={null} onSelectField={onSelectField} />,
    );
    await waitFor(() => {
      expect(screen.getByText("Sector")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("Sector"));
    expect(onSelectField).toHaveBeenCalledWith("sector_name");
  });

  it("applies active class to selected field", async () => {
    mockStageStatus.mockResolvedValue(makeStageStatus());
    const { container } = render(
      <FieldOverview project="dep" fields={fields} selectedField="sector_name" onSelectField={vi.fn()} />,
    );
    await waitFor(() => {
      expect(screen.getByText("Sector")).toBeInTheDocument();
    });
    const activeRow = container.querySelector(".fo-row--active");
    expect(activeRow).not.toBeNull();
  });

  it("shows 'Needs human review' when n_needs_review > 0", async () => {
    mockStageStatus.mockResolvedValue(
      makeStageStatus({
        n_needs_review: 1,
        models: [
          { model_id: "a", gate_metric_name: "accuracy", gate_metric: 0.80, precision: null, recall: null, f1: null, accuracy: 0.80, kappa: null, n: 100, llm_judged_accuracy: null, n_judged: 0, gate_passed: false, opt_status: "plateaued" },
        ],
      }),
    );
    render(
      <FieldOverview project="dep" fields={fields} selectedField={null} onSelectField={vi.fn()} />,
    );
    await waitFor(() => {
      expect(screen.getAllByText(/Needs human review/)).toHaveLength(2);
    });
  });
});
