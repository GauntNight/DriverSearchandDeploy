# Claude Reference — Intune Packaging Authority

Authoritative external references for **building and deploying packages via the
Microsoft Intune Graph API**. Consult these when adding catalog entries, wiring a
new installer family, or changing how the pipeline talks to Intune.

| File | Use it when you need… |
|---|---|
| `ch04-driver-updates-reference.md` | Driver update mechanics — update-ring toggle vs. Driver Update Profiles, Graph endpoints (`beta`), scope requirements, lifecycle gotchas. Relevant to `driver_update` jobs and the discovery/deployment agents. |
| `ch11-windows-app-packaging-reference.md` | Win32 app packaging — app-type matrix, `.intunewin` lifecycle, install context, **detection methods** (MSI product code, registry, file), supersedence relationships, Graph endpoints (`v1.0`). Relevant to `new_software` MSI/EXE jobs. |

Both are extracted from *Microsoft Intune Cookbook, 2nd Edition* (Andrew Taylor,
Packt, Feb 2026), chapters 4 and 11. They are generic Intune references, **not**
AutoPackager-specific docs — for how this project implements these concepts see
[`docs/PIPELINE_LIFECYCLE.md`](../PIPELINE_LIFECYCLE.md) and the catalog notes in
[`README.md`](../../README.md).
