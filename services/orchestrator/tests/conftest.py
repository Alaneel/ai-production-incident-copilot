import os

# Developers often keep Anthropic credentials in their shell. The orchestrator
# turns Claude reasoning on when ANTHROPIC_API_KEY and ANTHROPIC_MODEL are both
# set, so drop them here: no test may make a paid model call by accident.
for _name in ("ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "ANTHROPIC_BASE_URL", "ANTHROPIC_EFFORT"):
    os.environ.pop(_name, None)
