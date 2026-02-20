# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

This is a **documentation and research repository** analyzing 5 mainstream AI Coding Agent projects: Codex (Rust), Gemini CLI (TypeScript), Kimi CLI (Python), OpenCode (TypeScript), and SWE-agent (Python).

The repository contains:
- **docs/**: Core documentation comparing architecture and mechanisms across projects
- **{project}/**: Source code of each AI Coding Agent (for reference during documentation)
- **index.html**: Docsify configuration for GitHub Pages site (giraffe-tree.github.io/agent-base/)

## Documentation Structure

### Document Numbering System

All documentation uses a unified numbering scheme for cross-project comparison:

| Number | Topic | Priority | Location Pattern |
|--------|-------|----------|------------------|
| `01` | Overview | Required | `docs/{project}/01-*-overview.md` |
| `04` | Agent Loop | Core | `docs/{project}/04-*-agent-loop.md` |
| `06` | MCP Integration | Core | `docs/{project}/06-*-mcp-integration.md` |
| `07` | Memory Context | Core | `docs/{project}/07-*-memory-context.md` |
| `10` | Safety Control | As needed | `docs/{project}/10-*-safety-control.md` |
| `questions/` | Deep dives | As needed | `docs/{project}/questions/*-{topic}.md` |

### Key Documentation Files

- **Entry point**: `README.md` - Project overview and quick start
- **Cross-project comparison**: `docs/comm/*.md` - Abstract common patterns
- **Project-specific**: `docs/{codex,gemini-cli,kimi-cli,opencode,swe-agent}/*.md`
- **Sidebar config**: `_sidebar.md` - Docsify navigation structure

## Working with Documentation

### Adding New Documentation

When creating new documentation:

1. **Follow the existing numbering scheme** - Use consistent numbers for comparable topics across projects
2. **Include flowcharts early** - Place ASCII flowcharts before detailed code explanations for visual overview
3. **Structure per the template**:
   - Conclusion first (one-sentence summary)
   - Key code locations (file paths + responsibilities)
   - Flowcharts/data flow diagrams
   - Configuration/code examples
   - Design highlights

4. **Use consistent terminology**:
   - "Agent Loop" not "Main Loop" or "Event Loop"
   - "MCP" (Model Context Protocol) for external tool integration
   - "Checkpoint" for state rollback mechanisms

### Updating Cross-Project Comparison

When adding a new mechanism documentation to one project:

1. Check if `docs/comm/` has a corresponding comparison document
2. If yes, update the comparison to include the new project
3. If no, consider creating one following `docs/comm/comm-{mechanism}.md` pattern

### Evidence Markers

Use these markers when analyzing source code:

- **✅ Verified**: Conclusions based on read source code
- **⚠️ Inferred**: Reasonable assumptions from code structure
- **❓ Pending**: Hypotheses needing further verification

## Project Characteristics

Understand these key differences when comparing implementations:

| Project | Language | Key Architectural Feature | Best For Studying |
|---------|----------|--------------------------|-------------------|
| Codex | Rust | Native sandbox, CancellationToken | Enterprise security |
| Gemini CLI | TypeScript | Scheduler state machine, layered memory | UX/state management |
| Kimi CLI | Python | Checkpoint rollback, D-Mail | State persistence |
| OpenCode | TypeScript | resetTimeoutOnProgress, streaming | Long-running tasks |
| SWE-agent | Python | forward_with_handling(), autosubmit | Error recovery |

## GitHub Pages Site

The documentation site uses Docsify:

- **Local preview**: Open `index.html` directly (uses CDN)
- **Auto-deployment**: GitHub Pages deploys from `main` branch automatically
- **Search**: Enabled via docsify search plugin
- **Sidebar**: Auto-generated from `_sidebar.md`

No build step required - it's a static site.

## Source Code Subdirectories

The repository includes full source code of analyzed projects:

```
codex/           # OpenAI Codex CLI (Rust + TypeScript)
gemini-cli/      # Google Gemini CLI (TypeScript)
kimi-cli/        # Moonshot Kimi CLI (Python)
opencode/        # Anomaly OpenCode (TypeScript)
SWE-agent/       # SWE-agent (Python)
```

These are for reference during documentation writing. When citing code:
- Use relative paths like `codex/codex-rs/core/src/agent_loop.rs`
- Include version/date stamps (baseline: 2026-02-08)
- Note if code may have changed in newer versions

## Cursor Rules Reference

From `.cursor/rules/source-research-bootstrap.mdc`:

1. **Global before local**: Identify subprojects first, then dive into 1-2 relevant modules
2. **Exploration order**: README → architecture → entry → agent loop → tools → core abstractions → edge modules
3. **Output in Chinese**: Conclusion first, then evidence
4. **Always cite**: "project + file path + key responsibility"
5. **Compare dimensions**: Give at least 2 dimensions when comparing (e.g., loop mechanism, tool system, persistence)

## Research Scope Boundaries

- **Analysis only**: Do not modify source code in subdirectories (codex/, gemini-cli/, etc.)
- **Documentation focus**: Primary output is in docs/
- **Minimal changes**: When updating docs, make smallest necessary changes
- **Version awareness**: Note when documentation may be outdated relative to source

## Common Tasks

### Finding Related Documentation

```bash
# Find all documents about a specific mechanism
glob "docs/*/04-*-agent-loop.md"        # Agent Loop
glob "docs/*/06-*-mcp-integration.md"   # MCP
glob "docs/*/questions/*-checkpoint*.md" # Checkpoint

# Search across all docs
grep -r "CancellationToken" docs/
```

### Adding Questions Documentation

Place deep-dive analysis in `docs/{project}/questions/`:
- Filename pattern: `{project}-{topic}.md`
- Example: `docs/kimi-cli/questions/kimi-cli-checkpoint-implementation.md`
- Link from main docs when relevant
