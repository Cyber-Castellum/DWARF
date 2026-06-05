#!/usr/bin/env python3

import argparse
import json
from pathlib import Path


def _load_modules(angr_module=None, claripy_module=None):
    if angr_module is None:
        import angr as angr_module  # type: ignore
    if claripy_module is None:
        import claripy as claripy_module  # type: ignore
    return angr_module, claripy_module


def _write_summary(output_dir: Path, report: dict) -> None:
    lines = [
        "# Symbolic Execution Campaign",
        "",
        f"- paths_explored: {report['paths_explored']}",
        f"- constraints_solved: {report['constraints_solved']}",
        f"- novel_inputs_generated: {report['novel_inputs_generated']}",
        f"- hangs: {report['hangs']}",
        f"- crashes: {report['crashes']}",
        "",
    ]
    (output_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def run_symbolic_execution_campaign(config: dict, *, angr_module=None, claripy_module=None) -> Path:
    angr_module, claripy_module = _load_modules(angr_module=angr_module, claripy_module=claripy_module)
    target_binary_path = Path(config["target_binary_path"])
    output_dir = Path(config["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_dir = output_dir / "generated-inputs"
    generated_dir.mkdir(parents=True, exist_ok=True)
    input_size_bytes = int(config.get("input_size_bytes", 8))
    max_steps = int(config.get("max_steps", 60))
    max_generated_inputs = int(config.get("max_generated_inputs", 4))

    stdin = claripy_module.BVS("stdin", input_size_bytes * 8)
    project = angr_module.Project(str(target_binary_path), auto_load_libs=False)
    state = project.factory.full_init_state(
        args=[str(target_binary_path)],
        stdin=angr_module.SimFileStream(name="stdin", content=stdin, has_end=True),
    )
    options = getattr(angr_module, "options")
    state.options.add(options.ZERO_FILL_UNCONSTRAINED_MEMORY)
    state.options.add(options.ZERO_FILL_UNCONSTRAINED_REGISTERS)
    simgr = project.factory.simulation_manager(state)

    for _ in range(max_steps):
        if not simgr.active:
            break
        simgr.step()

    explored_states = list(simgr.deadended) + list(simgr.active)
    generated = []
    seen_inputs = set()
    for index, candidate in enumerate(explored_states):
        if len(generated) >= max_generated_inputs:
            break
        data = candidate.solver.eval(stdin, cast_to=bytes)
        if data in seen_inputs:
            continue
        seen_inputs.add(data)
        path = generated_dir / f"candidate-{len(generated):03d}.bin"
        path.write_bytes(data)
        generated.append(
            {
                "path": str(path),
                "hex": data.hex(),
                "stdout": candidate.posix.dumps(1).decode("utf-8", errors="replace")[:200],
                "constraint_count": len(getattr(candidate.solver, "constraints", [])),
            }
        )

    report = {
        "target_binary_path": str(target_binary_path),
        "input_size_bytes": input_size_bytes,
        "max_steps": max_steps,
        "max_generated_inputs": max_generated_inputs,
        "paths_explored": len(explored_states),
        "constraints_solved": sum(len(getattr(candidate.solver, "constraints", [])) for candidate in explored_states),
        "novel_inputs_generated": len(generated),
        "hangs": len(simgr.active),
        "crashes": len(simgr.errored),
        "deadended_paths": len(simgr.deadended),
        "generated_inputs": generated,
    }
    (output_dir / "stdout.log").write_text("", encoding="utf-8")
    (output_dir / "stderr.log").write_text("", encoding="utf-8")
    report_path = output_dir / "result.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_summary(output_dir, report)
    return report_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-binary-path", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--input-size-bytes", type=int, default=8)
    parser.add_argument("--max-steps", type=int, default=60)
    parser.add_argument("--max-generated-inputs", type=int, default=4)
    args = parser.parse_args(argv)

    report_path = run_symbolic_execution_campaign(vars(args))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    print(
        " ".join(
            [
                "symbolic_execution_completed=true",
                f"paths_explored={report['paths_explored']}",
                f"novel_inputs_generated={report['novel_inputs_generated']}",
                f"hangs={report['hangs']}",
                f"crashes={report['crashes']}",
            ]
        )
    )
    return 0 if report["paths_explored"] >= 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
