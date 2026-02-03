# LensPR

**LensPR makes Claude 3x more efficient by representing code as a graph.**

Instead of grep/read loops, Claude gets structured tools: one `lens_context` call returns function source + callers + callees + tests.

![LensPR Benchmark Summary](eval/results/chart_summary.png)

| Metric | Without LensPR | With LensPR | Improvement |
|--------|----------------|-------------|-------------|
| **Task Completion** | 33% (1/3) | 100% (3/3) | **+200%** |
| **Tokens Used** | 1.27M | 388K | **-70%** |
| **API Calls** | 84 | 38 | **-55%** |

---

> **This is a learning project.** I'm experimenting with LLM-assisted code development.
> Don't judge too harshly if something doesn't work!
>
> **Want to help?** I'm looking for contributors for JS/TS parser support.
> [Open an issue](https://github.com/kyrylopr/lenspr/issues) to discuss!

---

## Quick Start

```bash
pip install lenspr

# Initialize on your project
lenspr init ./my_project

# Check impact before changing code
lenspr impact ./my_project app.models.User
```

### With Claude Code (MCP)

```bash
pip install 'lenspr[mcp]'
lenspr setup    # Creates .mcp.json
lenspr init     # Builds the graph
# Restart VSCode - lens_* tools are now available
```

### Python API

```python
import lenspr

lenspr.init("./my_project")

# Get full context in one call
context = lenspr.handle_tool("lens_context", {"node_id": "app.utils.validate"})

# Check what breaks before changing
impact = lenspr.check_impact("app.models.User")
```

## How It Works

```
Source Files -> AST Parser -> Graph (SQLite) -> Tools (CLI / MCP / API)
```

LensPR parses Python into a directed graph (nodes = functions/classes, edges = calls/imports) and gives Claude structured tools to navigate and modify code safely.

**Key features:**
- **Impact analysis** before changes - know what breaks
- **27 tools** for navigation, analysis, modification
- **3-level validation** - syntax, structure, signature checks
- **Change history** with rollback capability

## Project Status

| Metric | Value |
|--------|-------|
| Tests | 154 passed |
| Graph Confidence | 87.6% |
| Python Support | Yes |
| JS/TS Support | Not yet (help wanted!) |

## Documentation

- [Tools Reference](docs/TOOLS.md) - all 27 tools explained
- [Architecture](docs/ARCHITECTURE.md) - how it works internally

<details>
<summary>Detailed benchmark results</summary>

![Task Completion](eval/results/chart_success.png)
![Token Usage](eval/results/chart_tokens.png)

| Task | Without | With | Status |
|------|---------|------|--------|
| Understand Function | 602K tokens | 131K tokens | Both passed |
| Find All Usages | 623K tokens | 137K tokens | With: passed, Without: failed |
| Safe Code Change | Rate limit | 121K tokens | With: passed, Without: failed |

Run yourself: `make benchmark`

</details>

## Known Limitations

- **Python only** - JS/TS/Go/Rust parsers not implemented yet
- **Dynamic code** - `getattr`, `eval()` can't be fully tracked
- **Large projects** - not tested on >10k files
- **Alpha stage** - some features incomplete

## Contributing

I especially welcome:
- **JS/TS parser** - `BaseParser` interface is ready
- **Bug reports** - even "this doesn't work" is helpful
- **Ideas** - what would make this useful for you?

## Installation Options

```bash
pip install lenspr           # Core only
pip install 'lenspr[mcp]'    # + MCP server for Claude
pip install 'lenspr[dev]'    # + dev tools
```

## License

MIT

---

**Questions?** [Open an issue](https://github.com/kyrylopr/lenspr/issues)
