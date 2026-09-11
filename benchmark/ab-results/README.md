# A/B results

Results of the treatment-vs-control comparison defined in
[`../ab-comparison.md`](../ab-comparison.md) — one file per case, plus a summary once more
than one case exists.

Empty until the first case is adjudicated. The protocol was committed before the control runs
were read, and results land here afterwards; that ordering is deliberate and is what lets a
reader check that the metrics were not chosen to fit the outcome.

Each case file must carry, for both arms: the repository and commit, the raw output, the
computed primary metrics (§3 of the protocol), and every adjudication decision with the
source fact that settled it.
