# Special aggregates contract

The product contains the ten exclusion indices listed in the README. Each crosswalk row retains six exact ONS identifiers: exclusion weight, index, published 12-month rate, complement weight, complement index and complement rate. This is a reviewed set of 60 CDIDs.

Table 38 is the stored level source. MM23 supplies weights, independent rate validation and previous versions. Both exclusion and complement weights are retained unchanged in `original_weights`; rates and complement index levels are not duplicated in `time_series`.

From 2017, January uses the final version superseded by the scheduled March publication and February–December uses the updated regime. Where ONS published MM23 twice in one March (2017), the last scheduled March snapshot is the January regime. Missing version evidence is a SOURCE GAP; the weight series themselves start in 1996, so earlier months have index levels and no weight regime.

See `METHODOLOGY.md` for storage semantics and `COMPLIANCE.md` for the evidence policy and pending connected gates.
