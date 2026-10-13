# Energy, Water and CO₂e Footprint — Design Spec

Date: 2026-10-13
Status: implemented on `develop` (2026-10-13). This document describes the code as built.

## Goal

Show roughly how much electricity, water and CO₂e the data centers spent serving the user's Claude tokens. This is a third view on the cost card, `$ | h | ⚡`, covering the same 30 days, plus one line in the session drill-down panel.

**This is an estimate, not a measurement.** Anthropic publishes no per-query or per-token energy, water or emissions figures for any Claude model. Its Transparency Hub names its compute providers (AWS, Google Cloud, and Microsoft Azure since July 2026) and nothing more; as of this writing there is no Anthropic sustainability report. Every number on the view is the logged token counts multiplied by per-token factors taken from third-party research. The honest uncertainty is about an order of magnitude, and the view shows a range rather than a single figure.

## Decisions

| Question | Decision |
|---|---|
| Layout | A third view on the cost card (`⚡`), 30 days, like `h` |
| Water boundary | On-site cooling **plus** the water consumed generating the electricity. The two are shown separately, and the headline is their sum |
| Extras | CO₂e; one everyday comparison per number; a per-session line in the drill-down panel |
| How factors are set | Per token type (input, cache write, cache read, output), × a model-family multiplier, each as (low, central, high) |
| Ranges | Energy uses all-low or all-high factors together. Water and CO₂e combine that range with their own factor's range in quadrature in log space |
| Scope | Inference only. Excludes training, hardware manufacturing, networking and the user's own PC |

## Method

All constants are in `db.py`, under "Energy, water and CO2e estimates".

For each model group, energy (Wh) = `FAMILY_ENERGY_MULT[family]` × Σ (tokens of each type × `FOOTPRINT_WH_PER_1K[type]`) / 1000.

| Factor | Low | Central | High | Unit |
|---|---|---|---|---|
| Output | 0.2 | 1.0 | 3.0 | Wh per 1k tokens, Sonnet-class, whole data center |
| Input (uncached prefill) | 0.04 | 0.2 | 0.6 | ″ |
| Cache write (5m and 1h) | 0.04 | 0.2 | 0.6 | ″ |
| Cache read | 0.001 | 0.02 | 0.1 | ″ |
| Family multiplier | Haiku 0.5 · Sonnet 1 · Opus 2 · Fable 5 | | | × |
| Cooling water | 0.12 | 0.5 | 1.1 | L per kWh |
| Water consumed generating the power | 1.0 | 3.1 | 4.5 | L per kWh |
| Grid carbon intensity (location-based) | 0.12 | 0.35 | 0.6 | kg CO₂e per kWh |

### Why these values

- **Output.** Estimates for frontier models span about 0.2 to 3 Wh per 1k output tokens, counting the whole data center:
  - Google's production measurement of Gemini comes out at 0.24 Wh for a median prompt, against 0.10 Wh for the chips alone, so the whole facility uses about 2.4× the chip energy. Google gives no token count per prompt, so that figure can't be converted per token.
  - Epoch AI's GPT-4o estimate is about 0.3 Wh for about 500 output tokens, which is about 0.6 Wh per 1k.
  - Couch's Claude Code estimate uses 1.95 Wh per 1k output tokens.
  - A fit to Jegham et al.'s Claude 3.7 Sonnet figures gives about 2.9 Wh per 1k.
  - Measured GPU-only energy for large open models on recent hardware is about 0.1–0.7 Wh per 1k. Multiplied by the facility overhead, that is about 0.3–1.6 Wh.
  - The central value of 1.0 sits geometrically in the middle of these.
- **Input and cache writes.** These are about 1/5 of output: Anthropic's input/output price ratio, Epoch's marginal input cost, and the Jegham fit (0.135 Wh per 1k input) all point there.
  - A cache write performs the same prefill as fresh input. The 2× price of a 1-hour write pays for keeping the cache, not for extra compute.
- **Cache reads.** These skip the prefill. They are typically 90–96% of Claude Code's tokens, so this is the factor that matters most, and also the one that is least certain.
  - The central value is 10% of input (0.02 vs 0.2). That is the long-standing price ratio, which Hausfather and tokenatlas also use.
  - The low end is 2.5% of input (0.001 vs 0.04), close to the cost of only moving the stored cache back onto the chips.
  - The high end is 17% of input (0.1 vs 0.6), inside the 14–37% that Irminsul (2026) measured for a cache hit against a fresh prefill at 4k tokens. Claude Code's contexts are 25–50× longer, which should lower that ratio, but the high end also leaves room for the extra decode work a long context adds.
