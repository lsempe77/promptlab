"""Definitions for the 5 fields being prototyped in v1 (author, institution,
country of institution, sector, sub-sector). Each spec captures: where the
ground truth comes from in the raw ier-records export, what shape the value
takes (single value vs. list), and how it should be scored/typed in prompts.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FieldSpec:
    name: str
    label: str
    value_type: str  # "single_categorical" | "list_categorical" | "list_text"
    taxonomy_key: str | None  # key into taxonomy.json, or None for free text
    description: str  # shown to the model in the extraction prompt


FIELDS: dict[str, FieldSpec] = {
    "authors": FieldSpec(
        name="authors",
        label="Author names",
        value_type="list_text",
        taxonomy_key=None,
        description=(
            "List EVERY author of the paper, one entry per author, in the order they appear in the "
            "title/author block \u2014 check for co-authors named after the first author and in footnotes, "
            "do not stop at the first name you find. Format each as 'Last name, First name Middle name' "
            "(e.g. 'Sabet, Shayda Mae'). If the paper gives only initials for the first/middle name, keep "
            "the initials exactly as printed (e.g. 'Miranda, J. M.') \u2014 do not guess or invent a full name "
            "you are not certain of from the text."
        ),
    ),
    "author_affiliation": FieldSpec(
        name="author_affiliation",
        label="Author institution(s)",
        value_type="list_text",
        taxonomy_key=None,
        description=(
            "The institution(s)/organization(s) that authors are affiliated with (university, research "
            "center, government agency, NGO). Report ONLY the PRIMARY parent institution for each author "
            "(e.g. 'Cornell University'), NOT its internal departments, schools, centers or programs "
            "(e.g. do not list 'Atkinson Center for a Sustainable Future' or 'Master of Public Health "
            "Program' separately from their parent university). List each distinct parent institution "
            "once, using its full name with any abbreviation in brackets (e.g. 'International Initiative "
            "for Impact Evaluation (3ie)'). Treat known name variants of the same organization as one "
            "(e.g. 'ICDDR,B' and 'Centre for Health and Population Research' are the same institution). "
            "If an author's affiliation is not stated in the paper, do not invent one.\n\n"
            "CRITICAL RULE FOR PRECISION: If an author lists a department, school, center, or lab "
            "affiliation, extract ONLY the parent institution and discard the department. For example:\n"
            "- 'Department of Economics, Harvard University' → report 'Harvard University' (NOT "
            "'Department of Economics, Harvard University')\n"
            "- 'School of Public Health, Johns Hopkins University' → report 'Johns Hopkins University'\n"
            "- 'Center for Global Health, University of Nairobi' → report 'University of Nairobi'\n"
            "Do NOT include the department, school, center, faculty, or lab name in the value. Only the "
            "parent university/organization name.\n"
            "Also do NOT list the same institution twice if two authors are from the same university — "
            "list each distinct institution only once. Fewer, correct parent institutions is better than "
            "many department-level entries."
        ),
    ),
    "author_country": FieldSpec(
        name="author_country",
        label="Author institution country",
        value_type="list_categorical",
        taxonomy_key="countries",
        description=(
            "The country/countries where EACH author's institutional affiliation is located \u2014 check every "
            "co-author's affiliation (title page, footnotes, acknowledgments), not just the first author, and "
            "report one country per distinct institution (there may be several if co-authors are affiliated "
            "with institutions in different countries). If an affiliation names an organization that has "
            "country offices worldwide (e.g. 'World Bank', 'JPAL') without specifying a particular office, use "
            "the country of that organization's headquarters (e.g. 'JPAL' alone -> United States; 'JPAL Africa' "
            "-> South Africa; 'World Bank' -> United States). Use standard country names (e.g. 'United States', "
            "not 'USA' or 'US'). If a country cannot be determined from the paper, output "
            "'Not specified' rather than guessing."
        ),
    ),
    "sector_name": FieldSpec(
        name="sector_name",
        label="Sector",
        value_type="single_categorical",
        taxonomy_key="sectors",
        description=(
            "Select the single World Bank sector that is the PRIMARY focus of the paper. "
            "Papers often touch multiple sectors; pick the one that is most central to the intervention evaluated.\n\n"
            "Sector definitions:\n"
            "- Agriculture fishing and forestry: Primary food/raw-material production — crop cultivation, livestock, "
            "irrigation, fisheries, forestry. NOT agri-business or trade.\n"
            "- Education: Learning systems — early childhood, primary, secondary, tertiary, vocational training, adult literacy.\n"
            "- Energy and extractives: Power generation, oil/gas extraction, mining, energy transmission and distribution.\n"
            "- Financial sector: Banking, insurance, capital markets, microfinance, financial inclusion for the unbanked.\n"
            "- Health: Medical care, disease prevention/treatment, nutrition programmes, maternal/child health, "
            "health system capacity — any intervention delivered through clinics, hospitals or health workers.\n"
            "- Social protection: Cash transfers (conditional or unconditional), food vouchers, pensions, social "
            "insurance, safety nets, labour market programmes. Distinguishable from Health because the core "
            "intervention is a monetary transfer or entitlement, not medical care.\n"
            "- Industry trade and services: Manufacturing, trade policy, agri-business/commercialisation, tourism, "
            "housing construction, retail and hospitality services.\n"
            "- Information and communications technologies: Internet infrastructure, mobile networks, digital services.\n"
            "- Public administration: Governance reform, institutional capacity, law and justice, decentralisation — "
            "ONLY when government management or governance itself is the primary subject, NOT when the paper "
            "evaluates a programme that happens to be administered by government.\n"
            "- Transportation: Roads, railways, aviation, ports, urban transit.\n"
            "- Water sanitation and waste management: Water supply, sanitation facilities, sewerage, solid-waste management.\n\n"
            "Key disambiguation rules:\n"
            "1. Health vs Social protection: A deworming campaign → Health. A conditional cash transfer requiring "
            "health check-ups → Social protection. A vitamin supplementation programme → Health.\n"
            "2. Public administration vs any sector: A paper about reforming the health ministry → Health "
            "(not Public administration). A paper about fiscal decentralisation itself → Public administration.\n"
            "3. Agriculture vs Industry/trade: Growing maize → Agriculture. Selling/marketing maize or "
            "agri-business → Industry trade and services."
        ),
    ),
    "sub_sector": FieldSpec(
        name="sub_sector",
        label="Sub-sector",
        value_type="single_categorical",
        taxonomy_key="sub_sectors_flat",
        description=(
            "Select the most specific World Bank sub-sector using a two-step approach:\n\n"
            "STEP 1 — Identify the main sector from the 11 options: Agriculture fishing and forestry | "
            "Education | Energy and extractives | Financial sector | Health | Social protection | "
            "Industry trade and services | Information and communications technologies | "
            "Public administration | Transportation | Water sanitation and waste management\n\n"
            "STEP 2 — Pick the sub-sector ONLY from within that sector:\n"
            "Agriculture fishing and forestry → Crops | Livestock | Irrigation and drainage | "
            "Agricultural extension, research and other support activities | Forestry | Fisheries | "
            "Public admin - Agriculture, fishing and forestry | Other - Agriculture, fishing and forestry\n"
            "Education → Early childhood education | Primary education | Secondary education | "
            "Tertiary education | Workforce development and vocational education | "
            "Adult basic and continuing education | Public admin - Education | Other - Education\n"
            "Energy and extractives → Mining | Oil and gas | Renewable energy - Hydro | "
            "Renewable energy - Solar | Renewable energy - Wind | Renewable energy - Biomass | "
            "Renewable energy - Geothermal | Non-renewable energy generation | "
            "Energy transmission and distribution | Public admin - Energy and extractives | "
            "Other - Energy and extractives\n"
            "Financial sector → Banking institutions | Insurance and pension | Capital markets | "
            "Financial Inclusion | Other non-bank financial institutions | Public admin - Financial sector\n"
            "Health → Health | Health facilities and construction | Public admin - Health\n"
            "Social protection → Social protection | Public admin - Social protection\n"
            "Industry trade and services → Agricultural markets, commercialization and agri-business | "
            "Housing contruction | Trade | Services | Manufacturing | Tourism | "
            "Public admin - Industry, trade and services | Other - Industry, trade and services\n"
            "Information and communications technologies → ICT infrastructure | ICT services | "
            "Public admin - Information and communications technologies | "
            "Other - Information and communications technologies\n"
            "Public administration → Law and justice | Central government or agencies | "
            "Sub-national government | Other - Public administration\n"
            "Transportation → Rural and inter-urban roads | Railways | Aviation | Ports/waterways | "
            "Urban transportation | Public admin - Transportation | Other - Transportation\n"
            "Water sanitation and waste management → Sanitation | Waste management | Water supply | "
            "Public admin - Water, sanitation and waste management | "
            "Other - Water, sanitation and waste management\n\n"
            "Rules: Use 'Other - [Sector]' only when no specific sub-sector fits. "
            "Use 'Public admin - [Sector]' only when the paper is about managing the sector's "
            "administration, not evaluating service delivery."
        ),
    ),
}
