---
name: adapter-stub
description: Generate protocol-compliant adapter stubs for Bucket 3 operations requiring external services. Use when the user requests "adapter stub", "dry run", "Bucket 3", "Linear ticket stub", "Kafka stub", or needs a fail-soft fallback for unavailable services.
---

# Adapter Stub

Use this skill when the user requests a Bucket 3 operation (decompose epic, create Linear ticket, route via Kafka) that requires an external service not available in the current environment.

## Purpose

Generate protocol-compliant adapter stub implementations that demonstrate how a Bucket 3 integration would work without actually calling external services. The stub follows the frozen adapter contract, enforces dry-run semantics, and provides fail-soft fallbacks.

## Prerequisites

- Understanding of the operation the user wants to perform
- Familiarity with the frozen adapter contract in `docs/ARCHITECTURE.md`
- Knowledge of which external services are required (Linear MCP, Kafka, Python validator)

## Workflow

1. **Identify the Bucket 3 operation.**
   Determine which external service the operation requires. Common operations:
   - `decompose-epic`: requires Linear MCP
   - `create-ticket`: requires Linear MCP
   - `generate-ticket-contract`: requires Linear MCP + Python validator
   - `executing-plans`: requires Linear MCP + Kafka routing

2. **Describe what the operation would do.**
   Explain the intended behavior if all services were available. List every external dependency.

3. **Construct the dry-run request payload.**
   Build a JSON payload conforming to the frozen adapter contract:
   - `POST /onex/api/v1/skills/{skill_name}`
   - Include `skill_name`, `input`, `dry_run: true`, and `context` (repo, cwd)
   - Use relative paths for `context.cwd` — never absolute paths

4. **Show expected response shapes.**
   Display the expected success response (`status: "ok"` with `stdout`, `artifacts`, `next_actions`) and the expected error response (`status: "error"` with error code and message).

5. **Apply fail-soft behavior.**
   Since external services are unavailable:
   - Output: `Service unavailable. Complete manually: [next step]`
   - Do NOT retry automatically
   - Do NOT pretend the operation succeeded
   - Do NOT loop or re-invoke the endpoint

6. **Provide manual completion steps.**
   Give the user clear instructions for completing the operation manually (e.g., open Linear and create tickets by hand).

## Expected Output Format

A structured response containing:
- Identified operation and its bucket classification
- Description of intended behavior
- Complete dry-run request payload (JSON)
- Expected success and error response shapes
- Fail-soft message with manual completion steps
- Stage 2 requirements for enabling live execution

## Quality Checklist

- [ ] Operation correctly classified as Bucket 3
- [ ] All required external dependencies listed
- [ ] Dry-run request payload conforms to frozen adapter contract
- [ ] `dry_run: true` is set on the first call — never skipped
- [ ] `context.cwd` uses relative path, not absolute
- [ ] Fail-soft message is present when service is unavailable
- [ ] No automatic retry or loop behavior
- [ ] Manual completion steps are provided
- [ ] Stage 2 requirements are documented