- **Family multiplier.** This uses the current output prices relative to Sonnet 5 ($10): Fable 5.1 $50, Opus 5.5 $20, Haiku 4.5 $5. Anthropic publishes no model sizes, so price is the only public signal. Treat it as an assumption.
- **Cooling water.** Amazon reports 0.12 L/kWh for its data centers in 2025, but individual regions run from 0.04 to over 1.5. Google's 0.26 mL against 0.24 Wh works out to about 1.1 L/kWh. Anthropic's fleet mix across AWS, Google and Azure isn't public, so 0.5 is a judgment that weights AWS heavily.
- **Water consumed generating the power.** Li & Ren use the US average of 3.14 L/kWh of consumption, from Reig et al. They call it conservative next to the US Department of Energy's 4.35. Lawrence Berkeley National Laboratory puts US data centers at 4.52 for 2023. The low end of 1.0 stands for grids heavy in wind, solar or dry-cooled plants.
- **CO₂e.** The central value is the US average from eGRID2023, about 771 lb/MWh, or 0.35 kg/kWh. The low end stands for a low-carbon grid region; Google's market-based figure works out to about 0.125. The high end is a coal-heavy US grid subregion. Market-based accounting, which counts purchased renewable certificates, would come out lower. The estimate uses the physical grid mix instead.
- **Comparisons.**
  - Electricity: 28.4 kWh a day, from the EIA's 865 kWh a month for an average residential customer in 2024.
  - Water: 60 L and 7.8 minutes per shower, from Home Water Works (15.8 gal at 2.1 gpm).
  - CO₂e: 400 g CO₂ per mile, the EPA's figure for a typical gasoline car.

### Ranges

- **Energy.** The low and high ends apply every token type's low or high factor together. All token types run on the same hardware, so a more efficient fleet lowers all of them at once.
- **Water and CO₂e.** These are energy × a factor that depends on location, and location is independent of hardware efficiency. They are combined by `db._combine`. It treats each (low, central, high) as a like-for-like interval of a lognormal and adds the log spreads in quadrature. Multiplying the extremes instead would assume everything is at its worst at once, which for water gives a band about 50× wide.
- **What the ranges are.** They are judgment ranges, not statistical confidence intervals.

### Sanity check against independent estimates

| Case | This method (central) | Other estimate |
|---|---|---|
| 1M cache reads + 10k output on Opus | 60 Wh | — (the hand-checked unit test) |
| Hausfather's 3.2B tokens, 96% cache reads, 0.4% output | about 190 kWh if all Opus, about 97 kWh if all Sonnet | 170 kWh (70–330) |
| Opus, per 1k tokens (input / output / cache read) | 0.4 / 2.0 / 0.04 Wh | Couch: 0.39 / 1.95 / 0.039 Wh |

The agreement is partly circular: Couch, Hausfather and this method all lean on Epoch's estimate and Anthropic's price ratios.

## API

`GET /api/footprint?days=30` and `GET /api/session/{id}/footprint` (404 for an unknown session) both return:

```json
{"energy_wh": {"low", "central", "high"}, "water_l": {...}, "co2e_g": {...},
 "water_cooling_l", "water_generation_l", "cache_read_share",
 "breakdown": [{"model", "energy_wh"}],
 "comparisons": {"home_days", "showers", "shower_minutes", "car_miles"}}
```

`/api/footprint` adds `days`. All values are unrounded floats, and the page shows them to two significant figures.

## Known limitations

- No Claude-specific measurement exists. Every factor comes from other models, other providers or price ratios.
- The CLEER dataset (Sustainable AI Group, Sep 2026) estimates Claude per-token energy separately for cached prefill, uncached prefill and output. It could not be fetched from the build environment. Check it before the next revision of these constants, and multiply its accelerator-only figures by about 2.4 for the whole facility.
- Energy is linear in tokens. Real energy also depends on batch size, the hardware (GPU, TPU or Trainium), and context length: decoding over a long context costs more per token.
- Fast mode and extended thinking aren't modelled separately. Thinking tokens are billed as output, so they are counted as output.
- Cooling water (WUE) is defined per kWh of IT energy but is applied here to whole-facility energy, which overstates cooling water by about the PUE factor (around 10–15%).
- Excluded: training, hardware manufacturing, networking, the user's own PC, and the person-hours judge's calls, which write no transcript.
- The water figures count **consumption** (evaporated or otherwise lost), not withdrawal. Li & Ren's withdrawal figure for the same electricity is about 44 L/kWh.

