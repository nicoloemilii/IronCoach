# Descriptive effort comparison: research and independent raw-stream audit

Research checked 2026-10-22. No production changes, personal database, credentials,
or provider requests were used in the accompanying synthetic parser audit.

## Three primary references for the physiological interpretation boundary

1. [TrainingPeaks official EF and aerobic-decoupling definition](https://help.trainingpeaks.com/hc/en-us/articles/204071724-Aerobic-Decoupling-Pw-Hr-and-Pa-HR-and-Efficiency-Factor-EF).
   Provider documentation updated March 2026. Its EF uses normalized power or
   normalized graded pace relative to heart rate, and compares that factor across
   halves of a sustained aerobic, steady effort. The prerequisites matter: a raw
   mean-power/mean-HR or speed/HR comparison across an arbitrary activity is not
   the documented TrainingPeaks calculation. Their published percentage guidance
   should not become an IronCoach fitness classification for a different formula.
2. [Gonzalez-Alonso et al., 1997, controlled experiment](https://pubmed.ncbi.nlm.nih.gov/9104860/),
   DOI 10.1152/jappl.1997.82.4.1229. The experiment separated dehydration and
   hyperthermia; both altered cardiovascular responses, including an increase in
   heart rate as stroke volume fell. This supports an environmental alternative
   to interpreting a changed power/HR ratio as changed fitness. The PubMed search
   abstract was available; the journal full-text endpoint returned 403 during this
   check. No claims beyond the abstract are used here.
3. [American Heart Association: beta blockers and exercise](https://www.heart.org/en/health-topics/consumer-healthcare/medication-information/how-do-beta-blocker-drugs-affect-exercise).
   Official clinical organization guidance, reviewed January 2024. Beta blockers
   slow heart rate and their exercise effect varies between individuals. This
   supports stating medication as an unobserved influence, without inferring that
   the athlete takes medication or giving medication advice.

Together these sources support a descriptive comparison and explicit limits;
they do not validate an automatic fitness diagnosis or a prescription from a
single arbitrary first/second-half ratio. This last conclusion is our inference.

## Suggested transparent calculation, if the product chooses to implement it

- Label it `Rapporto potenza/FC` or `Rapporto velocita/FC`, not a physiological
  score, measured aerobic efficiency, or a replica of TrainingPeaks EF.
- Use the raw time axis and the same valid paired intervals for both signals.
  For each elapsed-time half, compute time-weighted mean output and time-weighted
  mean heart rate, then divide output mean by HR mean. Do not average the ratio
  of each sample or combine independently selected timestamps.
- If showing a signed variation, document the denominator and direction, e.g.
  `100 * (ratio_second / ratio_first - 1)`. A positive result means the observed
  output/HR ratio increased, and says nothing on its own about a beneficial
  physiological change. If ratio_first is zero, variation is unavailable.
- The interpolation/hold convention between samples is a calculation assumption,
  not observed physiology. Require a positive finite time step and an explicit
  maximum gap; do not carry the last measurement across pauses or dropouts.
  State time represented by valid intervals and its fraction of each full half.
- Split at half of the selected elapsed-time window, not half of sample count or
  separately selected active durations. State the actual start/end: if recording
  starts at second 300, the comparison covers the recorded window, not the entire
  activity. Split intervals that cross the midpoint without inventing new samples.
- Keep observed zero power/speed as zero. HR zero is not a usable denominator.
  Missing values stay missing; no filling from summary averages or FTP estimates.
  Incomplete or truncated raw data must be visible in availability/coverage.
- Warm-up, intervals, terrain, wind, coasting, stops, changing workload, sensor
  errors, heat, hydration, medication and unknown conditions prevent attribution.
  Steady-state aerobic conditions are not established by this ratio alone.
- Any minimum duration/coverage/gap rules chosen by the application are technical
  eligibility choices, not a validated universal health or endurance boundary.
  Avoid a green/red 5% band and automatic training changes.

## Actual parser findings and reproductions

`review_streams.py` runs production `normalize_streams` on synthetic values;
`review_streams.json` records exact outputs and SHA256 of inspected sources.

1. Display-point extrema sampling cannot support statistical summaries. A
   10,000-point series with raw mean 126.55 W produced 595 chart points whose mean
   was 546.218 W. `web/charts.js` currently labels its display mean as sample mean;
   the data note mentions reduced display, but these are not all raw samples.
2. Power and HR are downsampled independently. In that same paired example, 294
   timestamps occurred only in the power chart and 294 only in the HR chart.
   Zipping normalized chart arrays would pair different moments.
3. Equal timestamps are accepted (`current < last`, not strict increase). They
   must not create double time weighting or inconsistent values at one instant.
4. Backward timestamps are dropped without adding a gap marker. The displayed
   polyline may bridge past the invalid sample, and analysis must not treat that
   segment as fully observed.
5. A timestamp jump from 1 to 900 seconds has no explicit null and is connected
   by the current chart. A derived average must not assign the full 899 seconds
   to one neighboring value without an explicit bounded assumption.
6. Short signal arrays are silently zipped against the time axis. Missing channel
   tails are not zero; analysis needs common interval coverage against the whole
   chosen window, separately for each half.
7. Trailing nulls remain in points, but chart bounds use first/last finite values.
   A series at times 0,1,2,100 with its last value missing displays through second
   2. A coverage denominator based only on finite endpoints hides the missing tail.
8. Duplicate stream types silently choose the last array. Conflicting duplicate
   time or signal channels require a documented choice or unavailable result.
9. Raw processing stops at 250,000 points without a distinct raw-truncation flag.
   Display downsampling is a different operation and must not conceal raw loss.
10. A numeric integer `10**400` causes `OverflowError` in `_number`; a stream row
    with `type: []` causes `TypeError` in dictionary construction. Fail closed on
    malformed input, without losing the activity's available summary.

The hazards are properties of the inspected baseline, not observations about any
athlete's data or a claim that production provider payloads contain these cases.
