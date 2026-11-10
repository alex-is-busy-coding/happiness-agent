# The happiness database (DuckDB)

## gss — General Social Survey, 75,699 US adults, 1972-2024 (NORC)
One row per respondent. Self-reported happiness vs. social life, money, health, work.
Ordinal answers come as a numeric code plus a `_label` text column; LOWER code = better/more
(happy: 1 very happy ... 3 not too happy; socfrend 1 almost daily ... 7 never; EXCEPT attend: 0 never ... 8 several times a week).
NULL means the question wasn't asked or wasn't answered that year — always filter `IS NOT NULL`.
Weight every share by `weight` (e.g. SUM(weight * (happy = 1)::INT) / SUM(weight)).
family_income_2024usd is family income before taxes in 2024 dollars, comparable across all years.
The survey records income in brackets, so values cluster: compare bands or NTILE groups.
CAUTION on 2021 and later: until 2018 the survey was done in person; 2021 was done on the web
because of COVID, and 2022 and 2024 mix both. NORC warns that changes between 2018 and 2021
may partly reflect that switch, not real change. Say so whenever a finding rests on the 2020s.
Columns: year (BIGINT), id (SMALLINT), age (DOUBLE), sex (DOUBLE), degree (DOUBLE), marital (DOUBLE), wrkstat (DOUBLE), childs (DOUBLE), region (TINYINT), happy (DOUBLE), hapmar (DOUBLE), health (DOUBLE), life (DOUBLE), satfin (DOUBLE), satjob (DOUBLE), finrela (DOUBLE), class (DOUBLE), trust (DOUBLE), socrel (DOUBLE), socommun (DOUBLE), socfrend (DOUBLE), socbar (DOUBLE), attend (DOUBLE), happy_label (VARCHAR), hapmar_label (VARCHAR), health_label (VARCHAR), life_label (VARCHAR), satfin_label (VARCHAR), satjob_label (VARCHAR), finrela_label (VARCHAR), trust_label (VARCHAR), class_label (VARCHAR), sex_label (VARCHAR), marital_label (VARCHAR), degree_label (VARCHAR), wrkstat_label (VARCHAR), socfrend_label (VARCHAR), attend_label (VARCHAR), socrel_label (VARCHAR), socommun_label (VARCHAR), socbar_label (VARCHAR), family_income_2024usd (DOUBLE), weight (DOUBLE)

## whr — World Happiness Report 2026, 2,116 country-year rows, 2011-2025 (Gallup World Poll)
One row per country per year. life_evaluation is the 0-10 Cantril ladder (3-year average).
The explained_by_* columns are the report's estimate of how many ladder points each of six
factors accounts for in that country's score; they plus dystopia_plus_residual sum to
life_evaluation. They are NULL before 2019. What each factor measures:
gdp_per_capita = national income per person; social_support = share who say they have someone
to count on in times of trouble; healthy_life_expectancy = years lived in good health;
freedom = share satisfied with their freedom to choose what to do with their life;
generosity = share who donated to charity recently; low_corruption = share who do NOT see
corruption as widespread in government and business (a view about corruption: do not call it
"trust"). Name them as that, nothing broader.
rank is within that year: 1 is the highest score, so a rank going from 24 to 23 is a rise.
Columns: year (BIGINT), rank (BIGINT), country (VARCHAR), life_evaluation (DOUBLE), ci_lower (DOUBLE), ci_upper (DOUBLE), explained_by_gdp_per_capita (DOUBLE), explained_by_social_support (DOUBLE), explained_by_healthy_life_expectancy (DOUBLE), explained_by_freedom (DOUBLE), explained_by_generosity (DOUBLE), explained_by_low_corruption (DOUBLE), dystopia_plus_residual (DOUBLE)

## atus_wb — NOT BUILT (raw files missing)
