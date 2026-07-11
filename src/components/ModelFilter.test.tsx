import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ModelFilter } from "./ModelFilter";

describe("ModelFilter", () => {
  const defaultProps = {
    models: ["openai/gpt-4", "anthropic/claude-3", "google/gemini"],
    selected: new Set(["openai/gpt-4"]),
    onToggle: vi.fn(),
    onSelectAll: vi.fn(),
    onSelectNone: vi.fn(),
  };

  it("renders summary with selected/total counts", () => {
    render(<ModelFilter {...defaultProps} />);
    expect(screen.getByText(/Models shown \(1\/3\)/)).toBeInTheDocument();
  });

  it("renders all model names as checkboxes", () => {
    render(<ModelFilter {...defaultProps} />);
    expect(screen.getByText("openai/gpt-4")).toBeInTheDocument();
    expect(screen.getByText("anthropic/claude-3")).toBeInTheDocument();
    expect(screen.getByText("google/gemini")).toBeInTheDocument();
  });

  it("checks the selected model checkbox", () => {
    render(<ModelFilter {...defaultProps} />);
    const checkbox = screen.getByLabelText("openai/gpt-4") as HTMLInputElement;
    expect(checkbox.checked).toBe(true);
  });

  it("leaves unselected model checkbox unchecked", () => {
    render(<ModelFilter {...defaultProps} />);
    const checkbox = screen.getByLabelText("anthropic/claude-3") as HTMLInputElement;
    expect(checkbox.checked).toBe(false);
  });

  it("calls onToggle when a checkbox is clicked", () => {
    const onToggle = vi.fn();
    render(<ModelFilter {...defaultProps} onToggle={onToggle} />);
    fireEvent.click(screen.getByLabelText("anthropic/claude-3"));
    expect(onToggle).toHaveBeenCalledWith("anthropic/claude-3");
  });

  it("calls onSelectAll when 'select all' is clicked", () => {
    const onSelectAll = vi.fn();
    render(<ModelFilter {...defaultProps} onSelectAll={onSelectAll} />);
    fireEvent.click(screen.getByText("select all"));
    expect(onSelectAll).toHaveBeenCalledOnce();
  });

  it("calls onSelectNone when 'select none' is clicked", () => {
    const onSelectNone = vi.fn();
    render(<ModelFilter {...defaultProps} onSelectNone={onSelectNone} />);
    fireEvent.click(screen.getByText("select none"));
    expect(onSelectNone).toHaveBeenCalledOnce();
  });

  it("handles empty model list", () => {
    render(<ModelFilter {...defaultProps} models={[]} selected={new Set()} />);
    expect(screen.getByText(/Models shown \(0\/0\)/)).toBeInTheDocument();
  });

  it("handles all models selected", () => {
    const all = new Set(defaultProps.models);
    render(<ModelFilter {...defaultProps} selected={all} />);
    expect(screen.getByText(/Models shown \(3\/3\)/)).toBeInTheDocument();
    defaultProps.models.forEach((m) => {
      expect((screen.getByLabelText(m) as HTMLInputElement).checked).toBe(true);
    });
  });
});
