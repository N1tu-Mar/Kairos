import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ScraperCandidates } from "@/components/scraper-candidates";
import { scraperCandidate, scraperCandidateGroup } from "./fixtures";

describe("ScraperCandidates", () => {
  it("renders university and general lanes as separate sections", () => {
    render(
      <ScraperCandidates
        groups={{
          university: scraperCandidateGroup({
            lane: "university",
            source_file: "opportunities.university-web.candidates.json",
            candidates: [
              scraperCandidate({
                scrape_id: "campus_1",
                title: "Campus Venture Prize",
                organization: "Example University Innovation Center",
              }),
            ],
          }),
          general: scraperCandidateGroup({
            lane: "general",
            label: "general funding",
            source_file: "opportunities.web.candidates.json",
            candidates: [
              scraperCandidate({
                scrape_id: "public_1",
                title: "Public Founder Grant",
                organization: "Public Startup Foundation",
                source_url: "https://publicfunding.example.org/grant",
                caveats: [
                  "[general web search] This page has not been human reviewed.",
                ],
              }),
            ],
          }),
        }}
      />,
    );

    expect(screen.getByText("University search")).toBeInTheDocument();
    expect(screen.getByText("General web search")).toBeInTheDocument();
    expect(screen.getByText("Campus Venture Prize")).toBeInTheDocument();
    expect(screen.getByText("Public Founder Grant")).toBeInTheDocument();
    expect(
      screen.getByText("opportunities.university-web.candidates.json"),
    ).toBeInTheDocument();
    expect(screen.getByText("opportunities.web.candidates.json")).toBeInTheDocument();
  });

  it("shows scraper facts without promoting candidates into recommendations", () => {
    render(
      <ScraperCandidates
        groups={{
          university: scraperCandidateGroup({
            candidates: [
              scraperCandidate({
                award_min: 2_000,
                award_max: 7_500,
                deadline_iso: "2027-05-01",
                unknown_fields: ["deadline", "equity_required"],
              }),
            ],
          }),
        }}
      />,
    );

    expect(screen.getByText("Review needed")).toBeInTheDocument();
    expect(screen.getByText(/^\$2,000.*\$7,500$/)).toBeInTheDocument();
    expect(screen.getByText("Due May 1, 2027")).toBeInTheDocument();
    expect(screen.getByText("2 unknown")).toBeInTheDocument();
    expect(screen.queryByText(/review the draft/i)).toBeNull();
  });

  it("renders empty lanes plainly before a scraper has written files", () => {
    render(<ScraperCandidates groups={{}} />);

    expect(
      screen.getByText("No university candidates have been written yet."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("No general web candidates have been written yet."),
    ).toBeInTheDocument();
  });
});
