# Second-reader extraction — instructions

## Why we are asking you to do this

We are testing whether a language model can extract study metadata as reliably as a person.

To judge that, we compare the model's output against the values already recorded in the 3ie
database and report an agreement score. The problem is that this only tells us how well the model
matches **one** reading of each paper. It tells us nothing about how hard the task itself is.

Some of these fields are genuinely ambiguous. A paper on a cash transfer conditional on clinic
attendance can defensibly be filed under Health or Social protection. If two qualified people
disagree on cases like that, then a model that "disagrees" with the database is not necessarily
wrong — it may be doing exactly what a second human would do.

Right now we cannot tell those two situations apart, so every quality target we have set is a
number borrowed from published convention rather than measured on this evidence base. We have
already been caught out by this: a review of 49 records found errors in the recorded values that
were being counted against the model, and a separate check found around 190 author names entered
in the wrong order.

Your independent reading of these 40 papers gives us the missing number — how much two careful
readers agree on this task. That becomes the realistic ceiling. If the model reaches it, the
remaining disagreement is ambiguity in the task, and there is nothing further to fix. If the model
falls well short, there is real room to improve.

**There is no expectation that you agree with the existing values.** Genuine disagreement is the
signal we need. Please do not try to guess what is already recorded.

## The one rule that matters most

**Work blind.** Do not look at the existing database values, the dashboard, or any model output
before you finish. If your reading is influenced by the first one, the comparison measures nothing.

## How to fill it in

Open `curator2_sheet.csv`. One row per paper, 40 in total. The papers are in `papers\`, named by
the `md_file` column.

Leave `record_id`, `title` and `md_file` untouched — they link your reading back to the paper.

- **Multiple values** (`authors`, `author_affiliation`, `author_country`): separate with a
  vertical bar, e.g. `Smith, John | Okonkwo, A. M.`
- **Single value** (`sector_name`): exactly one value, copied from
  `curator2_taxonomy_options.csv`. Please match the spelling in that file.
- **`sub_sector`**: one or more values from the options file, separated with a vertical bar, the
  same as the other multi-value fields. Most papers have exactly one.
- **Blank cells**: leave a cell empty only if the paper genuinely does not state the information.
  Please do not guess — a guess corrupts the ceiling we are trying to measure. A blank is a
  useful, honest answer.
- If a field is genuinely a toss-up between two options, pick one and note the alternative in an
  email. Those cases are the most informative ones in the whole exercise.

## What each field means

These definitions are taken from the **DEP extraction protocol for IE v4 (Admin panel)** — the
same protocol used to produce the existing records — so that your reading and the database follow
one standard. Where the protocol assumes the admin panel's dropdowns, the equivalent instruction
for this spreadsheet is given instead.

**authors** — enter all authors, one per entry, in the order printed. The format is
`Last name/s, First name Second name`, e.g. `Sabet, Shayda`, `Sabet, Shayda M.`,
`Sabet, Shayda Mae`.

The protocol asks you, where a publication gives only initials, to do a cursory online search on
the name plus paper title to find the full name. **For this exercise, please do not do that** —
record what the paper prints, e.g. `Miranda, J.` or `Miranda, J. M.`. The model cannot search the
web, so looking names up would measure your internet access rather than the difficulty of reading
the paper. This is a deliberate departure from the protocol, and the only one of substance.

Take care with the surname/given-name split: `Ryman, Tove K.`, not `Tove, K. Ryman`. This is the
single most common error we have found in the existing data.

**author_affiliation** — the institution each author is affiliated with, *as stated in the
article*. Give the full name with any abbreviation in brackets, e.g.
`International Initiative for Impact Evaluation (3ie)`. Capture all of them if there are several.

Per the protocol the department, faculty, lab or centre is a **separate** field and is not wanted
here: record `Harvard University`, not `Department of Economics, Harvard University`. Do not spend
time looking for an affiliation elsewhere if the paper does not report it — write `Not reported`.

**author_country** — the country in which each author's institution sits, where specified or
obvious. If an organisation has offices worldwide and no particular office is named, use the
headquarters country: `JPAL` → `United States`, `JPAL Africa` → `South Africa`. Write
`Not reported` if the country is not easily determined.

**sector_name** — select the ONE sector that captures the intervention being evaluated. Use the
spelling in the options file. Three recurring judgement calls:

- *Health vs Social protection* — a deworming campaign is Health; a cash transfer conditional on
  health check-ups is Social protection. The test is whether the core of the intervention is
  medical care or a monetary transfer.
- *Public administration vs a sector* — reforming the health ministry is Health. Fiscal
  decentralisation itself is Public administration. Use Public administration only when governance
  is the subject, not merely the deliverer.
- *Agriculture vs Industry trade and services* — growing maize is Agriculture; marketing or
  processing maize is Industry trade and services.

**sub_sector** — choose the sub-sector that applies, from within the sector you just chose. The
`parent_sector` column in the options file shows which sub-sectors belong to which sector.

Record **every** sub-sector that applies, separated with a vertical bar, exactly as the protocol
directs. Most papers have one. Where a paper genuinely spans two — a school-feeding trial is both
an education and a nutrition question — record both rather than choosing.

## When you are done

Send the filled CSV back. We will score your reading against the existing values using the same
code that scores the models, so the two are directly comparable.

Your individual answers are not being assessed. We are measuring the task, not the reader.

## Limitations we are aware of

The existing values come from the 3ie IER database, accumulated over time by many hands rather
than produced in a single documented pass. The protocol above is the standard they were meant to
follow, but adherence varies — the reversed author names are one visible example. So this exercise
measures agreement between one careful fresh reading and the database as it stands. That is the
number we need, but it is not the same as two readers working simultaneously from a clean brief.

One deliberate departure from the protocol is flagged above: no online name lookup. It exists to
keep the comparison with the model fair, since the model cannot browse.
