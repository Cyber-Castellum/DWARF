# Property catalog

| Property | Type | Non-vacuity requirement | Evidence |
| --- | --- | --- | --- |
| Both targets receive SP4 traffic | Reachable | Both transcripts contain concrete injection records | Paired fuzzer logs |
| Equivalent prefault cases | Sometimes | Identical ordered common prefix covers all 24 protocol/class cells | `prefault.json` |
| Illegal sessions are contained | Always | Post-setup injections occur and later sessions remain usable | Counts plus target probes |
| Honest progress continues | Sometimes | p1 and isolated Amaru-fed consumer advance | Cardano CLI tips |
| Unrelated peers remain usable | Sometimes | p1/p2/p3 and consumer are queryable and consumer matches a producer | Cardano CLI tips |
| No fatal target termination | Always | Evidence is classifiable; known injected downtime is not called panic | Logs, state, restart counts |
| Both targets recover | Sometimes/eventually | Quiet-period target probes and honest tips advance beyond prefault baseline | Recovery command |
| Consumer converges | Sometimes/eventually | Amaru-fed consumer hash matches a producer after advancing | Recovery command |

