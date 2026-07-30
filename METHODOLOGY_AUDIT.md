# Methodology Audit — SCREENER_SPEC.md

> Date: 2026-07-06. Produced by a multi-agent review: 7 specialist lenses
> (inflection engine, accounting, EDGAR data, scoring statistics, short side,
> long side, use-case fit) → dedup → one adversarial verifier per finding
> (several fact-checked against the **live** EDGAR companyfacts API) → a
> completeness critic. 38 findings survived verification: **9 critical,
> 29 important, 0 rejected.**
>
> Scope discipline: none of these challenge the accepted strategy choices
> (turnaround-first 55/45, permissive no-gates, distress-as-feature, free data
> only, financials included, snapshot not backtest). Every finding is either an
> unappreciated consequence of those choices or an unspecified mechanism the
> build would otherwise improvise.
>
> Severity key: **CRITICAL** = would produce wrong/garbage rankings, or a
> spec'd metric cannot be computed from the chosen data sources.
> **IMPORTANT** = materially degrades shortlist quality.

---

## Theme 1 — The "quarterly EDGAR series" doesn't exist as served (data layer)

### 1.1 [CRITICAL] Build a first-class "quarterlyizer" module — Q4 derivation, YTD differencing, restatement dedup, fiscal alignment
companyfacts does **not** serve discrete quarterly series for flow items (verified live against AAPL, CIK 320193):
- 10-Ks file only full-fiscal-year durations for income-statement items — **a standalone Q4 fact does not exist**; it must be derived as FY − (Q1+Q2+Q3), and the derived Q4 absorbs annual audit true-ups/impairments, making every 4th observation the noisiest.
- 10-Q cash-flow items are **YTD-only** (90/181/272-day durations observed; no 3-month CFO fact exists) — quarterly CFO/FCF/Sloan accruals require consecutive-YTD differencing (Q2 = H1−Q1, Q3 = 9M−H1, Q4 = FY−9M). The spec's quarterly FCF inflection input literally does not exist as served.
- The same (tag, period) appears in multiple filings (comparative columns, /A amendments) with different post-restatement values — 44 of 55 AAPL revenue periods appear in multiple filings. Naive parsing double-counts or mixes vintages, and the resulting spurious dips look exactly like the trough+recovery pattern the engine rewards.
- **Do NOT use `fy`/`fp` fields for period identification** — verified: they describe the *filing*, not the fact (a quarter ended 2025-03-29 appears with fy=2026 when re-reported in the FY2026 10-Q). Derive fiscal alignment from start/end dates + the company's fiscal year-end.

**Fix (insert between roadmap steps 3 and 4):** key facts by (taxonomy, tag, unit, start, end); dedupe keeping latest `filed` (tie-break accession number); restrict to 10-K/10-Q and /A forms; classify durations into ~90d (quarter), ~180d (H1 YTD), ~270d (9M YTD), ~360d (FY); derive Q4 and quarterly CFO as above; validate derived quarters sum back to FY within tolerance — on failure **null the quarter, never interpolate**, and require a minimum point count per slope window. Flag derived-Q4 points and down-weight them (or use Theil-Sen). Set a `restated` flag when the retained value differs >2–5% from the originally filed value (free forensic tell for the short side). Flag names whose latest quarterly fact is >~200 days old (delinquent filers are over-represented among distressed turnaround targets).

### 1.2 [CRITICAL] Per-concept XBRL tag-fallback ladders — otherwise gross margin silently vanishes for a large slice of the universe
GrossProfit is untagged by a substantial minority of filers; revenue spans Revenues / RevenueFromContractWithCustomerExcludingAssessedTax (or Including) / legacy SalesRevenueNet; COGS spans CostOfRevenue / CostOfGoodsAndServicesSold / CostOfGoodsSold + CostOfServices; SG&A is often split; total debt and interest expense need assembly from competing tags. A single-tag implementation silently drops gross margin — a metric in **both** long sub-scores — with no error surfaced.

**Fix:** add an ordered tag-ladder table to the data-layer section with derive-from-components rules (gross profit = GrossProfit, else revenue − COGS ladder; SG&A = SellingGeneralAndAdministrativeExpense, else SellingAndMarketingExpense + GeneralAndAdministrativeExpense; interest = InterestExpense → InterestExpenseNonoperating → InterestAndDebtExpense; debt = LongTermDebtNoncurrent + LongTermDebtCurrent (else LongTermDebt) + ShortTermBorrowings/DebtCurrent + finance leases). Make ladder selection **recency-aware and per-period** — stitch across the ~2018 ASC-606 tag switch when building 5-yr histories. Make a per-metric, per-sector data-coverage QA report a required deliverable of roadmap step 4. Also correct the spec's claim that companyfacts provides standardized line items "for every filer" — standardization is taxonomy-level, not concept-coverage-level.

