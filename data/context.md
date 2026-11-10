# The happiness database (DuckDB)

## gss — General Social Survey, 75,699 US adults, 1972-2024 (NORC)
One row per respondent. Self-reported happiness vs. social life, money, health, work.
Ordinal answers come as a numeric code plus a `_label` text column; LOWER code = better/more
(happy: 1 very happy ... 3 not too happy; socfrend 1 almost daily ... 7 never; EXCEPT attend: 0 never ... 8 several times a week).
NULL means the question wasn't asked or wasn't answered that year — always filter `IS NOT NULL`.
Weight every average/share by `weight` (e.g. SUM(weight * x) / SUM(weight)).
family_income_1986usd is inflation-adjusted to 1986 dollars (comparable across all years).
Columns: year (SMALLINT), id (SMALLINT), age (DOUBLE), sex (DOUBLE), degree (DOUBLE), marital (DOUBLE), wrkstat (DOUBLE), childs (DOUBLE), region (TINYINT), happy (DOUBLE), hapmar (DOUBLE), health (DOUBLE), life (DOUBLE), satfin (DOUBLE), satjob (DOUBLE), finrela (DOUBLE), class (DOUBLE), family_income_1986usd (DOUBLE), family_income_const_usd (DOUBLE), trust (DOUBLE), socrel (DOUBLE), socommun (DOUBLE), socfrend (DOUBLE), socbar (DOUBLE), attend (DOUBLE), happy_label (VARCHAR), hapmar_label (VARCHAR), health_label (VARCHAR), life_label (VARCHAR), satfin_label (VARCHAR), satjob_label (VARCHAR), finrela_label (VARCHAR), trust_label (VARCHAR), class_label (VARCHAR), sex_label (VARCHAR), marital_label (VARCHAR), degree_label (VARCHAR), wrkstat_label (VARCHAR), socfrend_label (VARCHAR), attend_label (VARCHAR), socrel_label (VARCHAR), socommun_label (VARCHAR), socbar_label (VARCHAR), weight (DOUBLE)

## whr — World Happiness Report 2026, 2,116 country-year rows, 2011-2025 (Gallup World Poll)
One row per country per year. life_evaluation is the 0-10 Cantril ladder (3-year average).
The explained_by_* columns decompose each country's score into six factors
(GDP per capita, social support, healthy life expectancy, freedom, generosity, low corruption);
they plus dystopia_plus_residual sum to life_evaluation. rank is within that year.
Columns: year (BIGINT), rank (BIGINT), country (VARCHAR), life_evaluation (DOUBLE), ci_lower (DOUBLE), ci_upper (DOUBLE), explained_by_gdp_per_capita (DOUBLE), explained_by_social_support (DOUBLE), explained_by_healthy_life_expectancy (DOUBLE), explained_by_freedom (DOUBLE), explained_by_generosity (DOUBLE), explained_by_low_corruption (DOUBLE), dystopia_plus_residual (DOUBLE)

## atus_wb — NOT BUILT (raw files missing)