## Sources

Status: **read** means the primary source was read directly. **via search** means the figure came from search-engine excerpts of the source, because the build environment's network blocked the site.

| Source | Date | What it gives | Type | Status |
|---|---|---|---|---|
| [Google Cloud: Measuring the environmental impact of AI inference](https://cloud.google.com/blog/products/infrastructure/measuring-the-environmental-impact-of-ai-inference); paper [arXiv 2508.15734](https://arxiv.org/abs/2508.15734) | 2025-08-21 | Gemini median text prompt: 0.24 Wh, 0.26 mL, 0.03 gCO₂e. Chips alone: 0.10 Wh, 0.12 mL, 0.02 g | Production measurement, company claim | read |
| [Epoch AI: How much energy does ChatGPT use?](https://epoch.ai/gradient-updates/how-much-energy-does-chatgpt-use) | 2025-02 | ≈0.3 Wh per GPT-4o query, ~500 output tokens | Estimate | via search |
| [Jegham et al., How Hungry is AI?](https://arxiv.org/abs/2505.09598) | 2025-05 → 2026 | Per-query Claude 3.7 Sonnet estimates; fit 0.135 / 2.88 Wh per 1k input / output ([claude-carbon](https://github.com/gwittebolle/claude-carbon)) | Estimate (preprint) | via search |
| [Couch: Electricity use of AI coding agents](https://simonpcouch.com/blog/2026-01-20-cc-impact/) | 2026-01-20 | 390 / 1,950 / 39 Wh per M input / output / cache-read tokens | Estimate (Epoch × price ratios) | via search |
| Hausfather, [The Climate Brink: the real energy use of agentic AI](https://www.theclimatebrink.com/p/the-real-energy-use-of-agentic-ai) | 2026 | 3.2B Claude Code tokens ≈ 170 kWh (70–330); cache read at 10% of input (range 1–25%) | Estimate | via search |
| Irminsul, [arXiv 2605.05696](https://arxiv.org/abs/2605.05696) | 2026-05 | Cache-hit energy 14–37% of fresh prefill (4k tokens; architecture-dependent) | GPU measurement | via search |
| [tokenatlas / claude-code-energy-monitor](https://github.com/Magnus-Gille/claude-code-energy-monitor) | 2026 | Opus: 0.39 / 1.40 / 0.015 / 0.49 Wh per 1k (input / output / cache read / cache write), GPU only | Estimate | via search |
| [CLEER tech report](https://reports.sustainableaigroup.com/CLEER-Tech-Report/) | 2026-09 | Per-token-type accelerator energy for closed models, Claude included | Estimate | **not retrieved** |
| Li, Yang, Islam & Ren, [Making AI Less "Thirsty"](https://arxiv.org/abs/2304.03271) (CACM 2025) | 2023 → 2025 | 3.14 L/kWh US-average water consumption for electricity (from Reig et al.); 43.83 L/kWh withdrawal; DOE 4.35 | Peer-reviewed | via search |
| Amazon data center water use (AWS sustainability fact sheets) | 2025 | Water use effectiveness 0.12 L/kWh across Amazon's data centers | Company claim | via search |
| EPA eGRID2023 | 2025-01 | US average ≈ 771 lb CO₂e/MWh (0.35 kg/kWh) | Government data | via search (third-party summary) |
| [EIA: Today in Energy](https://www.eia.gov/todayinEnergy/detail.php?id=65244) | 2025 | 865 kWh a month per residential customer (2024) | Government data | via search |
| [Home Water Works: showers](https://home-water-works.org/indoor-use/showers) | — | 15.8 gal (59.8 L), 7.8 min, 2.1 gpm | Research summary (Residential End Uses of Water, 2016) | via search |
| [EPA: typical passenger vehicle](https://www.epa.gov/greenvehicles/greenhouse-gas-emissions-typical-passenger-vehicle) | 2025 snapshot | ≈400 g CO₂ per mile (22.2 mpg, 8,887 g CO₂ per gallon) | Government data | via search |
