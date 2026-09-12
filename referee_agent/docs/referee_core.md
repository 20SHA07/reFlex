# Agent Referee Core

Deterministic policy and controlled file execution for the Agent Referee project.

## Components

- `referee.py` - policy decisions: `BLOCK`, `DEFER`, and `REVIEW`.
- `executor.py` - trusted execution, approvals, version checks, quarantine, and audit logging.
- `demo.py` - standalone safety/conflict demonstration without Slack or model API keys.
- `tests/test_referee.py` - 30 safety and coordination tests.
- `docs/referee_integration.md` - backend and Slack integration contract.

## Validate

```bash
python -m unittest discover -s tests -v
python demo.py
```

No third-party packages are required for the referee core itself.

## Trust boundary

Worker agents may propose actions, but they do not receive direct access to `execute_approved_action`, unrestricted filesystem operations, or shell execution. The application supplies verified Slack identities and trusted task state.

For the prototype, cleanup is implemented as quarantine rather than permanent deletion.
