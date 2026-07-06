import { useEffect, useRef } from "react";
import { driver } from "driver.js";
import "driver.js/dist/driver.css";

export function useWalkthrough() {
  const driverRef = useRef<ReturnType<typeof driver> | null>(null);

  useEffect(() => {
    driverRef.current = driver({
      showProgress: true,
      animate: true,
      overlayOpacity: 0.55,
      stagePadding: 6,
      popoverClass: "promptlab-tour",
      steps: [
        {
          element: "#tour-header",
          popover: {
            title: "Welcome to the 3ie Prompt Lab",
            description:
              "This dashboard shows how different AI models and prompts perform on evidence-synthesis tasks — like screening studies and extracting structured information from research papers.",
            side: "bottom",
            align: "start",
          },
        },
        {
          element: "#tour-project-switcher",
          popover: {
            title: "Project selector",
            description:
              "Switch between different evaluation projects. Each project covers a distinct extraction or classification task.",
            side: "bottom",
            align: "start",
          },
        },
        {
          element: "#tour-tab-nav",
          popover: {
            title: "Navigation tabs",
            description:
              "<strong>Dashboard</strong> shows live results. <strong>How it works</strong> explains the evaluation methodology in detail.",
            side: "bottom",
            align: "start",
          },
        },
        {
          element: "#tour-methodology",
          popover: {
            title: "Evaluation methodology",
            description:
              "A quick summary of the scoring approach. For list fields we use element-level F1; for categorical fields we use accuracy with Cohen's κ.",
            side: "bottom",
            align: "start",
          },
        },
        {
          element: "#tour-field-nav",
          popover: {
            title: "Field navigation",
            description:
              "Each button represents one piece of information being extracted — e.g. sector, country, intervention type. Click any field to see how models perform on it.",
            side: "right",
            align: "start",
          },
        },
        {
          element: "#tour-stage-badge",
          popover: {
            title: "Stage gate badge",
            description:
              "Shows the current optimization stage and how many models have cleared the quality gate (≥ the threshold for this field). Green = pass, red = below gate.",
            side: "bottom",
            align: "start",
          },
        },
        {
          element: "#tour-model-table",
          popover: {
            title: "Model comparison table",
            description:
              "All models ranked by quality score. Click any column header to re-sort. Green rows pass the production gate; red rows are below it. <em>Concordance</em> is an independent LLM-judge check.",
            side: "top",
            align: "start",
          },
        },
        {
          element: "#tour-model-filter",
          popover: {
            title: "Model filter",
            description:
              "Toggle individual models on or off to focus the per-model detail cards below.",
            side: "bottom",
            align: "start",
          },
        },
      ],
    });

    return () => {
      driverRef.current?.destroy();
    };
  }, []);

  const start = () => {
    driverRef.current?.drive();
  };

  return { start };
}
