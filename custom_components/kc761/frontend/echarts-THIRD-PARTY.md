# Third-party frontend dependency

The KC761 spectrum card vendors the official Apache ECharts distribution so
the card does not depend on a CDN or any external network at runtime.

- Package: `echarts`
- Version: `6.1.0`
- License: Apache-2.0
- Distribution: `dist/echarts.esm.min.js`
- npm registry integrity: `sha512-q0yaFPggC9FUdsWH4blavRWFmxdrIodbkoKNAjJudAI6CA9gNPxHtV2RcZNEepZVlk4yvBYkOkbk6HIVpIyHZA==`
- Vendored JavaScript SHA-256: `7208389ba18117a367f9393bd9cedece18a79ae052da30e050eaad9f4fad8c05`
- Source provenance is fixed by the package version, npm integrity value, and
  vendored JavaScript SHA-256 above; local acquisition metadata is not part of
  the public integration package.

The corresponding Apache ECharts `LICENSE` and `NOTICE` are distributed as
`echarts.LICENSE` and `echarts.NOTICE` beside the JavaScript file. The bundled
D3-derived license text is distributed as `licenses/LICENSE-d3`.
