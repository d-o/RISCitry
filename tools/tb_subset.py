"""Cut a scratch copy of the project down to some testbenches, for a quick `alchitry sim` pass
(its --tests option selects nothing in Alchitry Labs 2.0.57, so the copy is trimmed instead).

Usage: python3 tools/tb_subset.py COPY_DIR NAME [NAME...]
  NAME is a testbench name (e.g. cpu_timer_tb, machine_tb, text_display_tb). Testbench files
  with none of the names are dropped from the copy's .alp; files holding several testbenches
  (the generated cpu_tb.luc) keep only the named ones. Never run it on the real project.
  A trimmed copy may warn that a component (e.g. uart_rx) is unused: expected, not a bug.
"""
import json
import re
import sys
from pathlib import Path


def blocks(text):
    """Split a .luc file into (preamble, [(testbench name, text)])."""
    starts = [m.start() for m in re.finditer(r"^testbench (\w+)", text, re.M)]
    if not starts:
        return text, []
    parts = [text[a:b] for a, b in zip(starts, starts[1:] + [len(text)])]
    return text[:starts[0]], [(re.match(r"testbench (\w+)", p).group(1), p) for p in parts]


def main():
    root, names = Path(sys.argv[1]), set(sys.argv[2:])
    alp = next(root.glob("*.alp"))
    project = json.loads(alp.read_text())
    found = set()

    def keep(entry):
        path = entry.get("file", {}).get("path", "")
        if not path.startswith("source/tb/"):
            return True
        pre, tbs = blocks((root / path).read_text())
        chosen = [(n, t) for n, t in tbs if n in names]
        found.update(n for n, _ in chosen)
        if not chosen:
            return False
        if len(chosen) < len(tbs):
            (root / path).write_text(pre + "".join(t for _, t in chosen))
        return True

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, list):
                    node[k] = [walk(e) for e in v if not (isinstance(e, dict) and "file" in e) or keep(e)]
                else:
                    walk(v)
        return node

    walk(project)
    missing = names - found
    if missing:
        sys.exit(f"no such testbench: {', '.join(sorted(missing))}")
    alp.write_text(json.dumps(project, indent=4))


if __name__ == "__main__":
    main()
