# Grants catalogue: DRAFT for Adebayo's review (D-FUND-2)

**Status: draft, not seeded.** Every row below was checked on the funder's own page on **2026-11-12** with a plain fetch of that page, and every amount, date and eligibility line is what that page said on that day. Where a page did not state something, the cell says `not stated` or the amount is empty; nothing was filled in from memory, blogs or aggregator lists. Rows marked `partial` have a verified source page but missing amounts or dates: decide whether to keep them.

**Read this before approving:** this is **15 rows, not the 30 to 40 asked for.** The tooling available had no page reader, so each row was fetched directly with `curl` and read; many funders' pages were empty to a plain fetch (script-built sites), unreachable, or stale, and those were dropped (list at the bottom) rather than guessed. Four rows are credits (in-kind) and one is a matching-funds model, labelled as such. Nigeria-first coverage is thin: develoPPP Ventures (current call includes Nigeria), TEF, Orange Corners Nigeria (Lagos), and Africa-wide programmes.

Companion CSV for the later seed: `grants-catalogue-draft.csv` (same rows, array fields separated by commas inside the cell). Columns follow the spec's `grants` table: name, funder, amount min/max with currency, deadline, regions, sectors, stages, eligibility, source URL, verified_on, plus `kind`, `status_on_verified_date`, `verification` and notes.

