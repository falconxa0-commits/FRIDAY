"""Living Memory — mutation testing framework.

This module injects controlled defects into the implementation and runs the
test suite against each mutated version. If a mutation goes undetected (i.e.
tests still pass), it indicates a coverage gap.

Each mutation:
    1. Backs up the original file
    2. Applies the mutation (a targeted patch)
    3. Runs the test suite
    4. Restores the original
    5. Records whether the mutation was caught

Mutations cover the critical security/correctness paths:
    - Authorization bypass (skip authz check)
    - Immune system bypass (skip validation)
    - Provenance chain tamper (skip verify_chain)
    - Lifecycle transition bypass (skip validate_transition)
    - Capacity cap bypass (skip ResourceLimitError)
    - Contradiction detection bypass (skip detector)
    - Tenant isolation bypass (skip tenant check)
    - Persistence integrity hash skip
    - Replay protection skip
    - Decay threshold skip
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple

REPO_ROOT = Path("/home/z/my-project/work/FRIDAY")
LIVING_MEMORY_DIR = REPO_ROOT / "core" / "living_memory"


@dataclass
class Mutation:
    """A single mutation to apply."""
    name: str
    file: str               # relative to LIVING_MEMORY_DIR
    find: str               # exact string to find
    replace: str            # replacement
    description: str        # human-readable description
    expected_tests_to_fail: List[str] = field(default_factory=list)


@dataclass
class MutationResult:
    """Result of running one mutation."""
    name: str
    description: str
    applied: bool          # was the mutation applied successfully?
    tests_failed: int      # number of tests that failed with the mutation
    tests_passed: int
    caught: bool           # did at least one test catch this mutation?
    sample_failures: List[str] = field(default_factory=list)


# ----------------------------------------------------------------------
# Mutation catalog
# ----------------------------------------------------------------------


MUTATIONS: List[Mutation] = [
    Mutation(
        name="authz_skip_tenant_check",
        file="authz.py",
        find="""        if ctx.tenant_id != target_tenant_id:
            raise AuthorizationError(
                "Cross-tenant write denied (constitution Article 7)"
            )""",
        replace="""        # MUTATION: tenant check disabled
        pass""",
        description="Authorization: skip cross-tenant write check",
    ),
    Mutation(
        name="authz_skip_banned_check",
        file="authz.py",
        find="""    def _check_authenticated(self, ctx: AuthorizationContext) -> None:
        if not ctx.citizen_id:
            raise AuthorizationError("Missing citizen_id — unauthenticated")
        if ctx.is_banned:
            raise AuthorizationError(
                f"Citizen {ctx.citizen_id[:8]} is banned"
            )""",
        replace="""    def _check_authenticated(self, ctx: AuthorizationContext) -> None:
        # MUTATION: banned check disabled
        if not ctx.citizen_id:
            raise AuthorizationError("Missing citizen_id — unauthenticated")""",
        description="Authorization: skip banned-citizen check",
    ),
    Mutation(
        name="authz_skip_rank_check_for_write",
        file="authz.py",
        find="""        if ctx.rank_level < self._min_rank_for_write:
            raise AuthorizationError(
                f"Rank {ctx.rank_level} below write threshold "
                f"{self._min_rank_for_write}"
            )""",
        replace="""        # MUTATION: rank check disabled
        pass""",
        description="Authorization: skip rank check for write",
    ),
    Mutation(
        name="immune_skip_provenance_validation",
        file="immune.py",
        find="""    def _validate_provenance(
        self, memory: Memory, provenance: Optional[Provenance] = None, **kw
    ) -> None:
        \"\"\"Persistent memory types require provenance chain.\"\"\"
        if not MemoryType.is_persistent(memory.type):
            return
        if provenance is None or provenance.is_empty:
            raise ImmuneRejection(
                f"Persistent memory type {memory.type.value} requires provenance"
            )""",
        replace="""    def _validate_provenance(
        self, memory: Memory, provenance: Optional[Provenance] = None, **kw
    ) -> None:
        # MUTATION: provenance check disabled
        return""",
        description="Immune: skip provenance validation",
    ),
    Mutation(
        name="immune_skip_payload_size",
        file="immune.py",
        find="""    def _validate_payload_size(self, memory: Memory, **kw) -> None:
        size = payload_size_bytes(memory.payload)
        if size > self._max_payload_bytes:
            raise ImmuneRejection(
                f"Payload exceeds size cap: {size} > {self._max_payload_bytes} bytes",
                payload={\"size\": size, \"cap\": self._max_payload_bytes},
            )""",
        replace="""    def _validate_payload_size(self, memory: Memory, **kw) -> None:
        # MUTATION: size check disabled
        return""",
        description="Immune: skip payload size validation",
    ),
    Mutation(
        name="immune_skip_replay_check",
        file="immune.py",
        find="""    def _validate_replay(
        self,
        memory: Memory,
        seen_idempotency_keys: Optional[Set[str]] = None,
        idempotency_key: str = \"\",
        **kw,
    ) -> None:
        \"\"\"If idempotency_key is provided, it must not have been seen recently.\"\"\"
        if not idempotency_key:
            return
        if idempotency_key in (seen_idempotency_keys or set()):
            raise ImmuneRejection(
                f\"Replay detected: idempotency_key {idempotency_key[:8]} already seen\",
                payload={\"idempotency_key\": idempotency_key},
            )""",
        replace="""    def _validate_replay(
        self,
        memory: Memory,
        seen_idempotency_keys: Optional[Set[str]] = None,
        idempotency_key: str = \"\",
        **kw,
    ) -> None:
        # MUTATION: replay check disabled
        return""",
        description="Immune: skip replay attack detection",
    ),
    Mutation(
        name="immune_skip_existing_id_check",
        file="immune.py",
        find="""    def _validate_existing_id(
        self, memory: Memory, existing_ids: Optional[Set[str]] = None, **kw
    ) -> None:
        \"\"\"Caller-supplied ID must not collide with an existing memory.\"\"\"
        existing = existing_ids or set()
        if memory.id.value in existing:
            raise ImmuneRejection(
                f\"Forged/colliding ID: {memory.id.value[:8]} already exists\",
                payload={\"id\": memory.id.value},
            )""",
        replace="""    def _validate_existing_id(
        self, memory: Memory, existing_ids: Optional[Set[str]] = None, **kw
    ) -> None:
        # MUTATION: existing-id check disabled
        return""",
        description="Immune: skip forged-ID collision check",
    ),
    Mutation(
        name="provenance_skip_verify_chain",
        file="provenance.py",
        find="""    def verify_chain(self) -> bool:
        \"\"\"Verify the entire hash chain is intact.

        Returns False if any entry's hash doesn't match its content,
        or if any parent_hash doesn't match the previous entry's hash.

        V10: O(n) implementation — uses enumerate() instead of list.index()
        (which was O(n) per call, making the loop O(n²)).
        \"\"\"
        prev_hash = \"\"
        for i, entry in enumerate(self.chain):
            if not entry.verify():
                logger.warning(
                    \"Provenance chain broken: entry hash mismatch at idx %d\", i,
                )
                return False
            if entry.parent_hash != prev_hash:
                logger.warning(
                    \"Provenance chain broken: parent hash mismatch at idx %d\", i,
                )
                return False
            prev_hash = entry.entry_hash
        return True""",
        replace="""    def verify_chain(self) -> bool:
        # MUTATION: chain verification always returns True
        return True""",
        description="Provenance: skip chain integrity verification",
    ),
    Mutation(
        name="lifecycle_skip_validate_transition",
        file="base.py",
        find="""def validate_transition(src: MemoryState, dst: MemoryState) -> None:
    \"\"\"Raise LifecycleError if src→dst is not allowed.\"\"\"
    allowed = ALLOWED_TRANSITIONS.get(src, set())
    if dst not in allowed:
        raise LifecycleError(
            f\"Invalid memory lifecycle transition: {src.value} → {dst.value}\"
        )""",
        replace="""def validate_transition(src: MemoryState, dst: MemoryState) -> None:
    # MUTATION: transition validation disabled
    return""",
        description="Lifecycle: skip transition validation",
    ),
    Mutation(
        name="working_memory_skip_capacity_cap",
        file="working.py",
        find="""            # Evict if over capacity
            if len(self._items) > self._capacity:
                await self._evict(len(self._items) - self._capacity)""",
        replace="""            # MUTATION: capacity eviction disabled
            pass""",
        description="WorkingMemory: skip capacity-based eviction",
    ),
    Mutation(
        name="association_skip_degree_cap",
        file="association.py",
        find="""            # Check degree cap on SOURCE
            out_degree = len(self._out.get(edge.source_id, {}))
            if out_degree >= self._max_degree:
                raise ResourceLimitError(
                    f\"Node {edge.source_id[:8]} at max out-degree {self._max_degree}\"
                )""",
        replace="""            # MUTATION: degree cap disabled
            pass""",
        description="AssociationGraph: skip degree cap enforcement",
    ),
    Mutation(
        name="association_skip_total_edge_cap",
        file="association.py",
        find="""            # Check total edge cap
            if len(self._edges_by_id) >= self._max_edges:
                raise ResourceLimitError(
                    f\"Association graph at edge cap: {self._max_edges}\"
                )""",
        replace="""            # MUTATION: total edge cap disabled
            pass""",
        description="AssociationGraph: skip total edge cap",
    ),
    Mutation(
        name="contradiction_skip_detector",
        file="manager.py",
        find="""            # Detect contradictions (semantic + procedural)
            if memory.type in (MemoryType.SEMANTIC, MemoryType.PROCEDURAL):
                candidates = self._build_contradiction_candidates(tenant, memory)
                contradictions = self._contradiction.detect(
                    new_memory_id=memory.id.value,
                    new_payload=memory.payload,
                    new_type=memory.type.value,
                    candidates=candidates,
                )""",
        replace="""            # MUTATION: contradiction detection disabled
            if False and memory.type in (MemoryType.SEMANTIC, MemoryType.PROCEDURAL):
                candidates = []
                contradictions = []""",
        description="Manager: skip contradiction detection",
    ),
    Mutation(
        name="manager_skip_integrity_recompute",
        file="manager.py",
        find="""    async def _persist(self, memory: Memory, provenance: Provenance) -> None:
        \"\"\"Re-seal integrity hash (post-mutation) and persist atomically.

        This is the single point through which all persistence flows.
        It guarantees that the stored integrity_hash matches the in-memory
        state, so recovery verify_integrity() passes.
        \"\"\"
        memory.integrity_hash = self._immune.compute_integrity_hash(memory, provenance)
        await self._persistence.save(memory, provenance)""",
        replace="""    async def _persist(self, memory: Memory, provenance: Provenance) -> None:
        # MUTATION: integrity hash recompute skipped
        await self._persistence.save(memory, provenance)""",
        description="Manager: skip integrity hash recompute on persist",
    ),
    Mutation(
        name="authz_skip_forget_rank_check",
        file="authz.py",
        find="""        # Rank check: only Governor+ can forget without explicit approval
        if not ctx.is_founder and ctx.rank_level < 60:
            raise AuthorizationError(
                \"Forget requires Governor+ rank or Founder approval\"
            )""",
        replace="""        # MUTATION: forget rank check disabled
        pass""",
        description="Authorization: skip forget rank check (privilege escalation)",
    ),
    Mutation(
        name="immune_skip_metadata_validation",
        file="immune.py",
        find="""    def _validate_metadata(self, memory: Memory, **kw) -> None:
        md = memory.metadata
        if not isinstance(md.confidence, (int, float)) or math.isnan(md.confidence):
            raise ImmuneRejection(\"Confidence is NaN or non-numeric\")
        if not 0.0 <= md.confidence <= 1.0:
            raise ImmuneRejection(
                f\"Confidence out of range [0,1]: {md.confidence}\",
                payload={\"confidence\": md.confidence},
            )""",
        replace="""    def _validate_metadata(self, memory: Memory, **kw) -> None:
        # MUTATION: metadata validation disabled
        return""",
        description="Immune: skip metadata validation (NaN/negative/out-of-range)",
    ),
]


# ----------------------------------------------------------------------
# Mutation runner
# ----------------------------------------------------------------------


def apply_mutation(mutation: Mutation) -> bool:
    """Apply the mutation. Returns True if applied successfully."""
    target = LIVING_MEMORY_DIR / mutation.file
    if not target.exists():
        return False
    content = target.read_text()
    if mutation.find not in content:
        return False
    patched = content.replace(mutation.find, mutation.replace, 1)
    target.write_text(patched)
    return True


def restore_mutation(mutation: Mutation, backup_dir: Path) -> None:
    """Restore the original file from backup."""
    target = LIVING_MEMORY_DIR / mutation.file
    backup = backup_dir / mutation.file
    if backup.exists():
        shutil.copy2(backup, target)


def backup_all(backup_dir: Path) -> None:
    """Backup all living_memory files."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    for f in LIVING_MEMORY_DIR.glob("*.py"):
        shutil.copy2(f, backup_dir / f.name)


def run_test_suite(test_files: List[str]) -> Tuple[int, int, List[str]]:
    """Run pytest on the given test files. Returns (passed, failed, sample_failures)."""
    cmd = [
        sys.executable, "-m", "pytest",
        *test_files,
        "-q", "--tb=no", "--no-header",
        "-x",  # stop on first failure for speed
    ]
    try:
        result = subprocess.run(
            cmd, cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        return 0, 0, ["TIMEOUT"]
    output = result.stdout + result.stderr
    # Parse pytest output: "X passed, Y failed in Zs"
    import re
    m = re.search(r"(\d+) passed", output)
    passed = int(m.group(1)) if m else 0
    m = re.search(r"(\d+) failed", output)
    failed = int(m.group(1)) if m else 0
    if failed == 0 and result.returncode != 0:
        failed = 1  # at least one if returncode is non-zero
    # Extract sample failures (first 5 FAILED lines)
    failures = [
        line for line in output.split("\n")
        if line.startswith("FAILED") or "ERROR" in line[:20]
    ][:5]
    return passed, failed, failures


def main():
    test_files = [
        "tests/test_living_memory.py",
        "tests/test_living_memory_security.py",
        "tests/test_living_memory_chaos.py",
        "tests/test_living_memory_concurrency.py",
        "tests/test_living_memory_redteam.py",
    ]
    print("=" * 70)
    print("FRIDAY LIVING MEMORY — MUTATION TESTING")
    print("=" * 70)
    print(f"Total mutations: {len(MUTATIONS)}")
    print(f"Test files: {len(test_files)}")
    print()

    backup_dir = Path(tempfile.mkdtemp(prefix="friday_lm_backup_"))
    backup_all(backup_dir)
    print(f"Backed up originals to {backup_dir}")

    # Run baseline (no mutations)
    print("\n[BASELINE] Running test suite with NO mutations...")
    bp, bf, _ = run_test_suite(test_files)
    print(f"  Baseline: {bp} passed, {bf} failed")
    if bf > 0:
        print("  ⚠️ Baseline has failures — fix before mutation testing")
        return

    results: List[MutationResult] = []
    for i, mutation in enumerate(MUTATIONS, 1):
        print(f"\n[{i}/{len(MUTATIONS)}] {mutation.name}")
        print(f"  Description: {mutation.description}")
        applied = apply_mutation(mutation)
        if not applied:
            print("  ⚠️ Could not apply mutation (find string not found)")
            results.append(MutationResult(
                name=mutation.name, description=mutation.description,
                applied=False, tests_failed=0, tests_passed=0, caught=False,
            ))
            continue
        try:
            passed, failed, failures = run_test_suite(test_files)
            caught = failed > 0
            results.append(MutationResult(
                name=mutation.name, description=mutation.description,
                applied=True, tests_failed=failed, tests_passed=passed,
                caught=caught, sample_failures=failures,
            ))
            status = "✅ CAUGHT" if caught else "❌ NOT CAUGHT"
            print(f"  Result: {status} ({failed} tests failed)")
            if failures:
                for f in failures[:3]:
                    print(f"    - {f}")
        finally:
            restore_mutation(mutation, backup_dir)

    # Restore all originals (safety)
    for f in LIVING_MEMORY_DIR.glob("*.py"):
        backup = backup_dir / f.name
        if backup.exists():
            shutil.copy2(backup, f)

    # Summary
    print("\n" + "=" * 70)
    print("MUTATION TESTING SUMMARY")
    print("=" * 70)
    total = len(results)
    caught = sum(1 for r in results if r.caught)
    not_caught = [r for r in results if not r.caught]
    coverage = (caught / total * 100) if total else 0
    print(f"Mutations applied: {total}")
    print(f"Mutations caught:  {caught}")
    print(f"Mutations missed:  {len(not_caught)}")
    print(f"Mutation coverage: {coverage:.1f}%")
    if not_caught:
        print("\n⚠️ Mutations NOT caught by tests:")
        for r in not_caught:
            print(f"  - {r.name}: {r.description}")
    print("=" * 70)
    return 0 if coverage >= 80 else 1  # 80% minimum mutation coverage


if __name__ == "__main__":
    sys.exit(main())
