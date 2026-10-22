import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Citation } from "../api/types";
import { AnswerText } from "./AnswerText";

const citation: Citation = {
  number: 1,
  chunk_id: "c1",
  document_id: "d1",
  filename: "handbook.md",
  section: "Leave",
  page_start: null,
  page_end: null,
  text: "Employees accrue 1.75 days.",
  score: 0.03,
  vector_similarity: 0.7,
};

describe("AnswerText", () => {
  it("turns citation markers into buttons and leaves unknown markers as text", () => {
    const onCite = vi.fn();
    const { container } = render(
      <AnswerText text="Staff accrue 1.75 days [1]. Not real [7]." citations={[citation]} onCite={onCite} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "1" }));
    expect(onCite).toHaveBeenCalledWith(citation);
    expect(screen.queryByRole("button", { name: "7" })).toBeNull();
    expect(container.textContent).toBe("Staff accrue 1.75 days 1. Not real [7].");
  });
});