| # | Name | Funder | Kind | Amount | Deadline / window | Status on 2026-11-12 | Regions | Stages | Source | Verified |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | TEF Entrepreneurship Programme | Tony Elumelu Foundation | cash grant (non-refundable seed capital) + training | 5,000 USD | annual: portal opens 1 January, closes 1 March at midnight (per the programme page) | closed until next 1 January | Africa (all 54 countries per the foundation's homepage) | business idea, or business no older than 5 years | [www.tonyelumelufoundation.org](https://www.tonyelumelufoundation.org/tef-entrepreneurship-programme) | verified |
| 2 | develoPPP Ventures | German Federal Ministry for Economic Cooperation and Development (BMZ), delivered by GIZ and partners | matching-funds catalytic funding | up to 100,000 EUR | current call: 15 November to 31 December 2026 (Egypt, Kenya, Nigeria, Rwanda, South Africa, Tanzania) | opens 15 Nov 2026 | Nigeria, Egypt, Kenya, Rwanda, South Africa, Tanzania (current call) | early stage with initial revenues (proof of concept) | [www.developpp.de](https://www.developpp.de/en/application/ventures/) | verified |
| 3 | Africa Business Heroes (ABH) Prize | Jack Ma Foundation and Alibaba Philanthropy | competition prize (grants) | up to 300,000 USD | annual: the 2026 application deadline was 7 May 2026 | closed until next cycle | Africa (all 54 countries) | established: registered business or 3+ years of proven operation | [africabusinessheroes.org](https://africabusinessheroes.org/en/the-prize/application-guidelines) | verified |
| 4 | Africa Prize for Engineering Innovation | Royal Academy of Engineering | competition prize + commercialisation programme | up to 50,000 GBP | annual: the 2027 prize call ran 13 July to 8 September 2026 (closed now) | closed until next call | sub-Saharan Africa | early commercialisation (eight-month commercialisation programme for the shortlisted) | [africaprize.raeng.org.uk](https://africaprize.raeng.org.uk/about-the-prize/how-to-apply) | verified |
| 5 | UNICEF Venture Fund | UNICEF | equity-free funding (tranches) + mentoring | up to 100,000 USD | no deadline stated on the page; apply by Expression of Interest | rolling (as presented) | companies registered in a UNICEF programme country | early stage; growth stage for graduates | [www.unicefventurefund.org](https://www.unicefventurefund.org/funding-support) | verified |
| 6 | Echoing Green Fellowship | Echoing Green | fellowship stipend (grant; recoverable grant for for-profits) | up to 100,000 USD | annual: applications for the 2026 Fellowship are closed | closed until next cycle | not stated on the pages fetched | early stage | [echoinggreen.org](https://echoinggreen.org/fellowship/apply/) | verified |
| 7 | Cartier Women's Initiative | Cartier Philanthropy and partners | annual awards + coaching | not stated | annual; the 2026 fellows were announced on 26 March 2026 | not confirmed | any country | women-run and women-owned impact businesses | [www.cartierwomensinitiative.com](https://www.cartierwomensinitiative.com/) | partial |
| 8 | Google for Startups Accelerator: Africa | Google | equity-free accelerator (in-kind: mentoring, product credits) | not stated | annual: applications ran 4 February to 18 March 2026 (closed); register interest for the next one | closed until next cohort | Africa | growth stage | [startup.google.com](https://startup.google.com/programs/accelerator/africa/) | verified |
| 9 | Google for Startups Cloud Program | Google Cloud | credits (in-kind) | up to 350,000 USD | rolling | open | not restricted on the page fetched | startups (see page) | [cloud.google.com](https://cloud.google.com/startup) | verified |
| 10 | AWS Activate | Amazon Web Services | credits (in-kind) | up to 200,000 USD | rolling | open | not restricted on the page fetched | startups (see page) | [aws.amazon.com](https://aws.amazon.com/startups/) | verified |
| 11 | Microsoft for Startups Founders Hub | Microsoft | credits (in-kind) | up to 150,000 USD | rolling | open | not restricted on the page fetched | startups (see page) | [www.microsoft.com](https://www.microsoft.com/en-us/startups) | verified |
| 12 | Orange Corners Nigeria (incubation programme) | Embassy of the Kingdom of the Netherlands in Lagos, implemented by FATE Foundation | incubation programme | not stated | per cohort: the page shows an Apply Now button when a window is open | not confirmed | Lagos, Nigeria (core business operations in Lagos) | validated innovative business concept, not more than 2 years old | [www.orangecorners.com](https://www.orangecorners.com/country/nigeria/) | partial |
| 13 | Hult Prize | Hult Prize Foundation | student competition prize | not stated | annual; not captured | not confirmed | 130+ participating countries (homepage) | university student teams | [www.hultprize.org](https://www.hultprize.org/) | partial |
| 14 | MIT Solve Global Challenges | MIT Solve | challenge funding | not stated | per challenge: see Open Challenges | not confirmed | global | by challenge | [solve.mit.edu](https://solve.mit.edu/challenges) | partial |
| 15 | Grand Challenges Canada | Grand Challenges Canada | funding opportunities (health-focused) | not stated | per opportunity | not confirmed | varies | varies | [www.grandchallenges.ca](https://www.grandchallenges.ca/apply-for-funding/) | partial |

## Details per row

### TEF Entrepreneurship Programme
- **Amount:** US$5,000 non-refundable seed capital per selected participant, as stated on the programme page.
- **Eligibility:** Africans aged 18 or over, with a business idea or a business no more than 5 years old. Applications go through the TEFConnect platform. Other criteria on the page; read it before applying.
- **Sectors:** not stated
- **Source:** https://www.tonyelumelufoundation.org/tef-entrepreneurship-programme (verified 2026-11-12, verified)
- **Notes:** Window dates are the page's standing statement, not a 2027 announcement.

### develoPPP Ventures
- **Amount:** Up to EUR 100,000 of catalytic funding per start-up (the page states 100,000 euros). Matching-funds model: investment of at least the same amount must be raised in addition.
- **Eligibility:** Privately owned, profit-oriented start-up registered in the target country (or registering before the investment). Already generating initial revenues, high development relevance and scalability. Investors for the matching amount should be in advanced talks; matching funds received up to 6 months before applying can count.
- **Sectors:** innovative business models with social and/or environmental impact (development relevance)
- **Source:** https://www.developpp.de/en/application/ventures/ (verified 2026-11-12, verified)
- **Notes:** Not a pure grant: needs matching private capital.

### Africa Business Heroes (ABH) Prize
- **Amount:** Ten finalists share USD 1.5 million in grant funding; the top winner receives up to USD 300,000 (homepage).
- **Eligibility:** African entrepreneurs. The 2026 application required proof of legal registration or proof of operation for 3+ years (bank statements and similar). Read the current application guidelines each year.
- **Sectors:** not stated
- **Source:** https://africabusinessheroes.org/en/the-prize/application-guidelines (verified 2026-11-12, verified)
- **Notes:** Funder names as shown on the prize site; confirm on the site's About page.

### Africa Prize for Engineering Innovation
- **Amount:** 2027 prize: GBP 85,000 in total: GBP 50,000 overall winner, GBP 10,000 to each of three finalists, GBP 5,000 One-to-Watch (official news page).
- **Eligibility:** Engineers and innovators in sub-Saharan Africa. Two-stage application through the Academy's grants system. The how-to-apply page says applications are closed.
- **Sectors:** engineering innovation
- **Source:** https://africaprize.raeng.org.uk/about-the-prize/how-to-apply (verified 2026-11-12, verified)
- **Notes:** Amounts from raeng.org.uk news item of 13 July 2026.

### UNICEF Venture Fund
- **Amount:** Up to USD 100K equity-free for early-stage; up to USD 400K equity-free for growth-stage companies that graduated from the early-stage investment (page). Awarded in 2 to 3 tranches against an agreed work plan.
- **Eligibility:** Private business, open source solution showing promising results, registered in a UNICEF programme country. Check the page for the country list.
- **Sectors:** open source frontier tech (the page lists Data Science and AI among tech areas)
- **Source:** https://www.unicefventurefund.org/funding-support (verified 2026-11-12, verified)
- **Notes:** Whether Nigeria is on the programme-country list was not read from this page.

### Echoing Green Fellowship
- **Amount:** Seed funding: a stipend over 18 months (100,000 USD). For-profit organizations receive it as recoverable grants (fellowship page).
- **Eligibility:** Applicants 18+ (by 1 June in the 2026 round), proficient in English, early-stage organization or idea that needs financial and non-financial resources. A company valued over USD 5 million, or earning over USD 2 million revenue with net profit, is not eligible (2026 page).
- **Sectors:** social impact (enterprises tackling a challenge)
- **Source:** https://echoinggreen.org/fellowship/apply/ (verified 2026-11-12, verified)
- **Notes:** Country eligibility not read; confirm.

### Cartier Women's Initiative
- **Amount:** Award amounts not stated on the pages fetched (the site says USD 16M in financial support since 2006).
- **Eligibility:** Open to women-run and women-owned businesses from any country and sector that aim for a strong and sustainable social and/or environmental impact (homepage).
- **Sectors:** any sector with strong social and/or environmental impact
- **Source:** https://www.cartierwomensinitiative.com/ (verified 2026-11-12, partial)
- **Notes:** Application page returned an error; amounts and window not verified. Funder name as shown on the site footer was not read.

### Google for Startups Accelerator: Africa
- **Amount:** No cash stated. Equity-free support for the duration of the three-month programme; Cloud credits can be applied for separately, subject to review.
- **Eligibility:** Cohorts of 10 to 15 startups. Read the eligibility section on the program page.
- **Sectors:** AI-focused technical challenges (page)
- **Source:** https://startup.google.com/programs/accelerator/africa/ (verified 2026-11-12, verified)
- **Notes:** In-kind only.

### Google for Startups Cloud Program
- **Amount:** Up to USD 350,000 in Cloud credits over two years (program page).
- **Eligibility:** Apply through the program page; eligibility rules are on the page.
- **Sectors:** any
- **Source:** https://cloud.google.com/startup (verified 2026-11-12, verified)
- **Notes:** Credits, not cash.

### AWS Activate
- **Amount:** Up to USD 200,000 in AWS Activate credits, with additional credits for AI startups (page).
- **Eligibility:** Apply through the page.
- **Sectors:** any
- **Source:** https://aws.amazon.com/startups/ (verified 2026-11-12, verified)
- **Notes:** Credits, not cash.

### Microsoft for Startups Founders Hub
- **Amount:** Up to USD 150K in Startup credits over time; up to USD 5,000 unlockable through business verification (page).
- **Eligibility:** Apply through the page.
- **Sectors:** any
- **Source:** https://www.microsoft.com/en-us/startups (verified 2026-11-12, verified)
- **Notes:** Credits, not cash.

### Orange Corners Nigeria (incubation programme)
- **Amount:** Not stated on the Nigeria page; any innovation-fund support is described elsewhere and was not verified.
- **Eligibility:** Aspiring entrepreneur with core operations in Lagos, aged 18 to 35, with a validated innovative concept no more than 2 years old.
- **Sectors:** not stated
- **Source:** https://www.orangecorners.com/country/nigeria/ (verified 2026-11-12, partial)
- **Notes:** Skills and incubation; cash terms not verified.

### Hult Prize
- **Amount:** The homepage states USD 1M in annual prize funding; the split per team was not read.
- **Eligibility:** Students.
- **Sectors:** business solutions to global challenges
- **Source:** https://www.hultprize.org/ (verified 2026-11-12, partial)
- **Notes:** Only for students; terms not verified.

### MIT Solve Global Challenges
- **Amount:** Per-award amounts not read. The site says Solve has mobilized over USD 90 million in funding to 477 solutions.
- **Eligibility:** Open innovation challenges; each has its own criteria.
- **Sectors:** by challenge
- **Source:** https://solve.mit.edu/challenges (verified 2026-11-12, partial)
- **Notes:** Check which challenges are open.

### Grand Challenges Canada
- **Amount:** Not read. The page lists open funding opportunities, including a climate-and-health initiative with the Science for Africa Foundation.
- **Eligibility:** Per opportunity.
- **Sectors:** health, climate and health
- **Source:** https://www.grandchallenges.ca/apply-for-funding/ (verified 2026-11-12, partial)
- **Notes:** Only relevant to health and climate-health ventures.

## Checked and dropped (not in the catalogue)

- **Catalyst Fund (BFA Global)**: only an outdated page (cohort 'late 2022') could be found; no current call verified.
- **Google for Startups Black Founders Fund**: the program page describes past awards (USD 40M since 2020) and no open Africa call.
- **Global Innovation Fund**: its apply page says the last window closed 15 January 2025.
- **USADF (US African Development Foundation) Nigeria and Off-Grid Energy calls**: the current Nigeria call and the challenge page could not be read directly (the page fetched was almost empty); only a search summary mentioned a USD 250,000 ceiling for agricultural cooperatives.
- **Innovation Prize for Africa, Visa Everywhere Initiative, FATE Foundation, NSIA Prize, Wharton and Harvard Africa venture competitions, Smart Africa x FAO Innovate Africa Challenge, Heva Fund, Anzisha Prize, MEST Africa Challenge, Nigeria Youth Investment Fund**: the official site could not be reached, was empty to a plain fetch, or returned no usable amounts or deadlines; none was written down from memory or from a blog.
- **Lagos State Employment Trust Fund (LSETF)**: its programmes are loans, not grants.

## For the seed step (later)

- `deadline` is free text on purpose: most of these are annual windows, and the match job should treat them as `rolling`/`annual` with a stale check, not as a date.
- Credits rows (`kind` contains `credits`) are not cash; the match job may want a separate flag.
- Re-verify every row before seeding: pages and windows change, and `verified_on` is the date of this draft.
- No personal contact details are recorded.
