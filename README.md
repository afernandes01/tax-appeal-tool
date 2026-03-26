# Cook County Property Tax Appeal Tool

#### Video Demo: <https://youtu.be/HECpn_qOMuw>

#### Description:

The Cook County Property Tax Appeal Tool (provisory name) is a web application that I have been willing to develop and make it available to the public for quite some time, but did not have the skills that now I acquired after CS50. The tool helps homeowners in Cook County, Illinois, to analyze their property tax assessment and build evidence for a property tax appeal. Commercial appeal firms charge 25% to 50% of first-year tax savings for work that is fundamentally data retrieval and comparable property analysis in data readily available by the government. This tool performs that same analysis for free, using the publicly available data from the Cook County Open Data Portal.

The tool supports two types of appeal arguments recognized by the Cook County Board of Review: (1) uniformity (demonstrating that similar properties are assessed at a lower value) and (2) overvaluation (demonstrating that the assessed value implies a fair market value higher than what comparable homes have actually sold for). It currently supports single-family residential properties, with condo and multi-family support planned as a future enhancement. The reason for not including other types of properties is that we would have to pull data from multiple other databases, and what is already a high-complexity project for CS50 scope, would be even more daunting.

Most important, is the relevance of this project and what impact it can cause. Research from the University of Chicago and reporting by ProPublica have documented significant racial and socioeconomic disparities in who files property tax appeals in Cook County. Historical trends have shown that lower income communicaties are the ones that have been the most affected by tax property increases and the ones with the lower access to data and information to proceed. Communities that would benefit most from appealing are least likely to file. This tool aims to make the analysis accessible to everyone. For this reason parts of the page and the way data/information is displayed is targeted for easy interpretation and some education (see the "How it works" html page).

## How It Works

A user enters their 14-digit Property Index Number (PIN) on the home page. If the user doesn't know the 14-digit Pin there is a link for the Cook County website where they can find it. The application then makes a series of API calls to the Cook County Open Data Portal (powered by Socrata) to fetch the property's physical characteristics, current assessment, comparable properties in the same neighborhood and classification, and recent sales of similar homes. It scores each comparable property using a multiplicative similarity algorithm, ranks them by appeal strength, and presents the results in an organized, actionable format.

The tool also includes the educational page mentioned before explaining how Cook County assesses property, the triennial reassessment cycle, the four levels of appeal, and the three valid bases for filing. This helps users who may be unfamiliar with the process understand their options before filing.

## Files

**app.py** is the Flask application. It defines the routes (similarly to how we designed the finance exercise in CS50): the home page where users enter their PIN and adjust similarity weights, the analysis route that orchestrates the entire workflow, detail pages for viewing all comparable properties and all sales data, and the educational page. It handles PIN formatting (removing dashes, zero-padding to 14 digits), collects user-adjusted weights from the form sliders, coordinates the API calls through helper functions, and passes results to the templates for rendering. It also includes error handling so that if the Cook County data portal is slow or unresponsive, the user receives a friendly error message rather than a crash (again, similar to the grumpy cat in CS50).

**helpers.py** contains all the core logic. A lot more functions than I initially expected, but AI supported in designing the logic and explaining syntaxes, espacially for the API calls. Key functions are:

- `fetch_property(pin)` retrieves a property's physical characteristics from the Single and Multi-Family Improvement Characteristics dataset, its current assessment from the Assessed Values dataset, its neighborhood code from the Parcel Universe dataset, and its street address from the Parcel Addresses dataset. It also validates that the property is a supported single-family class.

- `find_comps(prop, weights, limit)` finds comparable properties for uniformity analysis. It queries the Parcel Universe for all properties in the same neighborhood code and classification, retrieves their characteristics and assessments, applies hard cutoffs to eliminate properties that are fundamentally not comparable, scores similarity using a multiplicative weighted algorithm (a lot of back and forth to get to something I was comfortable with), calculates assessment per square foot and savings potential (a rough estimate), and ranks results by a blended appeal score.

- `find_sales_comps(prop, weights)` performs overvaluation analysis by pulling recent arm's-length sales in the same neighborhood and class (used some external sources as inspiration to that), filtering out flagged transactions (non-arm's-length sales, outliers, same-property resales within 365 days), merging in property characteristics, scoring similarity, and comparing sale prices against the subject property's implied fair market value.

- `calculate_savings(current_assess, proposed_assess)` estimates potential annual tax savings using the Cook County equalization factor and an estimated local tax rate.