### 1.3 [CRITICAL] Foreign private issuers (20-F/40-F, IFRS) have no quarterly data and no us-gaap tags — the inflection engine silently fails on them
A $1–20B US-listings universe contains hundreds of ADRs/FPIs filing annually under ifrs-full; 6-Ks are not structured XBRL. No quarterly series exists (engine can't run) and us-gaap lookups return nothing; non-USD facts mixed with USD market caps produce garbage EV/Sales.

**Fix:** classify filer type from the EDGAR **submissions JSON** form history (presence of 20-F/40-F, absence of 10-Q → `annual_only` tag; taxonomy ifrs-full → `ifrs`). Don't infer from absence of us-gaap facts alone (some FPIs carry vestigial us-gaap tags; ASML reports full US GAAP yet is annual-only). Run an **annual-frequency inflection variant** for them (slope over last 3–5 FYs, trough within 2 FYs) over a small ifrs-full ladder; check the `unit` key on every fact and FX-convert via yfinance (EURUSD=X etc.) or skip the ratio — never mix currencies silently. Cap the weight of yfinance-only signals for annual_only names so ADRs can't rank on momentum alone.

### 1.4 [CRITICAL] Universe builder as specced will hit Yahoo rate limits and silently drop names
Market cap lives only in per-ticker yfinance quoteSummary calls; at ~10k EDGAR tickers Yahoo 429s and the first pipeline stage silently drops a random subset — the worst failure for a "never under-surface" design.

**Fix:** shares outstanding from EDGAR (dei:EntityCommonStockSharesOutstanding → us-gaap:CommonStockSharesOutstanding/Issued → weighted-average diluted); last close from chunked `yf.download(tickers, period='5d')` (~500–1,000 per chunk, retries) — the chart endpoint is far more rate-tolerant than `.info`; dedupe by CIK **before** fetching prices; widen the build-time cap band (~$0.8B–$25B) because dei shares are up to a quarter stale, then re-check caps with per-ticker `fast_info` only for the final ~100 names. For fundamentals prefer the **nightly EDGAR bulk companyfacts.zip (~1.4 GB, verified)** over ~8k API calls.

### 1.5 [IMPORTANT] Universe entity hygiene — share classes, warrants/units, funds, shells
company_tickers.json is per-ticker, not per-company: multi-class listings duplicate rows; SPAC units/warrants, CEFs, BDCs, royalty trusts and shells pass the cap band with empty companyfacts.

**Fix:** dedupe to one row per CIK keeping the first-listed ticker (verified: the primary class lists first — GOOGL, BRK-B, LEN); use company_tickers_exchange.json to drop the 2,558 OTC rows (consistent with "US listings"); regex-drop derivative suffixes (-WT, -WS, -U, -UN, -RT, -PA…); detect non-operating entities **structurally** (no us-gaap taxonomy in companyfacts, or recent forms are N-CSR/N-PORT/N-2 instead of 10-K/10-Q) — do NOT rely on SIC (verified blank for NUV/EVV/ARCC). Don't compute cap from dei shares for multi-class names (absent for GOOGL, stale for BRK) — use the primary ticker's yfinance marketCap.

### 1.6 [IMPORTANT] No as-of / staleness policy — windows are anchored to each filer's last data point, not calendar time
Filers' latest quarters differ by 3+ months routinely, 6–12+ for delinquents — concentrated in exactly the distressed cohort the long screen targets. A name that went dark after a trough-shaped quarter carries a frozen "recent trough + rising slope" signature indefinitely.

**Fix:** define the run's as-of date; evaluate "trough within 4–6 quarters" and slopes against **calendar quarters back from the run date** (missing recent quarters count as elapsed time); apply a soft multiplicative staleness decay once last-period-end is older than ~135–150 days (no compliant filer is penalized); emit data-as-of / staleness-days columns and an NT-filing tag (staleness doubles as a forensic tell).

---

## Theme 2 — Inflection engine construction (the core technical piece)

### 2.1 [CRITICAL] On raw quarterly series the engine measures seasonality, not inflection — run it on TTM and YoY-differenced series
Revenue, margins, NI and FCF are strongly seasonal across much of this cap band. A 3–6q window covers a non-integer number of seasonal cycles, so the slope mostly measures which fiscal quarters landed in the window, and the trough finder "finds" the seasonal low every year — concentrated in exactly the simple-business sectors (retail, consumer, industrials) judges like. Quarterly FCF is additionally dominated by working-capital timing.

**Fix:** build two derived series per metric: (a) **TTM** (rolling 4-quarter sums; TTM-numerator/TTM-denominator for ratios) — compute **level** and **trough** on TTM (a seasonal low can't register as a bottom; re-tune the trough window on TTM since TTM troughs lag ~1–2 quarters); (b) **YoY-delta** (Qt − Qt−4, fiscal-aligned) — compute the **slope** on this (seasonality-free, timelier than TTM slope, and slope-of-first-differences is literally the second derivative the spec wants). For FCF: never fit slopes to discrete quarters; use TTM FCF level/trough and YoY change in TTM FCF margin as the slope. Keep raw-quarterly as a tunable option; make TTM/YoY the default.

### 2.2 [CRITICAL] Slopes are not comparable across metrics/companies and blow up on negative bases — specify robust, unitless slope scoring
OLS on 3–6 points has huge standard error and single-point leverage; NI/FCF slopes come out in dollars/quarter (size-biased, incomparable with margin slopes); percent-change normalization is undefined/sign-flipped near zero or negative bases — exactly the depressed regime this screener targets.

**Fix:** (a) convert dollar metrics to margins first (NI/revenue, FCF/revenue in pp, TTM revenue denominator floored at a small fraction of assets); (b) fit on TTM or YoY-delta series; (c) Theil-Sen estimator with the 5–6q window primary, blended with a **sign-consistency count** (fraction of positive YoY deltas); (d) make it unitless by dividing by the MAD of the company's own historical quarterly changes (floored/blended with the cross-sectional median MAD), clip at ±3 — or, minimal-delta alternative: score the current slope as a **percentile within the company's own history of rolling same-window slopes**, mirroring the level component's treatment.

### 2.3 [IMPORTANT] Trough recency as specced fires on names still in freefall — require an actual bounce, smooth the recency score
"Did it bottom within 4–6 quarters?" is satisfied when the *latest* quarter is the minimum — i.e. a falling knife earns the trough point, the single worst failure mode for a turnaround screener. The boolean also creates cliff effects at the shortlist boundary.

**Fix:** trough = argmin of the TTM series over a trailing 8–12q reference window; contributes only if it occurred ≥2 quarters before the latest observation **and** cumulative recovery since exceeds ~1× the MAD of historical quarterly changes; score recency as a smooth ramp (e.g. max(0, 1 − q_since/8)) scaled by recovery magnitude capped at ~3 MADs. Anchor the slope fit at the trough only when ≥3 post-trough points exist. Mirror the same construction as **peak recency** for the short side's "peaked & rolling over" row — one tested primitive for both sides.

### 2.4 [IMPORTANT] Combine level + slope + trough conjunctively within each metric, not additively
Additive combination lets two strong components carry a missing third: high-and-rising names (momentum, no runway) score on slope+trough; deeply depressed non-recovering names (value traps) score on level+trough.

**Fix:** scale each component to [0,1] and combine as a **geometric mean with a per-component floor of ~0.1–0.15** (soft, not a gate); combine *across* the five metrics additively as before. Requires 2.3's bounce definition or a monotonically declining series still collects geomean(1, 1, floor) ≈ 0.5.

### 2.5 [IMPORTANT] Level percentile on only 12–20 quarters is cycle-blind — use the full companyfacts history
12 observations give ~8-point granularity, and a window that spans only the post-COVID boom shows normal margins as "depressed". companyfacts holds 10+ years (verified: 59 quarterly periods for CROX in the same JSON — zero extra API calls).

**Fix:** compute the **level** percentile on the full available TTM history (optionally capped ~40 quarters), keeping the resolved slope (3–6q) and trough (4–6q) windows unchanged; replace hard minimum-history cutoffs with smooth shrinkage toward neutral 50 by n/(n+k), k≈8; apply the same full-history percentile to the short side's "peaked" level; hold current WACC constant across the ROIC−WACC lookback.

### 2.6 [IMPORTANT] One-off items manufacture fake inflections — outlier detection, margin-led weighting, breadth term
Impairments/restructuring/settlements/valuation-allowance releases mechanically create the deep-trough-then-recovery shape; inventory liquidation boosts FCF precisely while the business shrinks; a one-time gain creates a false "peak" on the short side.

**Fix (all soft):** (a) breadth term — final inflection = mean metric score × sqrt(breadth/5); (b) weight margin metrics above bottom-line ones (e.g. GM 30 / OM 25 / ROIC−WACC 15 / NI 15 / FCF 15); (c) prefer OperatingIncomeLoss and income-from-continuing-ops over NetIncomeLoss; pull a broad one-off tag list (GoodwillImpairmentLoss, AssetImpairmentCharges, RestructuringCharges, GainLossOnDispositionOfAssets(+1), LitigationSettlementExpense, IncomeTaxReconciliation…ValuationAllowance, …) as a best-effort "core" series — display + secondary score, never a filter; (d) **highest-value single fix, no tags needed:** robust outlier detection (k MADs from rolling median) — if the trough quarter is a flagged outlier, damp the trough component and print an "outlier trough" tear-sheet flag; (e) 5-yr peak = median of top-3 historical quarters, not max.

### 2.7 [IMPORTANT] No minimum-history / structural-break policy — IPOs, de-SPACs, spinoffs get degenerate own-history scores
De-SPAC series splice near-zero shell quarters onto operating quarters and read as monster fake inflections; a "5-yr peak" silently becomes a 2-yr peak.

**Fix:** per-signal minimum observation counts as soft confidence multipliers (level percentile ≥12 obs, any 5-yr construct ≥16; below, shrink weight linearly — never gate). Count per metric, not per company (verified: DJT's NI series starts 2021, revenue 2022). De-SPAC detection: (a) EDGAR submissions `formerNames` matching /acquisition (corp|co)/i — near-perfect free flag; (b) >20× magnitude jumps near series start on NI and assets; (c) on conflicting duplicate facts keep the latest-filed. Start own-history windows at the first post-break quarter.

---

## Theme 3 — Scoring, aggregation, and the financials problem

### 3.1 [CRITICAL] No missing-data policy — with no hard gates, null-handling silently determines the ranking
Missingness is pervasive, not an edge case (GrossProfit untagged for many; banks lack COGS/inventory/classified balance sheets; DIO undefined for software; SI fields spotty). Missing→0 crushes sparse-XBRL names (a de facto gate, biased against the small end of the core band); missing→neutral clusters financials mid-pack on meaningless metrics. A bank scored on 4 metrics and an industrial on 11 are not comparable, and a ranked CSV hides it.

**Fix:** (1) derive before declaring missing (tag ladders); (2) missing/inapplicable = **excluded, with within-bucket weights renormalized** over computed metrics — never zero- or neutral-fill; multi-input composites rescale over applicable components; (3) sector-driven applicability rules (mask GM/DIO/current-ratio for banks; DIO only when inventory material; DSO/DIO expansion measured YoY same-fiscal-quarter); (4) guard thin-basis renormalization: per-name **coverage columns** (% of intended weight computed, per bucket), "low-confidence" tag below ~60%, and shrink bucket scores toward the universe median in proportion to missing weight; (5) list missing metrics by name on each tear sheet.

### 3.2 [IMPORTANT] Raw-metric→score mapping unspecified — un-normalized level metrics turn the moat bucket into a sector bet
GM level is industry-structural (software 70–90% vs retail 15–30%): universe-wide ranking of "GM high+stable" just ranks sectors; the composite literally cannot be computed without this decision.

**Fix:** one explicit rule — all cross-sectional inputs scored as **percentile ranks vs the current snapshot universe** (outlier-robust, no winsorization needed); composite = weighted average of ranks. Industry-structural level metrics (GM level/stability, margin-vs-industry) rank **within coarse sector buckets** (fallback universe-wide when a bucket has <15 names; SIC via EDGAR submissions JSON — companyfacts does NOT carry SIC). Written exceptions: ROIC−WACC has a meaningful absolute zero — blend an is-the-spread-positive component with the sector-relative rank; EV/Sales richness universe-wide vs sector-relative is a thesis choice — pick one and document; leave alone what's already self-normalized (inflection engine, vs-own-peak, drawdown, Piotroski/Beneish absolute scales).

### 3.3 [IMPORTANT] Within-bucket weights unspecified while inputs are heavily correlated — the short composite degenerates toward an accruals screen
Piotroski internally contains dROA, CFO>0, accruals, dGM — overlapping three other turnaround rows, so equal weights give the profitability theme ~4/7 of the bucket by accident. Short side is worse: accruals appears ~3× (false-moat row, Sloan, Beneish TATA) and Beneish's DSRI *is* the DSO row.

**Fix:** weight at the **theme level**, averaging correlated metrics (as rank percentiles) within themes. Long turnaround starting point: profitability-inflection ~50%, F-Score-rising ~15%, valuation reset (drawdown) ~20%, sentiment/squeeze ~15% (user tunes). Short: compute Sloan accruals exactly once inside a single forensic theme {Beneish intact, DSO/DIO expansion, Sloan}; replace the "leverage/accruals" false-moat row with an actual leverage-propping measure (ROE−ROA gap widening, or debt/EBITDA rising while ROIC falls). After the first run, compute the Spearman correlation matrix of all inputs and merge/down-weight pairs >0.8.

### 3.4 [IMPORTANT] For banks/insurers/REITs most of the metric library is structurally incomputable — operationalize the sector tag with substitute metrics
Verified live: KeyCorp has no GrossProfit/COGS, no AssetsCurrent/LiabilitiesCurrent, no OperatingIncomeLoss; **Kd = InterestExpense/debt ≈ 39%** for KEY because deposit interest is in the numerator while $149B of deposits are outside debt tags — the WACC formula is provably wrong for banks, not merely noisy. REITs: depreciation makes NI inflection misleading (FFO is standard; FundsFromOperations absent from companyfacts) and structural leverage puts the whole sector in the Z distress zone, mis-firing the "distress + inflection = amplifier" tag.

**Fix:** banks — skip WACC/ROIC, use **ROE − Ke** on both inflection and moat rows; efficiency ratio (NoninterestExpense / (InterestIncomeExpenseNet + NoninterestIncome), all tags verified) replaces op-margin-vs-peak; drop 2 F-Score components and renormalize to 7. Insurers — ROE − Ke plus loss-ratio proxies where tagged. REITs — FFO proxy = NI + D&A − gains (fallback chain on gain tags, else omit gains); replace the Z tag with interest coverage and debt/EBITDA. Globally: never impute zero for absent tags; renormalize; emit coverage %. Rank financials/REITs in their own CSV section. Minimum viable v1: the global policy + segmented output + the bank ROE−Ke swap.

### 3.5 [CRITICAL] ROIC is undefined, and ROIC/WACC/Kd/tax-rate constructions blow up precisely on the distressed cohort the screener targets
Effective tax rate explodes/flips sign when pretax income ≤ 0 (the normal state of the target cohort); invested capital crosses zero for buyback-heavy names (ROIC sign-flips); Kd divides by near-zero debt; negative equity flips E/V; betas are noisiest right after the crash the drawdown row selects for.

**Fix:** NOPAT = TTM OperatingIncomeLoss × (1−t), t = TTM tax/TTM pretax clamped [0%, 30%], statutory ~21–25% fallback when pretax ≤ 0. IC = total debt (excl. operating-lease tags) + equity − cash, goodwill-inclusive for the moat test. Guard: if |IC| < 10% of assets, substitute EBIT/Assets and flag; winsorize quarterly ROIC to ~[−50%, +50%] and the spread at 1st/99th percentiles. Kd: tag fallback chain; compute only when debt > ~2% of assets else D/V≈0; clamp to [Rf+1%, Rf+8%]. Tear-sheet flag whenever any clamp/fallback fired.

### 3.6 [IMPORTANT] WACC has no time dimension — "ROIC−WACC inflecting" and the 5-yr moat spread are undefined as spec'd
The CAPM build yields one current scalar; two rows consume WACC as a history. Under constant WACC the inflection row is *mathematically identical* to plain ROIC inflection (all three components are shift-invariant) — fine, but say so; and a current WACC against 2021–22 ROIC understates historical spreads (10-yr Treasury ~1.5% → ~4.5% across the lookback).

**Fix:** (a) re-spec the turnaround row honestly: ROIC inflection + a snapshot "current ROIC−WACC < 0" boolean tag; (b) for the 5-yr moat spread, vary at least the risk-free rate by year (^TNX history via yfinance, or FRED's keyless fredgraph.csv?id=DGS10 — verified working), holding beta/ERP constant; (c) if skipped, relabel the row "5-yr avg ROIC minus current WACC" and score cross-sectionally by percentile, not by sign.

---

## Theme 4 — Long composite

### 4.1 [CRITICAL] No revenue trajectory anywhere in the turnaround bucket — cost-cutting melting ice cubes will cluster at the top
All five inflection metrics (ROIC−WACC, GM, OM, NI, FCF) co-move under cost cuts/layoffs/capex starvation/divestitures — the screener can't distinguish "demand recovering" from "shrinking gracefully." A cost-cutting harvest story is exactly the pitch judges dismantle ("where does growth come from?").

**Fix:** add **revenue as a sixth inflection input** (tag ladder; financials fall back to RevenuesNetOfInterestExpense / InterestAndDividendIncomeOperating). Separately compute a revenue-context classifier (growing / stabilizing / still-declining) and apply a ~0.5–0.7× haircut **only to the margin/earnings/FCF inflection components** when still-declining (drawdown, SI, F-Score aren't cost-cut artifacts; revenue's own score already penalizes decliners — avoid double-counting). Add a "cost-cut inflection" tear-sheet tag and always display the quarterly revenue series.

### 4.2 [IMPORTANT] Turnaround and moat buckets score the same metrics in opposite directions — the composite under-ranks the fallen-angel archetype
GM and ROIC−WACC appear in both buckets with opposite desired signs, and the trailing 5-yr moat average/stdev include the very trough quarters the turnaround bucket hunts — the strong-franchise-that-stumbled is systematically dragged toward mid-pack on 45% of the composite for the exact feature the other 55% rewards.

**Fix:** compute the moat level as the **best rolling 12-consecutive-quarter average within the trailing ~7 years ("demonstrated economics")** — needs no trough-dating, degrades gracefully on short histories; GM stability on that same window (or robust dispersion excluding the trailing 6–8 quarters). Keep the trailing-5-yr version **as a second column**: the spread between demonstrated and trailing moat is itself the fallen-angel signature and a great sort key/tear-sheet contrast. Guardrail: a demonstrated-peak window will flag COVID-spike/cyclical-top names as proven moats — surface the name's short-side false-moat flags on the long tear sheet so review catches the collision. Always emit Turnaround and Moat sub-scores as separate CSV columns.

### 4.3 [IMPORTANT] "Op margin vs own 5-yr peak" has no cyclical-vs-structural discriminator — a value-trap magnet for secular decliners
Legacy media/wireline/mall retail look permanently "depressed with runway," and periodic restructuring bounces pass the slope test too.

**Fix (tags, not gates):** (1) **decline-shape modifier** on TTM op margin — sharp drop within ~6–10 quarters after a multi-year plateau = "stumble" (high credit); monotonic multi-year erosion = "erosion" (near zero); apply as a multiplier on this row and a tear-sheet tag. (2) **Sector-relative check** — compare the name's margin change since peak to its sector-group median (reuses the short side's peer machinery); if the industry declined in lockstep, tag "secular/industry decline"; require ≥8 peers else "no-peer-data." Feed a "stumble vs erosion" field on the tear sheet — it's the first manual-research question.

### 4.4 [IMPORTANT] Price-drawdown row: 52-wk window too short, the 30–50% band punishes deep distress, and it fights the recovery the engine selects for
A stock down 70% eighteen months ago that has based since — the archetypal setup — shows a small 52-wk drawdown; a literal band-pass scores down-55–80% names *worse* than down-45% (contradicts distress-as-feature); and as price recovers, the drawdown shrinks — the row fights the fundamental rows on the best-timed names.

**Fix:** (1) drawdown from the **2–3 year high** (lookback must be ≥ trough window + typical decline duration; yfinance period='3y' is free); (2) replace the band with a **monotone-then-plateau kernel** (~0 below 20%, full credit by ~35%, plateau through ~80%; taper beyond 80% optional and gentle — the $1B floor already excludes most terminal names); (3) add a small "price confirming" term (% above the 2–3 yr low or positive 3-month slope) so a name curling off its bottom gains points; (4) process rule: document the metric→score kernel shape for **every** row in both composites before coding — "no hard gates" doesn't protect against a mis-shaped soft kernel.

### 4.5 [IMPORTANT] Distress-as-feature needs a survivability context tag (runway, maturities, dilution)
Altman Z answers "is it distressed?", not "can it survive to the inflection?" A turnaround that files Ch. 11 or triples its share count first isn't pitchable.

**Fix (zero score weight, tear-sheet traffic-light):** (1) cash runway (quarters) = (cash + ST investments) / trailing-4q FCF burn, only when burn < 0, floored denominator; (2) near-term debt vs cash — the debt-maturity footnote **is** XBRL-tagged for many filers (verified: LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths present for PTON, CVNA), fallback to DebtCurrent tags, "n/a" when nothing resolves; (3) dilution trajectory = 2-yr growth in diluted WAS, flag >15%. Render "distressed but funded" as the narrative amplifier; "distressed, <4q runway, near-term debt > cash" as "check the financing story first." Suppress runway for financials.

### 4.6 [IMPORTANT] Long composite has no valuation input — fully re-rated recoveries score maximum
A name whose margins inflected six quarters ago and already tripled can still max every turnaround row while trading at peak multiples — a dead pitch that wastes a research slot (judges expect upside to a target).

**Fix:** one low-weight (~5–10%) soft cheapness row: **EV/Sales percentile vs the screened universe (or sector bucket)** — EV components are already required by the WACC build. Use EV/Sales, not EV/GP (gross profit is depressed at the inflection — EV/GP flags the best setups as rich). P/TBV under the financials tag. Strictly soft; its job is breaking ties against fully-priced recoveries.

### 4.7 [IMPORTANT] "Insider buying / short interest" row: insider data has NO source in the chosen data layer; short interest needs definition + staleness + a double-use rule
companyfacts contains zero Form 4 data (financial-statement XBRL only) and yfinance is scoped "market data only" — the insider metric is unbuildable as spec'd. And as written, the same elevated SI adds points to BOTH composites (long squeeze setup + short confirmation), boosting the same names onto both shortlists with zero discriminating power.

**Fix:** split the row. **Short interest** (source already in scope): sharesShort / sharesOutstanding (shortPercentOfFloat is frequently None for small caps — display only), stamped with dateShortInterest (bi-monthly FINRA print, ~2-week lag); long side multiplies the SI contribution by the turnaround-inflection sub-score (squeeze points accrue only in proportion to turnaround evidence); short side gets a hump-shaped contribution (rising to moderate elevation, flat-to-negative when crowded; breakpoints tunable); emit an "appears on both lists" flag. **Insider buying:** remove from the universe-wide table; demote to shortlist enrichment via yfinance insider_purchases/netSharePurchaseActivity for the top ~100 only (nullable); roadmap upgrade = SEC quarterly Insider Transactions Data Sets or Form 4 XML parsing (code 'P' net of 'S'). Renormalize the turnaround sub-weights accordingly.

### 4.8 [IMPORTANT] Piotroski F-Score: annual construct, "rising" undefined, two components need proxies
The nine signals are defined on annual comparisons — an annual F-Score changes once a year and lags the 3–6q window it's supposed to corroborate.

**Fix:** define a **TTM F-Score updated quarterly** (flow signals on trailing-4q sums; stock signals latest balance vs 4-quarters-ago); "rising" = F(t) − F(t−4q), scored continuously. Issuance signal: shares-outstanding YoY growth with a >2% threshold (routine SBC passes; distressed raises fail). For financials, drop signals whose inputs don't exist (verified: JPM lacks AssetsCurrent and GrossProfit) and rescale as 9 × mean(available signals).

---

## Theme 5 — Short composite

### 5.1 [IMPORTANT] "Revenue growth decelerating off a high base" is undefined — no growth measure, base threshold, or M&A handling
Sequential growth is seasonality-corrupted, and acquisition-inflated revenue that laps produces fake "deceleration" — the classic pitch-killer when the decelerating hypergrower is just cycling an acquisition.

**Fix:** YoY revenue growth per quarter over the last 8–12 quarters; "high base" = peak YoY growth in last 8 quarters ≥ ~20–25% (or top quintile of universe peaks); "deceleration" = latest YoY well below peak AND negative slope of the YoY-growth series over 3–4 quarters — the inflection engine sign-inverted (this also gives the "ROIC/margins peaked & rolling over" row a definition for free). **M&A tear-sheet flag:** Goodwill up >~20% YoY or PaymentsToAcquireBusinessesNetOfCashAcquired > ~5% of TTM revenue. Keep revenue-growth-outpacing-gross-profit-growth as a separate revenue-quality flag.

### 5.2 [IMPORTANT] Beneish M-Score and Sloan accruals: unspecified constructions with known input gaps and applicability limits
DEPI needs depreciation separate from amortization (often lumped); SGAI needs an SG&A tag many filers split; the model excluded financials and SGI mechanically flags every legitimate fast grower. Balance-sheet accruals are corrupted by M&A/divestitures/FX (Hribar & Collins) — endemic in this cap band.

**Fix:** Sloan accruals = **(TTM NI − TTM CFO) / avg assets** (cash-flow method; all inputs near-universal), reused for the false-moat accruals line so both entries share one definition. Beneish: continuous M-Score percentile-ranked (not a −2.22 binary), fiscal-year-over-fiscal-year; component fallbacks (DEPI: `Depreciation` tag else neutral 1.0; SGAI: SG&A ladder else 1.0; GMI: GP ladder else 1.0); winsorize component indices [0.1, 10]; require ≥6 of 8 computable else neutral; **suppress entirely for financials/REITs** (out-of-model); show the 8 components on the tear sheet flagging when SGI dominates.

### 5.3 [IMPORTANT] Industry margin benchmark has no data mechanism, and "without structural reason" is not screenable
Neither companyfacts nor the yfinance market-data slice provides industry classification; and if the row silently degrades to "high margin vs industry" it flags every genuinely great business — signal inversion against the long side's moat score.

**Fix:** SIC from the EDGAR **submissions JSON** (sic/sicDescription verified; same host/rate limit; also supplies the financials/REIT tag the Universe section requires but never sources). Group at 3-digit SIC, fallback 2-digit when <~8 members. Peer-median GM/OM per group (GP tag ladder), excluding SIC 6xxx from margin peers. **Make margin-vs-peer an interaction term:** it contributes only when combined with the company's own deteriorating trend (inflection engine inverted: high own-history percentile + negative slope + recent peak) or an active forensic flag; unconditioned high margin contributes zero. "Structural reason?" becomes a manual tear-sheet checklist line.

### 5.4 [IMPORTANT] PEG is effectively not computable for this universe — resolve the "EV/Sales or PEG" ambiguity to EV/Sales
yfinance's pegRatio has been broken since ~June 2025 (GitHub issue #2570, verified); long-term growth estimates are sparse for small caps; and PEG is undefined for negative trailing EPS — i.e. for exactly the unprofitable hypergrowth names the short screen targets. Neutral-filled PEG makes analyst coverage a hidden ranking variable that exempts the best shorts.

**Fix:** EV/Sales as the primary richness metric (yfinance enterpriseValue, fallback mktcap + EDGAR debt − cash, over EDGAR TTM revenue), percentile within coarse peer group. Never neutral-fill PEG into the composite; keep trailingPegRatio as an explicitly-sparse tear-sheet column only.

### 5.5 [IMPORTANT] Zero-revenue cohort degenerates the short ranker — EV/Sales is infinite for pre-revenue biotech
Clinical-stage biotech/exploration miners/early de-SPACs max the richness row and coast on missing-data defaults; they're terrible pitch shorts (no false-moat narrative, binary readout risk, fail the simple-business criterion).

**Fix:** zero/low-revenue tag (TTM revenue < $10M or revenue/mktcap < ~1%); for tagged names **null all sales-denominated short metrics** (EV/Sales, deceleration, margins, DSO/DIO, Beneish — DSRI/GMI/SGI/SGAI divide by sales), renormalize, emit coverage % (else the degeneracy just migrates to the SI row); keep them in the CSV in their own segmented section. Apply the same nulling on the long side for consistency.

### 5.6 [IMPORTANT] Missing cheap, high-signal EDGAR short tells: dilution, debt-funded buybacks, NT filings, auditor changes
For a judge-facing pitch, "they filed an NT 10-K and changed auditors" is far more persuasive than an M-Score decimal — and all of it is free.

**Fix:** (1) dilution = YoY growth in split-adjusted diluted WAS (split-adjust via yfinance Ticker.splits or a 2:1 split reads as 100% dilution) — continuous short input, plus optional long-side turnaround-quality tag; (2) debt-funded buybacks = PaymentsForRepurchaseOfCommonStock > (OCF − capex) while total debt rises — belongs under "inflated growth"; (3) late-filer flag = forms starting "NT 10" in the submissions JSON (last ~2–3 years); (4) 8-K items **4.01** (auditor change — also fires on benign rotations; review prompt, not proof) and **4.02** (non-reliance/restatement — higher signal). Items 3–4 are rare events: tear-sheet red-flag lines + small soft bump, not heavy composite weights.

---

## Theme 6 — Output, shortlist, and the tuning loop

### 6.1 [IMPORTANT] No sector-relative control or diversity mechanism — cyclical waves flood both shortlists at cycle turns
At an industry trough every energy/materials/semi/shipping name exhibits the exact long fingerprint simultaneously — a 30-name shortlist becomes 25 correlated plays on one commodity cycle ("that's just the cycle" dismantles the pitch). Mirrored on the short side at cycle tops.

**Fix (v1, cheap):** (a) soft per-sector cap at shortlist assembly (~5–6 per side, overflow spills to next-ranked other-sector names, capped names still visible in the top-100 CSV); (b) sector-crowding diagnostic (same-sector count in top 50); (c) two tags: "sector-driven" (name's inflection matches its sector-group median move) and "cyclical" (reuse the moat bucket's margin-volatility calc — high 10-yr margin volatility = cyclical; don't build a peak-trough episode counter). Group at 2-digit SIC / ownerOrg / yfinance sector — 4-digit groups are too thin in this band. Short side: partial haircut when the sector median margin is also declining (not a sharp downweight — preserve best-short-in-a-bad-sector). Sector-relative score blend (e.g. 60/40 own/sector) as a v1.1 toggle, compared on/off before locking.

### 6.2 [IMPORTANT] No shortlist assembly rule — "ranked CSV" → "20–40 names" is undefined, and scores will bunch
Percentile-based sub-scores sum toward a near-normal distribution: rank 30 vs rank 70 is likely noise; and with a shared metric library, some names rank high on BOTH composites.

**Fix:** every output row carries both composite scores + all sub-scores; shortlist = tunable top-N per side (e.g. 25 long / 15 short as a starting guess); a **"contested" flag** for names in the top decile of both composites — structurally likely (the turnaround-first long screen fishes in the same fallen/busted-growth pool the short screen targets, and SI scores positively on both sides), and pitch-relevant (turnaround-vs-value-trap is the debate judges care about); always also emit the top ~100 per side.

### 6.3 [IMPORTANT] Tear sheet contents unspecified — and missing the catalyst/narrative hooks a pitch needs
The screener finds STATES; a winning pitch needs a NARRATIVE and a CATALYST. Verified: the free EDGAR submissions JSON already includes 8-K item codes per filing — ready-made hooks the spec never taps.

**Fix:** add the **submissions JSON as a third data source** (same host/User-Agent as companyfacts; fetched at roadmap step 3). Tier 1 (must-have): composite + every sub-score; per inflection metric the level/slope/trough triplet; the raw quarterly series per metric (pipeline must retain per-metric intermediates); tags (sector, financials/REIT, distress, survivability, coverage % with missing-metric list, contested, restated = 10-K/A, 10-Q/A or 8-K 4.02). Tier 2 (best-effort, nullable): trailing ~8 quarters of 8-K item codes with a legend (2.02 results, 5.02 exec departure, 2.05 restructuring, 2.06 impairment, 1.01 material agreement, 4.02 non-reliance, 3.01 delisting); next earnings date (yfinance calendar — flaky); insider transactions; short interest with staleness stamp; direct EDGAR URLs to the latest 10-K/10-Q.

### 6.4 [IMPORTANT] "Tune windows by inspecting results" has no defined diagnostics — the iteration loop can't actually run
Eyeballing a ranked CSV can't reveal whether 3q vs 6q changes the shortlist or whether one sub-score dominates — and this tuning step is the only validation a no-backtest project gets.

**Fix — "first-run diagnostics" alongside the CSVs (~a day of work):** (1) parameter sensitivity: for slope {3,4,6}q × trough {4,6}q report top-50 Jaccard overlap AND full-universe Spearman rank correlation between combos; (2) sub-score health: distribution, stdev, correlation with composite (detects dominating/dead sub-scores that silently break the 55/45 split); (3) trough-detector fire rate per window (if >~half the universe fires, the window can't discriminate); (4) per-metric coverage counts, split by the financials tag; (5) top-decile sector mix vs universe (flags accidental sector bets; motivates the sector-cap decision).

### 6.5 [IMPORTANT] "Simple business model" proxies (segment count, geographic mix) are not computable from companyfacts
Verified live: companyfacts serves only consolidated, non-dimensional facts — no segment/geography members; us-gaap:NumberOfReportableSegments appears for only ~298 filers in the CY2024 frame (cf. XBRL US rule DQC 0221).

**Fix:** delete the two automated proxies from the spec; keep sector/industry as the sole automated tag and make the judgment fully manual at shortlist stage (the spec's existing fallback). Tear sheet supplies the inputs: sector/industry, SIC description, revenue scale, yfinance longBusinessSummary, a link to the latest 10-K. Optionally display NumberOfReportableSegments when present (coverage should rise for FY2025+ under ASU 2023-07), never score it.

---

## Suggested order of spec updates

1. **Data-layer reality (Theme 1):** quarterlyizer, tag ladders, submissions JSON as third source, FPI handling, universe build/hygiene. Everything downstream consumes this.
2. **Scoring foundations (Theme 3):** missing-data policy, normalization rule, theme-level weights, financials substitute metrics, ROIC/WACC definitions. The composite is uncomputable without these decisions, and defaults chosen ad hoc in code would silently determine the output.
3. **Inflection engine (Theme 2):** TTM/YoY series, slope estimator, trough-with-bounce, conjunctive combination, full-history level, one-off/outlier damping.
4. **Composite content (Themes 4–5):** revenue as sixth input + cost-cut haircut, demonstrated-economics moat window, drawdown kernel, survivability tag, valuation row, SI/insider split; short-side definitions and new tells.
5. **Output & tuning (Theme 6):** shortlist assembly, tear sheet, sector caps, first-run diagnostics.
