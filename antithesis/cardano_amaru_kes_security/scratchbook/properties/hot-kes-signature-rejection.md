# Hot-KES signature rejection evidence

Amaru revision `a4f15e71cd16f1cef7175cbca49352c3fb9777bc` defines a KES signature as
448 bytes and validates the signature over the CBOR-encoded header body after
checking the operational-certificate period. Its explicit error is
`Invalid KES signature from leader`.

The local DWARF run `20260905T083615Z-26b9ac9c` delivered one otherwise-valid
header with only the final signature bit changed to dedicated Cardano and Amaru
victims. Both remained at the exact parent; Amaru emitted `Invalid KES
signature`. This proves feasibility, not Antithesis coverage.

The W34 report and current Amaru/Cardano issue searches contain no report for
this exact mixed live N2N differential. The property is therefore a new
campaign/regression target. It is not a claim that basic invalid-KES rejection
is a previously unknown requirement.

### Investigation Log

#### Is this already a reported mixed-net finding?

- Examined: W34 report, DWARF reports/docs, Amaru wiki and issues, Amaru source,
  Cardano-node issues, and cardano-node-antithesis issues.
- Found: KES/opcert implementation and unit work, but no live mixed
  signature-only differential under faults.
- Conclusion: retain as a unique scenario.
