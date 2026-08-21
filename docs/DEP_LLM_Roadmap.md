# DEP: Current Capabilities, LLM Integration & Full-Stack Rebuild Roadmap

---

## 1. What the DEP Is Today

The **Development Evidence Portal (DEP)** is 3ie's centralised, searchable repository of impact evaluations (IEs) and systematic reviews (SRs) relevant to international development. It is the largest structured evidence database of its kind.

### 1.1 Core Capabilities

| Capability | Description |
|---|---|
| **Evidence repository** | ~24,000 unique IE and SR records, publicly searchable at [developmentevidence.3ieimpact.org](https://developmentevidence.3ieimpact.org) |
| **Structured metadata** | 130+ fields per record: title, authors, DOI, abstract, sector, sub-sector, country, region, evaluation design, intervention, outcome, direction of effect, significance, SDGs, equity dimensions, transparency indicators |
| **Controlled taxonomies** | Proprietary hierarchical taxonomies for sectors (World Bank classification), DAC codes, interventions, outcomes, SDGs, equity dimensions, and WB themes |
| **Evidence Gap Maps (EGMs)** | Interactive visual maps of evidence coverage and gaps by intervention × outcome |
| **EPPI-Reviewer** | Upstream platform (UCL / EPPI Centre) used for document management, PDF storage, bibliographic import (OpenAlex, Zotero), collaborative screening, and review workflow management. **This layer is retained and remains the canonical source of truth for all PDFs and raw bibliographic records.** |
| **Admin panel (DEX)** | Web-based data extraction interface where human coders enter structured metadata from papers |
| **DEX Protocol** | Field-level extraction guidance document (IE v4), versioned changelog, sample codings |
| **Screening layer** | Studies are screened for inclusion before full DEX extraction via EPPI-Reviewer; include/exclude decisions recorded in `dep` and `exclusion_reason` fields |
| **Transparency tracking** | Dataset availability, code availability, pre-registration, pre-analysis plans, ethics approval — all structured fields |
| **Search & filter** | Public portal supports filtering by sector, geography, method, intervention, outcome, SDG, equity focus |
| **Export** | Records exportable as `.xlsx` (130+ columns, long format by study × author × intervention × outcome) |

### 1.2 Current Workflow (Human-Centric DEX)

```
New publication identified
        ↓
[EPPI-Reviewer] Bibliographic record imported
(OpenAlex / PubMed / Zotero / manual upload)
        ↓
[EPPI-Reviewer] PDF stored & linked to record
        ↓
[EPPI-Reviewer] Screener reads title + abstract
→ include/exclude decision coded in EPPI
        ↓
Included record handed off to DEP admin panel
        ↓
Coder opens PDF (from EPPI) + DEP admin panel
→ manual field-by-field DEX extraction (~20–90 mins/study)
        ↓
Quality appraisal (QA review by coordinator)
        ↓
Published to DEP public portal
```

### 1.3 Known Bottlenecks

- **Speed**: Full DEX extraction of a single study takes 20–90+ minutes by a trained human coder
- **Scale**: ~24,000 records built over years; millions of relevant papers exist in the literature
- **Consistency**: Coding decisions depend on coder interpretation despite detailed protocols
- **Coverage gaps**: Health sector currently excluded from full DEX (skeleton records only)
- **Taxonomy rigidity**: Adding new intervention/outcome codes requires manual coordinator review
- **No feedback loop**: The portal does not learn from user search behaviour or expert corrections
- **Static EGMs**: Evidence gap maps are manually curated, not dynamically updated

### 1.4 EPPI-Reviewer: The Upstream Platform (Stays)

[EPPI-Reviewer](https://eppi.ioe.ac.uk/EPPIReviewer-Web/) (v6, UCL Institute of Education) is the backbone document management and review workflow system used by 3ie. It is **not replaced** in the new DEP — it is deepened as an integration.

| EPPI Capability | Role in DEP |  
|---|---|
| PDF storage & retrieval | Canonical source of full-text papers for LLM extraction |
| Bibliographic import | Connected to OpenAlex (200M+ records), Zotero, PubMed, RIS/BibTeX |
| Collaborative screening | Title/abstract screening workflows with inter-rater reliability tools |
| Coding framework | Custom coding trees used for some structured extraction |
| Meta-analysis module | Built-in outcome data entry and forest plot generation |
| Export | RIS, Excel, CSV export for downstream processing |
| API / data access | Export hooks used to pipe included studies into the DEP admin panel |

**Integration principle**: The new DEP stack treats EPPI-Reviewer as a **trusted upstream source** — screened papers flow out of EPPI via export or API into the DEP LLM extraction pipeline. PDFs stored in EPPI are fetched on demand for full-text extraction. EPPI screening decisions populate the `dep` and `exclusion_reason` fields automatically.

---

## 2. Where LLMs Enter the Picture

### 2.1 Screening Automation (Inside EPPI-Reviewer)

**Current state**: Human reads title + abstract in EPPI-Reviewer and codes include/exclude.  
**LLM opportunity**: Deploy an AI screener *inside* the EPPI workflow — either via EPPI's machine learning screening tools (built-in) or via a custom model that writes predictions back to EPPI coding fields.

- Input: title + abstract + source metadata (from EPPI record)
- Output: include/exclude prediction + confidence score + justification, written back as an EPPI coding
- Expected gain: 80–95% of screening decisions automated at >90% recall
- Human review retained only for uncertain cases (EPPI's prioritisation/active learning queue)
- EPPI-Reviewer v6 already has built-in ML screening support — extend rather than rebuild

### 2.2 Full DEX Auto-Extraction

**Current state**: Human reads full paper and populates 130+ fields in admin panel.  
**LLM opportunity**: Use a long-context model (128k+ tokens) to extract structured fields directly from PDF/HTML paper content.

Fields addressable by LLM extraction:

| Field Group | Extractability |
|---|---|
| Title, authors, DOI, journal, year | Near-perfect (NER + metadata) |
| Abstract | Direct copy with formatting cleanup |
| Country, region | High (named entity recognition) |
| Sector / sub-sector | High with taxonomy grounding |
| Evaluation design & method | High (pattern recognition in methods sections) |
| Intervention description | Medium–High (structured prompting) |
| Outcome codes | Medium (requires taxonomy lookup + reasoning) |
| Direction & significance of effect | Medium (requires reading results tables) |
| SDGs | Medium (semantic matching) |
| Equity dimensions | Medium (requires contextual interpretation) |
| Transparency fields | High (checklist-style extraction) |
| Agency names | Medium (entity linking to Common Agencies list) |

**PDF source**: PDFs are fetched from **EPPI-Reviewer** (the canonical document store) via its export API or direct download link, not re-fetched from publishers. This avoids duplication and respects the existing document management layer.

**Architecture**: RAG pipeline (Retrieval-Augmented Generation) over chunked paper content (PDF from EPPI) + structured output via JSON schema validation against DEP taxonomies.

### 2.3 Taxonomy Expansion & Maintenance

**Current state**: New intervention/outcome codes suggested manually via tracker spreadsheet.  
**LLM opportunity**:
- Cluster "Other intervention/outcome" free-text entries to surface taxonomy gaps
- Draft new taxonomy node proposals (name, definition, parent group) for coordinator review
- Detect synonym/near-duplicate codes across the taxonomy tree

### 2.4 Quality Assurance & Consistency Checking

**Current state**: Manual QA by coordinator.  
**LLM opportunity**:
- Cross-check extracted fields for internal consistency (e.g., sector ↔ intervention alignment)
- Flag outliers vs. similar studies already in the database
- Detect likely hallucinations or mismatches between extracted data and paper content
- Generate QA summaries: "Coder extracted X but paper states Y"

### 2.5 Semantic Search & Evidence Synthesis

**Current state**: Keyword/filter search on structured fields only.  
**LLM opportunity**:
- Dense vector search over abstracts + findings (embedding models)
- Natural language query interface: *"What works for reducing stunting in Sub-Saharan Africa?"*
- Auto-generated evidence summaries across a filtered set of studies
- Dynamic EGM generation from query results
- Citation-grounded synthesis reports (RAG over the full DEP corpus)

### 2.6 Evidence Use & Policy Recommendation

**LLM opportunity**:
- Given a policy question + country context, retrieve relevant DEP evidence and synthesize actionable recommendations
- Track evidence use cases and map them to policy outcomes over time

---

## 3. Conclusion: Build a New Full-Stack DEP

The existing DEP infrastructure — a legacy admin panel, manual extraction workflows, and a static web portal — cannot scale to meet the pace of global research production or the expectations of modern evidence users.

**EPPI-Reviewer stays.** It is 3ie's proven, Cochrane-endorsed platform for systematic review management, PDF storage, and collaborative screening. Replacing it would destroy accumulated institutional knowledge and workflows for no gain.

**What gets rebuilt**: everything downstream of EPPI — the extraction pipeline, the data schema, the admin panel, the public portal, and the evidence synthesis layer. The new full-stack DEP wraps around EPPI as a first-class integration, turning it from a standalone review tool into the ingestion layer of a modern, AI-native evidence platform.

**The path forward is clear: build a new full-stack DEP that integrates with EPPI and automates everything after the PDF.**

---

## 4. Production SOTA Roadmap

### Phase 0 — Foundation (Months 1–2)

**Goal**: Establish the technical foundation before writing a single line of product code.

- [ ] Audit all existing DEP data: schema, quality, completeness, duplicates
- [ ] **Map the EPPI-Reviewer integration**: document API endpoints, export formats (RIS/Excel/CSV), PDF access method, coding framework structure, and screening workflow hooks
- [ ] Define canonical data schema v2 (normalised relational + vector-compatible)
- [ ] Set up monorepo with CI/CD (GitHub Actions), staging/prod environments
- [ ] Choose and provision cloud infrastructure (AWS / Azure / GCP)
- [ ] Select core LLM providers and establish API rate limits, cost envelopes, fallback chains
- [ ] Establish evaluation benchmarks: extraction F1, screening recall/precision, search NDCG
- [ ] Migrate existing 24k records into new schema (ETL pipeline)

**Stack decisions**:

- **EPPI-Reviewer**: retained as upstream document store and screening platform
- Backend API: **FastAPI** (Python) — async, OpenAPI spec, Pydantic validation
- Database: **PostgreSQL** (structured records) + **pgvector** or **Qdrant** (embeddings)
- Queue: **Celery + Redis** (async extraction jobs)
- Auth: **Auth0** or **Supabase Auth**
- Frontend: **Next.js 15** (App Router, RSC, Tailwind CSS)
- Infra: **Docker + Kubernetes** (EKS / GKE) or **Railway/Render** for lower ops overhead initially

---

### Phase 1 — AI Extraction Pipeline

**Goal**: Replace manual DEX with an LLM-assisted extraction engine.

- [ ] **EPPI connector**: Poll EPPI-Reviewer for newly screened-in records → retrieve bibliographic metadata + PDF via EPPI export/API → enqueue for extraction
- [ ] **Fallback ingestion**: For papers not yet in EPPI, accept DOI / URL → fetch full text (Unpaywall, Semantic Scholar, CrossRef)
- [ ] **Chunking & preprocessing**: PDF (from EPPI) → clean text (PyMuPDF / Marker), section detection (abstract, methods, results, tables)
- [ ] **Extraction agent** (LLM): Structured output extraction against DEP schema using function calling / JSON mode
  - Model: GPT-4o or Claude 3.7 Sonnet as primary; smaller fine-tuned model (Llama 3 / Mistral) as fallback
  - Taxonomy grounding: vector search against taxonomy nodes before field assignment
  - Multi-pass: separate prompts for publication info, sector/geo, methods, intervention/outcome, transparency
- [ ] **Confidence scoring**: Per-field confidence + provenance (which sentence/table in the paper)
- [ ] **Human-in-the-loop review UI**: Coder sees pre-filled form with highlights; corrects only low-confidence fields
- [ ] **Active learning loop**: corrections feed back into prompt examples and fine-tuning queue
- [ ] **Extraction benchmark**: target F1 > 0.85 on held-out gold standard records

---

### Phase 2 — Intelligent Screening (parallel)

**Goal**: Automate 85%+ of screening decisions, surfaced *within* the EPPI-Reviewer workflow.

- [ ] Train screening classifier on historical include/exclude decisions (23k+ labelled examples from EPPI export)
- [ ] Model: fine-tuned **BioBERT / SciBERT** or few-shot GPT-4o with chain-of-thought
- [ ] Output: include/exclude + justification + uncertainty score → written back into EPPI as a coding
- [ ] Review queue: uncertain cases routed to human screener in EPPI's prioritisation interface
- [ ] **EPPI ML integration**: EPPI-Reviewer v6 has a built-in machine learning screening module — evaluate whether the custom model can be plugged in directly before building a parallel system
- [ ] Integrate with external literature feeds: PubMed, Scopus, OpenAlex (already in EPPI v6), SSRN, World Bank Open Knowledge — import new records directly into EPPI
- [ ] De-duplication: semantic similarity check against existing EPPI review records before import

---

### Phase 3 — New Public Portal

**Goal**: Replace the current portal with a modern, AI-powered evidence discovery interface.

- [ ] **Semantic search**: hybrid dense + sparse retrieval (BM25 + embedding similarity) over full corpus
- [ ] **Natural language query**: LLM query understanding → structured filter generation + retrieval
- [ ] **Evidence synthesis**: RAG-powered summaries over filtered result sets, with citations
- [ ] **Dynamic EGMs**: auto-generated intervention × outcome evidence maps from any query
- [ ] **Study page**: rich record view with full extraction, linked publications, AI-generated synopsis
- [ ] **Export API**: REST + GraphQL endpoints for downstream use (R packages, Stata, Python)
- [ ] **Policy question interface**: *"What reduces child stunting in LMICs?"* → ranked evidence + synthesis
- [ ] Accessibility: WCAG 2.1 AA, mobile-first, multilingual (English + French + Spanish at launch)

---

### Phase 4 — Quality & Governance Layer
**Goal**: Ensure extraction quality meets or exceeds human baseline.

- [ ] **Automated QA checks**: internal consistency rules (sector ↔ intervention, country ↔ region)
- [ ] **Disagreement detection**: flag AI extractions that diverge from human extractions in similar studies
- [ ] **Coder dashboard**: track extraction speed, accuracy, and QA pass rates per coder
- [ ] **Coordinator tools**: approve/reject AI suggestions, manage taxonomy, view audit trail
- [ ] **Provenance tracking**: every field stores source (human / AI model version / correction date)
- [ ] **Version history**: full diff history per record
- [ ] **Taxonomy management UI**: propose, review, and merge new taxonomy nodes

---

### Phase 5 — Scale & Ecosystem

**Goal**: Expand coverage, open up the platform, and build the evidence ecosystem.

- [ ] **Continuous ingestion**: automated monitoring of OpenAlex, PubMed, SSRN, World Bank for new papers
- [ ] **Health sector re-integration**: full DEX for health studies (currently excluded)
- [ ] **SR-specific pipeline**: extraction of included studies, PICO framework, meta-analytic results
- [ ] **R / Stata / Python packages**: `depr` / `dep.ado` / `pydep` for programmatic access
- [ ] **Open API**: documented public REST API with rate limiting and API keys
- [ ] **Embeddable EGMs**: iframe / widget for external websites
- [ ] **Evidence use tracking**: record citations, policy documents, and evaluations that reference DEP studies
- [ ] **Partner integrations**: Campbell Collaboration, Cochrane, What Works Clearinghouse, J-PAL

---

### Phase 6 — Advanced AI Features

**Goal**: Push toward autonomous evidence synthesis and policy intelligence.

- [ ] **Fine-tuned domain model**: train a `DEP-7B` open-weight LLM on all extracted paper content + protocol
- [ ] **Multi-document reasoning**: synthesize evidence across 10s–100s of studies on a topic
- [ ] **Causal inference metadata**: structured representation of identification strategies, threats to validity
- [ ] **Effect size standardisation**: extract and normalise effect sizes across studies for quantitative synthesis
- [ ] **Bayesian evidence updating**: model cumulative evidence strength over time per intervention × outcome cell
- [ ] **Policy recommendation engine**: country-specific evidence briefs generated on demand
- [ ] **Feedback flywheel**: user queries, expert corrections, and policy use cases continuously improve all models

---

## 5. Key Metrics for Success

| Metric | Baseline (Current) | Target (Phase 3) | Target (Phase 6) |
|---|---|---|---|
| Records in database | ~24,000 | 50,000 | 200,000+ |
| Extraction time / study | 20–90 min | <5 min (AI-assisted) | <1 min (autonomous) |
| Screening automation rate | 0% | 85% | 95% |
| Extraction F1 vs. gold standard | N/A (human) | >0.85 | >0.92 |
| Monthly active portal users | ~5,000 | 25,000 | 100,000+ |
| API partners / integrations | 0 | 5 | 20+ |
| Time from paper → published record | Weeks–months | <48 hours | <4 hours |

---
