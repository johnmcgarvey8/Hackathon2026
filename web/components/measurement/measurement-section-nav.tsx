"use client";

import { useEffect, useState } from "react";

const sections = [
  { id: "brief", label: "Brief" },
  { id: "query-plan", label: "Grounding & Brand Presence" },
  { id: "model-answers", label: "LLM Provider Survey" },
  { id: "citation-performance", label: "Citation Performance" },
  { id: "recommendations", label: "Recommendations" },
] as const;

type SectionId = (typeof sections)[number]["id"];

function isSectionId(value: string): value is SectionId {
  return sections.some((section) => section.id === value);
}

export function MeasurementSectionNav() {
  const [activeSection, setActiveSection] = useState<SectionId>("brief");

  useEffect(() => {
    const targets = sections
      .map((section) => document.getElementById(section.id))
      .filter((target): target is HTMLElement => target !== null);
    const scrollRoot = document.querySelector<HTMLElement>(".main-content");

    const updateActiveSection = () => {
      const rootTop = scrollRoot?.getBoundingClientRect().top ?? 0;
      const rootHeight = scrollRoot?.clientHeight ?? window.innerHeight;
      const activationLine = rootTop + Math.min(180, rootHeight * 0.25);
      let nextSection: SectionId = "brief";

      for (const target of targets) {
        if (target.getBoundingClientRect().top > activationLine) break;
        if (isSectionId(target.id)) nextSection = target.id;
      }

      setActiveSection(nextSection);
    };

    const observer = new IntersectionObserver(updateActiveSection, {
      root: scrollRoot,
      rootMargin: "-10% 0px -75% 0px",
      threshold: [0, 1],
    });

    targets.forEach((target) => observer.observe(target));
    updateActiveSection();

    const syncHash = () => {
      const hash = window.location.hash.slice(1);
      if (isSectionId(hash)) setActiveSection(hash);
    };
    syncHash();
    window.addEventListener("hashchange", syncHash);

    return () => {
      observer.disconnect();
      window.removeEventListener("hashchange", syncHash);
    };
  }, []);

  return (
    <aside className="measurement-section-nav">
      <nav aria-label="Measurement run sections">
        <p>On this page</p>
        <ol>
          {sections.map((section) => (
            <li key={section.id}>
              <a
                href={`#${section.id}`}
                className={activeSection === section.id ? "active" : undefined}
                aria-current={activeSection === section.id ? "location" : undefined}
              >
                {section.label}
              </a>
            </li>
          ))}
        </ol>
      </nav>
    </aside>
  );
}
