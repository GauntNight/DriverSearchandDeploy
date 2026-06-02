# Design History

Pre-release design artifacts, kept for context. **These describe the original
product vision — not the current implementation.**

| File | What it is |
|---|---|
| `PRFAQ_ Project AutoPackager.md` | Amazon-style press-release / FAQ written at project inception. Market positioning and phased roadmap. |
| `automated_software_packaging_whitepaper.md` | Technical whitepaper: market landscape, proposed architecture, core components. |

Both documents reference **Phase-2 capabilities that are not yet built** —
LLM-driven discovery, PSADT wrapping, AI-driven UAT, log analysis. The shipped
product (Phase 1) does catalog-based discovery, deterministic MSI/EXE packaging,
smoke + optional Hyper-V testing, and ringed Intune deployment with deterministic
supersedence.

For current, accurate capabilities see the top-level [`README.md`](../../README.md)
and [`CHANGELOG.md`](../../CHANGELOG.md). For how the pipeline actually works see
[`docs/PIPELINE_LIFECYCLE.md`](../PIPELINE_LIFECYCLE.md).
