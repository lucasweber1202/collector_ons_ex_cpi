# Special aggregates contract

The product contains the ten exclusion indices listed in the README. Each crosswalk row retains six exact ONS identifiers: exclusion weight, index, published 12-month rate, complement weight, complement index and complement rate. This is a reviewed set of 60 CDIDs.

Table 38 is the stored level source. MM23 supplies weights, independent rate validation and previous versions. Both exclusion and complement weights are retained unchanged in `original_weights`; rates and complement index levels are not duplicated in `time_series`.

From 2017, January uses the final version superseded by the scheduled March publication and February–December uses the updated regime. Missing or ambiguous version evidence is a SOURCE GAP.

See `METHODOLOGY.md` for storage semantics and `COMPLIANCE.md` for the evidence policy and pending connected gates.
