"""Qwen Code CLI stderr/stdout-classifier (Wave 1b).

Qwen Code is a fork of gemini-cli (same upstream, Tongyi Lab maintains
its own model + auth layer). The CLI surface is byte-compatible: it
emits a single terminal JSON blob on stdout when invoked with
``--output-format json`` and streams tool / progress hints on stderr.

We do a *light* classification mirroring the gemini parser:

  Error executing tool <name>: <reason>  → tool_result (✗)
  [qwen] tool: <name>                    → tool_use
  any other stderr                       → stderr (dim red)

Final stdout JSON parsing stays in the executor's body (one-shot at end
of run) — the parser here is just the live stderr pump.
"""
from __future__ import annotations

import re
from typing import Callable


_TOOL_ERR_RE = re.compile(r"Error executing tool (\S+?):\s+(.+)")
# Qwen Code prints both ``[qwen]`` and ``[qwen-code]`` prefixes depending
# on which sub-binary handled the call; accept either so a future rename
# upstream doesn't silently drop tool_use stream events.
_TOOL_USE_RE = re.compile(r"\[qwen(?:-code)?\]\s+tool:\s*(\S+)")


def build_parser(emitter) -> Callable[[str, str], None]:
    def on_chunk(line: str, fd: str) -> None:
        if fd == "stdout":
            # We do NOT live-stream stdout for qwen — it's a single
            # terminal JSON blob, parsed once at end of run in the
            # executor. Forward as plain stdout so the user sees the
            # final dump shape.
            if line.strip():
                emitter.emit_chunk("stdout", line)
            return
        # fd == "stderr"
        if not line.strip():
            return
        m = _TOOL_ERR_RE.search(line)
        if m:
            name = m.group(1)
            err = m.group(2)
            emitter.emit_chunk("tool_result", f"x {name}: {err[:240]}\n")
            return
        m2 = _TOOL_USE_RE.search(line)
        if m2:
            name = m2.group(1)
            emitter.emit_chunk("tool_use", f"-> Tool({name})\n")
            return
        emitter.emit_chunk("stderr", line, fd="stderr")

    return on_chunk
