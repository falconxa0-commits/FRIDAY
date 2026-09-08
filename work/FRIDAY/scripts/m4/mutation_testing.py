"""Knowledge Graph Ω — mutation testing framework.

Injects controlled defects into M4 implementation and verifies tests catch them.
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
KG_DIR = REPO_ROOT / "core" / "knowledge_graph"


@dataclass
class Mutation:
    name: str
    file: str
    find: str
    replace: str
    description: str


@dataclass
class MutationResult:
    name: str
    description: str
    applied: bool
    tests_failed: int
    tests_passed: int
    caught: bool
    sample_failures: List[str] = field(default_factory=list)


MUTATIONS: List[Mutation] = [
    Mutation(
        name="entity_skip_alias_cap",
        file="entity.py",
        find="""        if len(self.aliases) > 32:
            raise ValueError(f\"Too many aliases: {len(self.aliases)} > 32\")""",
        replace="""        # MUTATION: alias cap disabled
        pass""",
        description="Entity: skip alias cap (32)",
    ),
    Mutation(
        name="entity_skip_attribute_cap",
        file="entity.py",
        find="""        if len(self.attributes) >= 128 and name not in self.attributes:
            raise ValueError(f\"Attribute cap reached (128) for entity {self.entity_id}\")""",
        replace="""        # MUTATION: attribute cap disabled
        pass""",
        description="Entity: skip attribute cap (128)",
    ),
    Mutation(
        name="entity_skip_canonicalize_pipe_check",
        file="entity.py",
        find="""    # Reject pipe character — would collide with M3 edge_key format
    if \"|\" in cleaned:
        raise ValueError(\"Entity name must not contain '|' character\")""",
        replace="""    # MUTATION: pipe check disabled
    pass""",
        description="Entity: skip pipe character rejection",
    ),
    Mutation(
        name="relationship_skip_self_check",
        file="relationship.py",
        find="""        if self.source_entity_id == self.target_entity_id:
            raise ValueError(\"Self-relationship not allowed\")""",
        replace="""        # MUTATION: self-relationship check disabled
        pass""",
        description="Relationship: skip self-relationship check",
    ),
    Mutation(
        name="relationship_skip_weight_validation",
        file="relationship.py",
        find="""        if not 0.0 <= self.weight <= 1.0:
            raise ValueError(f\"Weight must be in [0,1], got {self.weight}\")""",
        replace="""        # MUTATION: weight validation disabled
        pass""",
        description="Relationship: skip weight validation",
    ),
    Mutation(
        name="relationship_skip_evidence_cap",
        file="relationship.py",
        find="""        if len(self.evidence) > 64:
            raise ValueError(f\"Too many evidence entries: {len(self.evidence)} > 64\")""",
        replace="""        # MUTATION: evidence cap disabled
        pass""",
        description="Relationship: skip evidence cap",
    ),
    Mutation(
        name="graph_skip_entity_cap",
        file="graph.py",
        find="""            if len(self._entities) >= self._max_entities:
                raise ValueError(
                    f\"Entity cap reached: {self._max_entities} (tenant={self._tenant_id})\"
                )""",
        replace="""            # MUTATION: entity cap disabled
            pass""",
        description="Graph: skip entity cap",
    ),
    Mutation(
        name="graph_skip_degree_cap",
        file="graph.py",
        find="""            # Check degree cap on SOURCE (out-degree)
            out_count = sum(
                1 for rid in self._adjacency.get(rel.source_entity_id, set())
                if rid in self._relationships and
                self._relationships[rid].source_entity_id == rel.source_entity_id
            )
            if out_count >= self._max_degree:
                raise ValueError(
                    f\"Out-degree cap reached for entity {rel.source_entity_id[:12]}: {out_count}\"
                )""",
        replace="""            # MUTATION: degree cap disabled
            pass""",
        description="Graph: skip degree cap",
    ),
    Mutation(
        name="graph_skip_endpoint_existence_check",
        file="graph.py",
        find="""            # Check both endpoints exist
            if rel.source_entity_id not in self._entities:
                raise ValueError(f\"Source entity {rel.source_entity_id[:12]} not in graph\")
            if rel.target_entity_id not in self._entities:
                raise ValueError(f\"Target entity {rel.target_entity_id[:12]} not in graph\")""",
        replace="""            # MUTATION: endpoint existence check disabled
            pass""",
        description="Graph: skip endpoint existence check",
    ),
    Mutation(
        name="query_skip_limit_validation",
        file="query.py",
        find="""        if not 1 <= self.limit <= 500:
            raise ValueError(\"limit must be 1..500\")""",
        replace="""        # MUTATION: limit validation disabled
        pass""",
        description="Query: skip limit validation",
    ),
    Mutation(
        name="merge_skip_high_stakes_check",
        file="manager.py",
        find="""        if conflict.requires_founder_approval and not ctx.is_founder:""",
        replace="""        if False and conflict.requires_founder_approval and not ctx.is_founder:""",
        description="Manager: skip high-stakes conflict Founder check",
    ),
    Mutation(
        name="manager_skip_already_resolved_check",
        file="manager.py",
        find="""        if conflict.status != ConflictResolution.PENDING:
            return False""",
        replace="""        # MUTATION: already-resolved check disabled
        pass""",
        description="Manager: skip already-resolved conflict check",
    ),
    Mutation(
        name="manager_skip_text_size_check",
        file="manager.py",
        find="""        if len(text) > self._config.max_extraction_text_size:
            raise ValueError(
                f\"Text too large: {len(text)} > {self._config.max_extraction_text_size}\"
            )""",
        replace="""        # MUTATION: text size check disabled
        pass""",
        description="Manager: skip text size DoS check",
    ),
]


