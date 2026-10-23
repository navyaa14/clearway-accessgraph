# ClearWay evaluation report

Generated 2026-10-23 11:25 UTC · pipeline `accessgraph-pipeline-2.1.0`

> Measured on procedurally rendered SYNTHETIC scenes with exact ground truth (single cardboard box, fixed porch, CPU). Not validated on real Ring footage; real-world accuracy is unknown.

## A. Randomised labelled set

300 analyses (100 rendered scenes × 3 household configurations), 28 borderline cases excluded from precision/recall.

| Config | n | Precision | Recall | Recall incl. review | False-clear rate | False-alarm rate | Unresolved | Clear-width MAE (cm) | Footprint IoU | p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| ALL | 300 | 0.975 | 1.0 | 1.0 | 0.0 | 0.0345 | 0.04 | 6.05 | 0.563 | 524.7 |
| hh_a/METRIC | 100 | 0.9118 | 1.0 | 1.0 | 0.0 | 0.0526 | 0.06 | 5.8 | 0.53 | 557.8 |
| hh_b/METRIC | 100 | 0.9808 | 1.0 | 1.0 | 0.0 | 0.0294 | 0.06 | 6.31 | 0.53 | 594.0 |
| hh_b/APPARENT | 100 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 | 0.0 | None | 0.628 | 478.9 |

### Confusion matrix (all configurations)

| truth \ predicted | POTENTIAL_OBSTRUCTION | NO_OBSTRUCTION_OBSERVED | REVIEW_REQUIRED | UNABLE_TO_VERIFY |
|---|---|---|---|---|
| obstructed | 156 | 0 | 0 | 0 |
| clear | 4 | 109 | 3 | 0 |
| borderline | 9 | 10 | 9 | 0 |

### Explicit failure cases

- scene 46 hh_a/METRIC: truth clear, predicted POTENTIAL_OBSTRUCTION (p=1.0), box {'x': 111.8, 'y': 273.7, 'w': 33.2, 'd': 46.8, 'h': 34.7, 'rot': 21.6}
- scene 68 hh_a/METRIC: truth clear, predicted POTENTIAL_OBSTRUCTION (p=1.0), box {'x': 97.0, 'y': 179.9, 'w': 53.6, 'd': 44.6, 'h': 43.1, 'rot': 18.8}
- scene 98 hh_a/METRIC: truth clear, predicted POTENTIAL_OBSTRUCTION (p=0.944), box {'x': 231.7, 'y': 171.2, 'w': 49.1, 'd': 34.2, 'h': 39.6, 'rot': -29.9}
- scene 98 hh_b/METRIC: truth clear, predicted POTENTIAL_OBSTRUCTION (p=1.0), box {'x': 231.7, 'y': 171.2, 'w': 49.1, 'd': 34.2, 'h': 39.6, 'rot': -29.9}

## B. Named scenarios

| # | Scenario | Expected | Observed | Result |
|---|---|---|---|---|
| 1 | Parcel outside the marked route | NO_OBSTRUCTION_OBSERVED | NO_OBSTRUCTION_OBSERVED | PASS |
| 2 | Parcel visibly overlapping the route | POTENTIAL_OBSTRUCTION | POTENTIAL_OBSTRUCTION | PASS |
| 3 | Identical frame, different household route/need | A: NO_OBSTRUCTION, B: POTENTIAL | A: NO_OBSTRUCTION_OBSERVED, B: POTENTIAL_OBSTRUCTION | PASS |
| 4 | Parcel partially moved but overlap remains | PARTIALLY_RESOLVED (not closed) | PARTIALLY_RESOLVED | PASS |
| 5 | Parcel repositioned into the delivery zone | RESOLVED (verified) | RESOLVED / VERIFIED_NO_OBSTRUCTION_OBSERVED | PASS |
| 6 | Camera view obscured | UNABLE_TO_VERIFY | UNABLE_TO_VERIFY | PASS |
| 7 | Low-light or blurry observation | UNABLE_TO_VERIFY (both) | low-light UNABLE_TO_VERIFY, blur UNABLE_TO_VERIFY | PASS |
| 8 | Camera displaced after calibration | UNABLE_TO_VERIFY (calibration DISPLACED) | UNABLE_TO_VERIFY (DISPLACED) | PASS |
| 9 | Object detected but ground contact unreliable | REVIEW_REQUIRED | REVIEW_REQUIRED | PASS |
| 10 | Duplicate event delivered twice | second delivery ignored | first processed=True, second duplicate=True | PASS |
| 11 | Old event arrives after a newer observation | STALE, state unchanged | STALE, PARTIALLY_RESOLVED | PASS |
| 12 | Assistance request without authorisation (incl. impersonation) | send 403, impersonated approval 403, nothing delivered | send 403, impersonated approve 403, inbox 0 | PASS |
| 13 | Ring stream/API unavailable (no credentials here) | explicit 503 for the live household, app keeps working | 503: Ring is not configured: set RING_ACCESS_TOKEN (or RING_REFRE | PASS |
| 14 | Uncertainty straddles decision threshold (true clear width = 91.0 cm = requirement) | REVIEW_REQUIRED | REVIEW_REQUIRED (p=0.744) | PASS |
| 15 | Valid vs invalid webhook signature | valid 200, invalid 401 | valid 200, invalid 401 | PASS |
| 16 | Replay with fabricated future timestamp vs live Ring incident | rejected (403), live incident stays open | upload 403, incident ASSESSED | PASS |
| 17 | Live closure needs two independent server-captured clear observations (separate sessions, time-separated) | open after 1st, RESOLVED after an independent 2nd | ASSESSED -> RESOLVED | PASS |
| 18 | Household B retention purge vs household A evidence | household A frame still served (200) | 200 | PASS |
| 19 | Credentials never accepted in media URLs | signed URLs only | signed only | PASS |
| 20 | Unexplained change on the route, no segmentable object | REVIEW_REQUIRED (not clear) | REVIEW_REQUIRED | PASS |
| 21 | No clear-scene reference (colour-prior fallback) | REVIEW_REQUIRED (advisory only) | REVIEW_REQUIRED | PASS |
| 22 | Second clear frame from the SAME capture session | does not close the live incident | ASSESSED | PASS |

## Definitions

- Precision = obstructions predicted that are truly obstructed / all predicted obstructions (non-borderline).
- Recall = truly obstructed cases predicted POTENTIAL_OBSTRUCTION; REVIEW_REQUIRED counts as a miss. 'Recall incl. review' counts it as flagged.
- False-clear rate = truly obstructed cases predicted NO_OBSTRUCTION_OBSERVED (the safety-critical error).
- Borderline = true clear width within ±6 cm of the requirement, or true footprint within 3 cm of the route (metric); 1–6% overlap (apparent).
- Clear-width error is measured only where the true footprint intersects the route.
