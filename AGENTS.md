# AGENTS.md

## Purpose
Guardrails for work in this repository. All modeling and reporting tasks must follow these rules.

## Workflow and documentation
- Read `README.md` before making changes and restate key inputs/assumptions in the report.
- Keep all quantities in meters (m) unless explicitly stated otherwise.
- Document every command executed in a "Run log" section of the report.
- Record derived values and intermediate calculations that affect conclusions.

## Testing and validation
- Run all generated code and confirm it produces valid output.
- Include at least one internal consistency check in the model (e.g., geometry sanity).
- If conclusions depend on assumptions or approximations, explicitly state them in the report.

## Safety and scope
- Do not use external data or web lookups unless explicitly requested.
- Keep the model deterministic and reproducible from the repository state.