def apply_mutation(mutation: Mutation) -> bool:
    target = KG_DIR / mutation.file
    if not target.exists():
        return False
    content = target.read_text()
    if mutation.find not in content:
        return False
    patched = content.replace(mutation.find, mutation.replace, 1)
    target.write_text(patched)
    return True


def restore_all(backup_dir: Path) -> None:
    for f in KG_DIR.glob("*.py"):
        backup = backup_dir / f.name
        if backup.exists():
            shutil.copy2(backup, f)


def backup_all(backup_dir: Path) -> None:
    backup_dir.mkdir(parents=True, exist_ok=True)
    for f in KG_DIR.glob("*.py"):
        shutil.copy2(f, backup_dir / f.name)


def run_tests(test_files: List[str]) -> Tuple[int, int, List[str]]:
    cmd = [
        sys.executable, "-m", "pytest",
        *test_files,
        "-q", "--tb=no", "--no-header",
        "-x",
    ]
    try:
        result = subprocess.run(
            cmd, cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=180,
        )
    except subprocess.TimeoutExpired:
        return 0, 0, ["TIMEOUT"]
    output = result.stdout + result.stderr
    import re
    m = re.search(r"(\d+) passed", output)
    passed = int(m.group(1)) if m else 0
    m = re.search(r"(\d+) failed", output)
    failed = int(m.group(1)) if m else 0
    if failed == 0 and result.returncode != 0:
        failed = 1
    failures = [
        line for line in output.split("\n")
        if line.startswith("FAILED") or "ERROR" in line[:20]
    ][:5]
    return passed, failed, failures


def main():
    test_files = [
        "tests/test_knowledge_graph.py",
        "tests/test_knowledge_graph_security.py",
        "tests/test_knowledge_graph_chaos.py",
        "tests/test_knowledge_graph_concurrency.py",
        "tests/test_knowledge_graph_redteam.py",
    ]
    print("=" * 72)
    print("FRIDAY KNOWLEDGE GRAPH — MUTATION TESTING (M4)")
    print("=" * 72)
    print(f"Total mutations: {len(MUTATIONS)}")
    print()

    backup_dir = Path(tempfile.mkdtemp(prefix="friday_kg_backup_"))
    backup_all(backup_dir)
    print(f"Backed up to {backup_dir}")

    print("\n[BASELINE] Running test suite with NO mutations...")
    bp, bf, _ = run_tests(test_files)
    print(f"  Baseline: {bp} passed, {bf} failed")
    if bf > 0:
        print("  ⚠️ Baseline has failures — fix before mutation testing")
        restore_all(backup_dir)
        return 1

    results: List[MutationResult] = []
    for i, mutation in enumerate(MUTATIONS, 1):
        print(f"\n[{i}/{len(MUTATIONS)}] {mutation.name}")
        print(f"  Description: {mutation.description}")
        applied = apply_mutation(mutation)
        if not applied:
            print("  ⚠️ Could not apply mutation")
            results.append(MutationResult(
                name=mutation.name, description=mutation.description,
                applied=False, tests_failed=0, tests_passed=0, caught=False,
            ))
            continue
        try:
            passed, failed, failures = run_tests(test_files)
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
            restore_all(backup_dir)

    total = len(results)
    caught = sum(1 for r in results if r.caught)
    not_caught = [r for r in results if not r.caught]
    coverage = (caught / total * 100) if total else 0
    print("\n" + "=" * 72)
    print("MUTATION TESTING SUMMARY (M4)")
    print("=" * 72)
    print(f"Mutations applied: {total}")
    print(f"Mutations caught:  {caught}")
    print(f"Mutations missed:  {len(not_caught)}")
    print(f"Mutation coverage: {coverage:.1f}%")
    if not_caught:
        print("\n⚠️ Mutations NOT caught:")
        for r in not_caught:
            print(f"  - {r.name}: {r.description}")
    print("=" * 72)
    return 0 if coverage >= 80 else 1


if __name__ == "__main__":
    sys.exit(main())
