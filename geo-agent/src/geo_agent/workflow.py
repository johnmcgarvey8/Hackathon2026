import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from geo_agent.contracts import Approval, EvaluationResult, Run, RunInputs, State


class Conflict(ValueError):
    pass


class NotFound(ValueError):
    pass


class RunStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS runs "
                "(run_id TEXT PRIMARY KEY, owner TEXT NOT NULL, payload TEXT NOT NULL)"
            )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def create(self, owner: str, inputs: RunInputs) -> Run:
        run = Run(owner=owner, inputs=inputs)
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO runs VALUES (?, ?, ?)",
                (run.run_id, owner, run.model_dump_json()),
            )
        return run

    def _read(self, connection: sqlite3.Connection, run_id: str, owner: str) -> Run:
        row = connection.execute(
            "SELECT payload FROM runs WHERE run_id = ? AND owner = ?", (run_id, owner)
        ).fetchone()
        if row is None:
            raise NotFound("Run not found")
        return Run.model_validate_json(row[0])

    def get(self, run_id: str, owner: str) -> Run:
        with self.connect() as connection:
            return self._read(connection, run_id, owner)

    def mutate(
        self, run_id: str, owner: str, revision: int, operation: Callable[[Run], Run]
    ) -> Run:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._read(connection, run_id, owner)
            if current.revision != revision:
                raise Conflict("Stale revision; reload the run")
            updated = operation(current)
            updated = Run.model_validate(updated.model_dump())
            connection.execute(
                "UPDATE runs SET payload = ? WHERE run_id = ? AND owner = ?",
                (updated.model_dump_json(), run_id, owner),
            )
            return updated


class Coordinator:
    def __init__(self, store: RunStore):
        self.store = store

    def approve(self, run_id: str, owner: str, revision: int, input_hash: str) -> Run:
        def operation(run: Run) -> Run:
            if run.state != State.AWAITING_APPROVAL:
                raise Conflict("Run is not awaiting query approval")
            if input_hash != run.inputs.approval_hash:
                raise Conflict("Approval does not match the current inputs")
            if run.approval:
                return run
            return run.model_copy(update={
                "approval": Approval(actor=owner, revision=revision, input_hash=input_hash),
                "events": (*run.events, "queries-approved"),
            })

        return self.store.mutate(run_id, owner, revision, operation)

    def revise(self, run_id: str, owner: str, revision: int, inputs: RunInputs) -> Run:
        def operation(run: Run) -> Run:
            if run.state not in {State.AWAITING_APPROVAL, State.CANCELLED, State.FAILED}:
                raise Conflict("Cancel an active run before changing its inputs")
            if inputs.snapshot.provenance != run.inputs.snapshot.provenance:
                raise Conflict("A run cannot change provenance")
            return run.model_copy(update={
                "inputs": inputs,
                "revision": run.revision + 1,
                "approval": None,
                "start_key": None,
                "results": (),
                "state": State.AWAITING_APPROVAL,
                "events": (*run.events, "inputs-revised", "awaiting-query-approval"),
            })

        return self.store.mutate(run_id, owner, revision, operation)

    def start(self, run_id: str, owner: str, revision: int, key: str) -> Run:
        if not key.strip() or len(key) > 128:
            raise Conflict("A bounded idempotency key is required")

        def operation(run: Run) -> Run:
            if run.state in {State.EVALUATING, State.READY, State.PARTIAL, State.FAILED, State.EXPORTED} and run.start_key == key:
                return run
            if run.state != State.AWAITING_APPROVAL:
                raise Conflict("Run cannot start from its current state")
            approval = run.approval
            if (
                approval is None
                or approval.actor != owner
                or approval.revision != revision
                or approval.input_hash != run.inputs.approval_hash
            ):
                raise Conflict("Current inputs require human query approval")
            return run.model_copy(update={
                "state": State.EVALUATING,
                "start_key": key,
                "events": (*run.events, "evaluating"),
            })

        return self.store.mutate(run_id, owner, revision, operation)

    def finish(
        self, run_id: str, owner: str, revision: int,
        results: tuple[EvaluationResult, ...],
    ) -> Run:
        def operation(run: Run) -> Run:
            if run.results == results and run.state in {State.READY, State.PARTIAL, State.FAILED, State.EXPORTED}:
                return run
            if run.state != State.EVALUATING:
                raise Conflict("Only an evaluating run can accept results")
            intended = {
                (query.query_id, profile.profile_id)
                for query in run.inputs.queries for profile in run.inputs.profiles
            }
            received = {(result.query_id, result.profile_id) for result in results}
            if received != intended or len(received) != len(results):
                raise Conflict("Each approved query/profile requires exactly one outcome")
            if any(result.provenance != run.inputs.snapshot.provenance for result in results):
                raise Conflict("Result provenance must match the run")
            completed = sum(result.status == "completed" for result in results)
            state = State.READY if completed == len(results) else State.PARTIAL if completed else State.FAILED
            return run.model_copy(update={
                "state": state,
                "results": results,
                "events": (*run.events, state.value),
            })

        return self.store.mutate(run_id, owner, revision, operation)

    def mark_exported(self, run_id: str, owner: str, revision: int) -> Run:
        def operation(run: Run) -> Run:
            if run.state == State.EXPORTED:
                return run
            if run.state not in {State.READY, State.PARTIAL, State.FAILED} or not run.results:
                raise Conflict("Only a completed evaluation can be exported")
            return run.model_copy(update={
                "state": State.EXPORTED,
                "events": (*run.events, "exported"),
            })

        return self.store.mutate(run_id, owner, revision, operation)

    def cancel(self, run_id: str, owner: str, revision: int) -> Run:
        def operation(run: Run) -> Run:
            if run.state == State.CANCELLED:
                return run
            if run.state not in {State.AWAITING_APPROVAL, State.EVALUATING}:
                raise Conflict("Run cannot be cancelled from its current state")
            return run.model_copy(update={
                "state": State.CANCELLED,
                "approval": None,
                "events": (*run.events, "cancelled"),
            })

        return self.store.mutate(run_id, owner, revision, operation)