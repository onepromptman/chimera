"""The one launch arc: graph — the planner-emitted-DAG runtime.

The eight fixed pipelines (research/design/proposal/build/n8n/comms/reflect/
gemini) were retired in the v7 consolidation: the planner composes their
shapes as data instead of chimera carrying them as code. Their history lives
in git; pre-consolidation task records still load (models.RETIRED_ARCS).

Arc authors never commit or push — the CLI/tick wrapper owns durability.
tests/test_no_write_outside_wrapper.py greps this package to enforce it.
"""

from . import graph  # noqa: F401 — registers the arc module for CLI dispatch