- `batch_fetch_addresses(pin_list)` consolidates all address lookups into a single API call to minimize timeouts from the address dataset.

**templates/layout.html** is the base template with the site header, navigation, footer, and all CSS styles. Every other template extends this one. Inspired by the CS50 finance.

**templates/index.html** is the home page with the PIN input form and optional similarity weight sliders. It includes a loading overlay with a spinner animation that appears while the analysis runs (typically 1 to 3 minutes due to multiple API calls).

**templates/results.html** displays the full analysis: property summary, assessment details, estimated savings, uniformity analysis with a ranked comp table and case strength badge, overvaluation analysis with comparable sales, and a "What to Do Next" section with direct links to the Cook County Assessor and Board of Review filing portals.

**templates/all_comps.html** and **templates/all_sales.html** are detail pages showing the full comparable property pool and all recent sales data respectively. Both feature sticky table headers (had to learn this one!) and sortable columns (this one was new to me too) implemented with vanilla JavaScript.

**templates/learn.html** is the educational page explaining the assessment process, triennial cycle, appeal levels, and bases for appeal.

**templates/error.html** displays a user-friendly error message when something goes wrong, typically due to API timeouts.

## Design Decisions

**Multiplicative similarity scoring with hard cutoffs.** Early versions used an additive weighted score, but this allowed a property that was very different on one dimension (such as age) to still score high if it matched well on other dimensions. A 1975 house is not a credible comparable for a 2021 house, regardless of how similar the square footage is. The multiplicative approach ensures all factors must be reasonable. Hard cutoffs (50% for square footage, relative age scaling, 2 bedrooms, 60% for lot size) eliminate fundamentally incomparable properties before scoring begins.

**Relative age cutoffs.** The age cutoff uses `max(10, subject_age * 40%)` rather than a fixed number. This means a 4-year-old house compares against homes up to 14 years old, while a 65-year-old house compares against homes 39 to 91 years old. This reflects the reality that age differences matter more for newer construction than for established homes.

**Separate approaches for uniformity and overvaluation.** Uniformity analysis applies hard cutoffs because the legal argument is that properties are equivalent. Overvaluation analysis uses a small floor value instead of hard cutoffs because even a somewhat different property that sold below the subject's implied fair market value is relevant market evidence.

**User-adjustable weights.** The similarity weights (square footage, age, bedrooms, lot size) are exposed as sliders on the input form. This lets users who understand their local market adjust the analysis. For example, in a neighborhood where all lots are similar, a user might reduce the lot size weight and increase the age weight.

**Honest output.** The tool explicitly tells users when they do not have a strong case, using color-coded badges (strong, moderate, weak). Not every property has grounds for appeal, and presenting that clearly prevents users from wasting time on filings unlikely to succeed.

## Technologies Used

- Python 3 with Flask for the web application
- The Socrata Open Data API (SODA) for all property data, accessed via the requests library
- Pandas for data manipulation and analysis (this was in my to do list to study after CS50, learning how to use this in this project was a great win!)
- NumPy for mathematical operations in similarity scoring (suggested by my co-pilot)
- HTML, CSS, and vanilla JavaScript for the frontend (as used in the projects)
- Jinja2 templating for dynamic page rendering (as used in the projects)
- VSCode with Jupyter notebooks were also in my "to learn" after CS50 and I took the opportunity to test it out. They are fantastic!

## Data Sources

All data comes from the Cook County Open Data Portal at datacatalog.cookcountyil.gov. The specific datasets used are Assessed Values (uzyt-m557), Parcel Universe (nj4t-kc8j), Single and Multi-Family Improvement Characteristics (x54s-btds), Parcel Sales (wvhk-k5uv), and Parcel Addresses (3723-97qp). No authentication is required for read access.

## Future Enhancements

- Condo and multi-family property support using the Residential Condominium Unit Characteristics dataset
- Local caching of some data sets to reduce API timeouts
- PDF generation of a formatted evidence packet ready for Board of Review submission
- Integration with the CCAO's LightGBM leaf-node matching algorithm for statistically superior comparable identification
- Historical appeal outcome data to estimate probability of success

## Acknowledgments

This project was built with assistance from Claude (Anthropic), which is permitted for CS50 final projects. The Cook County Assessor's Office Data Department maintains the open data infrastructure that makes this tool possible. The comparable property methodology draws from Board of Review guidelines, PTAB requirements, and Tanya Schlusser's published Jupyter notebook analysis at tanyaschlusser.github.io.
