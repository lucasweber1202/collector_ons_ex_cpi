# Special aggregates contract

The product contains the ten exclusion indices listed in the README. Each crosswalk row retains six exact ONS identifiers: exclusion weight, index, published 12-month rate, complement weight, complement index and complement rate. This is a reviewed set of 60 CDIDs.

Table 38 supplies the ten forecast-target levels. MM23 supplies weights, supporting headline and complement levels, independent rate validation and previous versions. Both exclusion and complement weights are retained unchanged in `original_weights`; normalized operational shares live in `weights`. The native weight CDID and component relationship are preserved in `weight_component_crosswalk`. MM23 rates remain validation-only.

From 2017, January uses the final version superseded by the scheduled March publication and February–December uses the updated regime. Where ONS published MM23 twice in one March (2017), the last scheduled March snapshot is the January regime. Missing version evidence is a SOURCE GAP; the weight series themselves start in 1996, so earlier months have index levels and no weight regime.

See `METHODOLOGY.md` for storage semantics and `COMPLIANCE.md` for the evidence policy and pending connected gates.
