from geo_agent.contracts import (
    Brief, EvaluationResult, PageSnapshot, Profile, Provenance, Query, Run, RunInputs, Source,
)


def synthetic_inputs() -> RunInputs:
    return RunInputs(
        brief=Brief(
            url="https://example.org/products/team-planner",
            audience="Small business operations managers",
            goal="Compare team planning tools",
            locale="en-GB",
        ),
        snapshot=PageSnapshot(
            url="https://example.org/products/team-planner",
            title="Synthetic team planner",
            content="Fictional team planning software with shared schedules and task lists.",
            provenance=Provenance.SYNTHETIC,
        ),
        queries=tuple(
            Query(query_id=f"q-{index}", text=text, intent="Compare planning tools")
            for index, text in enumerate((
                "Which planning tools suit a small operations team?",
                "What should a team compare when choosing shared scheduling software?",
                "Which tools combine shared schedules and task lists?",
                "What evidence supports a planning tool's accessibility claims?",
                "How can a small team assess planning software integration options?",
            ), start=1)
        ),
        profiles=(Profile(profile_id="synthetic-baseline", deployment="no-model-called", prompt_version="fixture-v1"),),
    )


def evaluate_synthetic(run: Run) -> tuple[EvaluationResult, ...]:
    if run.inputs.snapshot.provenance != Provenance.SYNTHETIC:
        raise ValueError("Synthetic evaluator cannot process live or recorded runs")
    results = []
    for index, query in enumerate(run.inputs.queries):
        for profile in run.inputs.profiles:
            cited = index < 2
            evidence_id = f"{query.query_id}-{profile.profile_id}-source"
            source = Source(
                evidence_id=evidence_id,
                url=run.inputs.snapshot.url if cited else "https://example.net/planning-comparison",
                excerpt="Synthetic evidence: shared schedules and task lists support team planning.",
                provenance=Provenance.SYNTHETIC,
            )
            results.append(EvaluationResult(
                query_id=query.query_id,
                profile_id=profile.profile_id,
                provenance=Provenance.SYNTHETIC,
                status="completed",
                answer="Synthetic answer for workflow testing; not a model response.",
                citation_ids=(evidence_id,),
                sources=(source,),
            ))
    return tuple(results)