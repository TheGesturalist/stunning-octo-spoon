#!/usr/bin/env bash
# =============================================================================
# GESTURALIST HQ — Johnny Decimal Folder Structure Generator
# =============================================================================
# Version: 2.0.0
# Created: 2025-12-06
# 
# FEATURES:
# - Modified Johnny Decimal: ACC.ID format (3-digit category + 2-digit ID)
# - Distributed persona lenses (X05-X09 in each area)
# - Centralized persona registry (05-09)
# - Explicit zettel staging layer
# - Seed files for immediate use
#
# USAGE:
#   chmod +x create_gesturalist_hq.sh
#   ./create_gesturalist_hq.sh [target_directory]
#
# =============================================================================

set -euo pipefail

# -----------------------------------------------------------------------------
# CONFIGURATION
# -----------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
NC='\033[0m'

ROOT_DIR="${1:-/Users/themainframe/locals_only/G01_gesturalism/gesturalist_reference}"
TODAY=$(date +%Y-%m-%d)

DIRS_CREATED=0
DIRS_EXISTED=0
FILES_CREATED=0

# -----------------------------------------------------------------------------
# LOGGING FUNCTIONS
# -----------------------------------------------------------------------------
log_info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[✓]${NC} $1"; ((DIRS_CREATED++)) || true; }
log_file()    { echo -e "${MAGENTA}[+]${NC} $1"; ((FILES_CREATED++)) || true; }
log_exists()  { echo -e "${YELLOW}[~]${NC} $1"; ((DIRS_EXISTED++)) || true; }
log_section() { echo -e "\n${CYAN}━━━ $1 ━━━${NC}"; }

# -----------------------------------------------------------------------------
# CORE FUNCTIONS
# -----------------------------------------------------------------------------
create_dir() {
    local dir_path="$1"
    if [[ ! -d "$dir_path" ]]; then
        mkdir -p "$dir_path"
        log_success "Created: $dir_path"
    else
        log_exists "Exists:  $dir_path"
    fi
}

create_file() {
    local file_path="$1"
    local content="$2"
    if [[ ! -f "$file_path" ]]; then
        echo "$content" > "$file_path"
        log_file "Created: $file_path"
    else
        log_exists "Exists:  $file_path"
    fi
}

create_standard_zeros() {
    local category_path="$1"
    local category_code="$2"
    
    create_dir "${category_path}/${category_code}.00_index"
    create_dir "${category_path}/${category_code}.01_inbox"
    create_dir "${category_path}/${category_code}.02_tasks-projects"
    create_dir "${category_path}/${category_code}.03_templates"
    create_dir "${category_path}/${category_code}.04_mocs"
    create_dir "${category_path}/${category_code}.08_someday"
    create_dir "${category_path}/${category_code}.09_archive"
}

# -----------------------------------------------------------------------------
# PERSONA LENSES: Creates X05-X09 within an area
# Usage: create_persona_lenses <area_path> <area_prefix>
# Example: create_persona_lenses "./10-19_language_theory" "1"
# -----------------------------------------------------------------------------
create_persona_lenses() {
    local area_path="$1"
    local prefix="$2"  # Single digit: 1, 2, 3, etc.
    
    create_dir "${area_path}/${prefix}05_persona-traces"
    create_dir "${area_path}/${prefix}06_voice-experiments"
    create_dir "${area_path}/${prefix}07_dialogues"
    create_dir "${area_path}/${prefix}08_persona-mocs"
    create_dir "${area_path}/${prefix}09_retired-traces"
}

# -----------------------------------------------------------------------------
# UTILITY FUNCTIONS (available after sourcing)
# -----------------------------------------------------------------------------
jd_create_category_range() {
    local area_path="$1"
    local start="$2"
    local end="$3"
    local name_pattern="${4:-unnamed}"
    
    for i in $(seq "$start" "$end"); do
        local padded=$(printf "%03d" "$i")
        create_dir "${area_path}/${padded}_${name_pattern}"
    done
}

jd_create_id_range() {
    local category_path="$1"
    local start="$2"
    local end="$3"
    local name_pattern="${4:-reserved}"
    
    local category_name=$(basename "$category_path")
    local category_code="${category_name:0:3}"
    
    for i in $(seq "$start" "$end"); do
        local padded=$(printf "%02d" "$i")
        create_dir "${category_path}/${category_code}.${padded}_${name_pattern}"
    done
}

# =============================================================================
# SEED FILE CONTENT
# =============================================================================

read -r -d '' JDEX_CONTENT << 'JDEX_EOF' || true
---
jd:
  code: "000.00"
  title: "jdex-master-index"
  status: active
  created: {{DATE}}
  updated: {{DATE}}
tags:
  - index
  - jdex
---

# JDex — Master Index

> The canonical map of Gesturalist HQ.

## Area Map

| Range | Name | Index | Status |
|-------|------|-------|--------|
| [[000.00_index\|00–04]] | System | [[000_system-index]] | 🟢 Active |
| [[050.00_index\|05–09]] | Persona Registry | [[050_persona-registry]] | 🟢 Active |
| [[100.00_index\|10–19]] | Language & Theory | [[100_philosophy-core]] | 🟢 Active |
| [[200.00_index\|20–29]] | Gesturalism Lab | [[200_framework-core]] | 🟢 Active |
| [[300.00_index\|30–39]] | Research Dossiers | [[300_bibliographies]] | 🟢 Active |
| [[400.00_index\|40–49]] | Writing Studio | [[400_essays]] | 🟢 Active |
| [[500.00_index\|50–59]] | Editorial | [[500_editorial-pipeline]] | 🟢 Active |
| [[600.00_index\|60–69]] | Media Lab | [[600_images-collage]] | 🟢 Active |
| [[700.00_index\|70–79]] | Islands Studies | [[700_theory-history]] | 🟢 Active |
| [[800.00_index\|80–89]] | Knowledge Systems | [[800_obsidian]] | 🟢 Active |
| [[900.00_index\|90–99]] | Public/Web | [[900_website]] | 🟢 Active |

## Quick Links

- **Inbox**: [[000.01_inbox]]
- **Active Tasks**: [[000.02_tasks-projects]]
- **Templates**: [[000.03_templates]]
- **Global MOC**: [[000.04_mocs]]

## Zettel Layer

- **Staging**: [[zettels/]]
- **Recent Zettels**: `= this.file.lists.where(l => l.path.startsWith("zettels")).limit(10)`

## Dataview: Recent Activity

```dataview
TABLE WITHOUT ID
  file.link AS "Note",
  jd.code AS "Code",
  file.mtime AS "Modified"
FROM ""
WHERE jd.code
SORT file.mtime DESC
LIMIT 15
```
JDEX_EOF

read -r -d '' TEMPLATE_STANDARD << 'TEMPLATE_EOF' || true
---
jd:
  code: "{{VALUE:code}}"
  area: "{{VALUE:area}}"
  category: "{{VALUE:category}}"
  id: "{{VALUE:id}}"
  title: "{{VALUE:title}}"
  status: active
  created: {{DATE:YYYY-MM-DD}}
  updated: {{DATE:YYYY-MM-DD}}
persona_lens: null
resonance: []
tags: []
---

# {{VALUE:title}}

## Summary



## Notes



## Connections

- Resonates with: 
- Contradicts: 
- Extends: 

---

*Last updated: {{DATE:YYYY-MM-DD}}*
TEMPLATE_EOF

read -r -d '' TEMPLATE_PERSONA << 'TEMPLATE_EOF' || true
---
jd:
  code: "{{VALUE:code}}"
  area: "05-09_persona_registry"
  category: "051_fictional-voices"
  id: "{{VALUE:id}}"
  title: "{{VALUE:name}}"
  status: active
  created: {{DATE:YYYY-MM-DD}}
  updated: {{DATE:YYYY-MM-DD}}
persona:
  name: "{{VALUE:name}}"
  type: fictional
  voice_traits: []
  epistemology: ""
  blind_spots: []
  obsessions: []
  key_tensions: []
tags:
  - persona
  - voice/{{VALUE:name_lower}}
---

# {{VALUE:name}}

> *A voice for thinking through...*

## Character Sheet

**Voice Traits**: 

**Epistemology**: How does this voice know things?

**Blind Spots**: What can this voice not see?

**Obsessions**: What does this voice return to?

**Key Tensions**: What internal conflicts drive this voice?

## Domain Traces

> Links to where this voice has spoken across the vault.

### Language & Theory (10-19)
```dataview
LIST FROM "10-19_language_theory"
WHERE contains(persona_lens, "{{VALUE:name}}")
```

### Gesturalism Lab (20-29)
```dataview
LIST FROM "20-29_gesturalism_lab"
WHERE contains(persona_lens, "{{VALUE:name}}")
```

### Islands Studies (70-79)
```dataview
LIST FROM "70-79_islands_archipelago_studies"
WHERE contains(persona_lens, "{{VALUE:name}}")
```

## Voice Samples

> Characteristic phrases, tones, gestures.

- 
- 
- 

---

*Created: {{DATE:YYYY-MM-DD}}*
TEMPLATE_EOF

read -r -d '' TEMPLATE_ZETTEL << 'TEMPLATE_EOF' || true
---
zettel:
  id: "{{DATE:YYYYMMDDHHmmss}}"
  created: {{DATE:YYYY-MM-DD}}
  status: unfiled
  destination: null
tags:
  - zettel
  - stage/seed
---

# {{VALUE:title}}

{{VALUE:idea}}

---

## Links

- 
- 

## Filing Notes

> Where should this crystallize into?
> 
> Candidate locations:
> - [ ] 
TEMPLATE_EOF

read -r -d '' TEMPLATE_DAILY << 'TEMPLATE_EOF' || true
---
daily:
  date: {{DATE:YYYY-MM-DD}}
  voice: authorial
tags:
  - daily
  - voice/authorial
---

# {{DATE:dddd, MMMM D, YYYY}}

## Morning Orientation

What's alive today?

## Working Notes

### 

## Captures

- 

## End of Day

### Crystallization Candidates

> What from today should become a zettel or permanent note?

- [ ] 

### Tomorrow

- 
TEMPLATE_EOF

read -r -d '' TEMPLATE_MOC << 'TEMPLATE_EOF' || true
---
jd:
  code: "{{VALUE:code}}"
  title: "{{VALUE:title}}-moc"
  status: active
  created: {{DATE:YYYY-MM-DD}}
  type: moc
tags:
  - moc
  - {{VALUE:area}}
---

# {{VALUE:title}} — Map of Content

## Overview



## Key Notes

```dataview
TABLE WITHOUT ID
  file.link AS "Note",
  jd.code AS "Code",
  jd.status AS "Status"
FROM "{{VALUE:folder_path}}"
WHERE jd.code
SORT jd.code ASC
```

## Cross-Area Connections

> Notes in this area that resonate with other domains.

```dataview
TABLE WITHOUT ID
  file.link AS "Note",
  resonance AS "Connects To"
FROM "{{VALUE:folder_path}}"
WHERE resonance AND length(resonance) > 0
```

## Persona Traces

> Voices active in this domain.

```dataview
LIST FROM "{{VALUE:folder_path}}"
WHERE persona_lens
GROUP BY persona_lens
```
TEMPLATE_EOF

read -r -d '' ZETTEL_README << 'ZETTELREADME_EOF' || true
---
jd:
  code: "zettel.00"
  title: "zettel-layer-guide"
  status: active
tags:
  - meta
  - zettel
---

# Zettel Layer — Usage Guide

> The staging ground for atomic thoughts before they find their place in the JD structure.

## What Belongs Here

1. **Fleeting notes** — Quick captures that need processing
2. **Atomic ideas** — Single, complete thoughts (one idea per note)
3. **Unfiled insights** — Thoughts that emerged but don't yet have a clear home
4. **Cross-pollinations** — Ideas that span multiple categories

## What Doesn't Belong Here (for long)

- Reference material → goes to `30-39_research_dossiers`
- Project notes → goes to relevant category
- Character development → goes to `05-09` or distributed `X05-X09`

## Zettel Lifecycle

```
┌──────────────┐
│   CAPTURE    │  Write in dailies or directly here
└──────┬───────┘
       ↓
┌──────────────┐
│   STAGING    │  Lives in /zettels/ with status: unfiled
└──────┬───────┘
       ↓ (weekly review)
┌──────────────┐
│  CRYSTALLIZE │  Move to JD category, update frontmatter
└──────┬───────┘
       ↓
┌──────────────┐
│   CONNECT    │  Add to MOCs, populate resonance field
└──────────────┘
```

## Naming Convention

Zettels use timestamp-based IDs: `YYYYMMDDHHmmss-title.md`

Example: `20251206143022-gesture-as-threshold.md`

## Weekly Review Questions

1. What's been sitting here more than 7 days?
2. Where does each unfiled zettel want to live?
3. What connections have emerged between zettels?
4. Which zettels are actually fragments of the same idea?

## Dataview: Unfiled Zettels

```dataview
TABLE WITHOUT ID
  file.link AS "Zettel",
  zettel.created AS "Created",
  dateformat(date(now) - file.ctime, "d") + " days" AS "Age"
FROM "zettels"
WHERE zettel.status = "unfiled"
SORT file.ctime ASC
```

## Dataview: Ready to File

```dataview
LIST FROM "zettels"
WHERE zettel.destination != null
```
ZETTELREADME_EOF

read -r -d '' PERSONA_REGISTRY_INDEX << 'PERSONA_REGISTRY_EOF' || true
---
jd:
  code: "050.00"
  title: "persona-registry-index"
  status: active
  created: {{DATE}}
tags:
  - index
  - persona
---

# Persona Registry — Master Index

> The canonical definitions of all voices in the vault.

## Active Voices

```dataview
TABLE WITHOUT ID
  file.link AS "Persona",
  persona.type AS "Type",
  persona.epistemology AS "Knows Via"
FROM "05-09_persona_registry/051_fictional-voices"
WHERE persona.name
```

## Theoretical Interlocutors

```dataview
LIST FROM "05-09_persona_registry/052_theoretical-interlocutors"
WHERE persona.name
```

## How Personas Work

### Central Registry (Here: 05-09)
This area holds the **canonical character sheets**. Each persona has ONE authoritative definition.

### Distributed Traces (X05-X09 in each area)
Each numbered area (10-19, 20-29, etc.) contains its own persona sub-zone:
- `105-109` — Persona traces in Language & Theory
- `205-209` — Persona traces in Gesturalism Lab
- `705-709` — Persona traces in Islands Studies

### Why This Structure?

> "Elena-in-phenomenology is not the same as Elena-in-islands. Identity is relational; voice mutates by domain."

The distributed structure allows characters to **become through encounter**. The central registry maintains coherence while the traces capture transformation.

## Cross-Vault Voice Activity

```dataview
TABLE WITHOUT ID
  jd.area AS "Area",
  length(rows) AS "Traces"
FROM ""
WHERE persona_lens
GROUP BY jd.area
```
PERSONA_REGISTRY_EOF

# =============================================================================
# MAIN STRUCTURE CREATION
# =============================================================================

main() {
    echo ""
    echo "╔═══════════════════════════════════════════════════════════════════════════╗"
    echo "║           GESTURALIST HQ — Johnny Decimal Structure Generator             ║"
    echo "║               ACC.ID Variant • Distributed Personas • Zettel Layer        ║"
    echo "╚═══════════════════════════════════════════════════════════════════════════╝"
    echo ""
    log_info "Target directory: ${ROOT_DIR}"
    log_info "Date: ${TODAY}"
    echo ""

    # -------------------------------------------------------------------------
    # ZETTEL LAYER (root level)
    # -------------------------------------------------------------------------
    log_section "ZETTEL LAYER"

    create_dir "${ROOT_DIR}/zettels"
    create_dir "${ROOT_DIR}/zettels/unfiled"
    create_dir "${ROOT_DIR}/zettels/processing"
    
    # Zettel readme
    local zettel_readme="${ZETTEL_README//\{\{DATE\}\}/$TODAY}"
    create_file "${ROOT_DIR}/zettels/_zettel-guide.md" "$zettel_readme"

    # -------------------------------------------------------------------------
    # DAILIES (root level)
    # -------------------------------------------------------------------------
    log_section "DAILIES"

    create_dir "${ROOT_DIR}/dailies"
    create_dir "${ROOT_DIR}/dailies/2025"
    create_dir "${ROOT_DIR}/dailies/2025/12"

    # -------------------------------------------------------------------------
    # 00-04_system
    # -------------------------------------------------------------------------
    log_section "00-04 SYSTEM"

    create_dir "${ROOT_DIR}/00-04_system/000_system-index"
    create_standard_zeros "${ROOT_DIR}/00-04_system/000_system-index" "000"

    create_dir "${ROOT_DIR}/00-04_system/001_conventions-styleguide"
    create_dir "${ROOT_DIR}/00-04_system/002_automations-scripts"
    create_dir "${ROOT_DIR}/00-04_system/003_schemas-metadata"
    create_dir "${ROOT_DIR}/00-04_system/004_logs-changelog"

    # System persona lenses (005-009)
    create_persona_lenses "${ROOT_DIR}/00-04_system" "0"

    # Seed files for system
    local jdex="${JDEX_CONTENT//\{\{DATE\}\}/$TODAY}"
    create_file "${ROOT_DIR}/00-04_system/000_system-index/000.00_index/jdex.md" "$jdex"
    create_file "${ROOT_DIR}/00-04_system/000_system-index/000.03_templates/tpl-standard-note.md" "$TEMPLATE_STANDARD"
    create_file "${ROOT_DIR}/00-04_system/000_system-index/000.03_templates/tpl-persona.md" "$TEMPLATE_PERSONA"
    create_file "${ROOT_DIR}/00-04_system/000_system-index/000.03_templates/tpl-zettel.md" "$TEMPLATE_ZETTEL"
    create_file "${ROOT_DIR}/00-04_system/000_system-index/000.03_templates/tpl-daily.md" "$TEMPLATE_DAILY"
    create_file "${ROOT_DIR}/00-04_system/000_system-index/000.03_templates/tpl-moc.md" "$TEMPLATE_MOC"

    # -------------------------------------------------------------------------
    # 05-09_persona_registry (CENTRAL — canonical voice definitions)
    # -------------------------------------------------------------------------
    log_section "05-09 PERSONA REGISTRY (Central)"

    create_dir "${ROOT_DIR}/05-09_persona_registry/050_persona-index"
    create_standard_zeros "${ROOT_DIR}/05-09_persona_registry/050_persona-index" "050"

    create_dir "${ROOT_DIR}/05-09_persona_registry/051_fictional-voices"
    create_dir "${ROOT_DIR}/05-09_persona_registry/052_theoretical-interlocutors"
    create_dir "${ROOT_DIR}/05-09_persona_registry/053_historical-personae"
    create_dir "${ROOT_DIR}/05-09_persona_registry/054_composite-figures"
    create_dir "${ROOT_DIR}/05-09_persona_registry/059_retired-voices"

    # Persona registry index
    local persona_index="${PERSONA_REGISTRY_INDEX//\{\{DATE\}\}/$TODAY}"
    create_file "${ROOT_DIR}/05-09_persona_registry/050_persona-index/050.00_index/persona-registry.md" "$persona_index"

    # -------------------------------------------------------------------------
    # 10-19_language_theory
    # -------------------------------------------------------------------------
    log_section "10-19 LANGUAGE & THEORY"

    create_dir "${ROOT_DIR}/10-19_language_theory/100_philosophy-core"
    create_standard_zeros "${ROOT_DIR}/10-19_language_theory/100_philosophy-core" "100"

    # Distributed persona lenses (105-109)
    create_persona_lenses "${ROOT_DIR}/10-19_language_theory" "1"

    create_dir "${ROOT_DIR}/10-19_language_theory/112_semiotics-logic"
    create_dir "${ROOT_DIR}/10-19_language_theory/120_phenomenology"
    create_dir "${ROOT_DIR}/10-19_language_theory/130_post-structuralism"
    create_dir "${ROOT_DIR}/10-19_language_theory/140_narratology"
    create_dir "${ROOT_DIR}/10-19_language_theory/150_aesthetics-mysticism"

    # -------------------------------------------------------------------------
    # 20-29_gesturalism_lab
    # -------------------------------------------------------------------------
    log_section "20-29 GESTURALISM LAB"

    create_dir "${ROOT_DIR}/20-29_gesturalism_lab/200_framework-core"
    create_standard_zeros "${ROOT_DIR}/20-29_gesturalism_lab/200_framework-core" "200"

    # Distributed persona lenses (205-209)
    create_persona_lenses "${ROOT_DIR}/20-29_gesturalism_lab" "2"

    create_dir "${ROOT_DIR}/20-29_gesturalism_lab/210_language-as-gesture"
    create_dir "${ROOT_DIR}/20-29_gesturalism_lab/220_subjectivity-temporality"
    create_dir "${ROOT_DIR}/20-29_gesturalism_lab/230_islands-archipelagos"
    create_dir "${ROOT_DIR}/20-29_gesturalism_lab/240_ooo-and-related"
    create_dir "${ROOT_DIR}/20-29_gesturalism_lab/250_methods-patterns"
    create_dir "${ROOT_DIR}/20-29_gesturalism_lab/260_prompts-scores"
    create_dir "${ROOT_DIR}/20-29_gesturalism_lab/270_lexicon-glossary"

    # -------------------------------------------------------------------------
    # 30-39_research_dossiers
    # -------------------------------------------------------------------------
    log_section "30-39 RESEARCH DOSSIERS"

    create_dir "${ROOT_DIR}/30-39_research_dossiers/300_bibliographies"
    create_standard_zeros "${ROOT_DIR}/30-39_research_dossiers/300_bibliographies" "300"

    # Distributed persona lenses (305-309)
    create_persona_lenses "${ROOT_DIR}/30-39_research_dossiers" "3"

    create_dir "${ROOT_DIR}/30-39_research_dossiers/310_primary-texts-notes"
    create_dir "${ROOT_DIR}/30-39_research_dossiers/320_secondary-criticism"
    create_dir "${ROOT_DIR}/30-39_research_dossiers/330_field-notes-interviews"
    create_dir "${ROOT_DIR}/30-39_research_dossiers/340_datasets-quotations"
    create_dir "${ROOT_DIR}/30-39_research_dossiers/350_timelines-chronologies"
    create_dir "${ROOT_DIR}/30-39_research_dossiers/360_maps-diagrams"

    # -------------------------------------------------------------------------
    # 40-49_writing_studio
    # -------------------------------------------------------------------------
    log_section "40-49 WRITING STUDIO"

    create_dir "${ROOT_DIR}/40-49_writing_studio/400_essays"
    create_standard_zeros "${ROOT_DIR}/40-49_writing_studio/400_essays" "400"

    # Distributed persona lenses (405-409)
    create_persona_lenses "${ROOT_DIR}/40-49_writing_studio" "4"

    create_dir "${ROOT_DIR}/40-49_writing_studio/410_novel-projects"
    create_dir "${ROOT_DIR}/40-49_writing_studio/420_poetry-fragments"
    create_dir "${ROOT_DIR}/40-49_writing_studio/430_hybrid-forms"
    create_dir "${ROOT_DIR}/40-49_writing_studio/440_scripts-dialogues"
    create_dir "${ROOT_DIR}/40-49_writing_studio/450_grants-proposals"
    create_dir "${ROOT_DIR}/40-49_writing_studio/460_submissions-tracking"

    # -------------------------------------------------------------------------
    # 50-59_editorial_publishing
    # -------------------------------------------------------------------------
    log_section "50-59 EDITORIAL & PUBLISHING"

    create_dir "${ROOT_DIR}/50-59_editorial_publishing/500_editorial-pipeline"
    create_standard_zeros "${ROOT_DIR}/50-59_editorial_publishing/500_editorial-pipeline" "500"

    # Distributed persona lenses (505-509)
    create_persona_lenses "${ROOT_DIR}/50-59_editorial_publishing" "5"

    create_dir "${ROOT_DIR}/50-59_editorial_publishing/510_revisions"
    create_dir "${ROOT_DIR}/50-59_editorial_publishing/520_peer-readers"
    create_dir "${ROOT_DIR}/50-59_editorial_publishing/530_copyedit-proof"
    create_dir "${ROOT_DIR}/50-59_editorial_publishing/540_layout-typesetting"
    create_dir "${ROOT_DIR}/50-59_editorial_publishing/550_imprint-zines"

    # -------------------------------------------------------------------------
    # 60-69_media_lab
    # -------------------------------------------------------------------------
    log_section "60-69 MEDIA LAB"

    create_dir "${ROOT_DIR}/60-69_media_lab/600_images-collage"
    create_standard_zeros "${ROOT_DIR}/60-69_media_lab/600_images-collage" "600"

    # Distributed persona lenses (605-609)
    create_persona_lenses "${ROOT_DIR}/60-69_media_lab" "6"

    create_dir "${ROOT_DIR}/60-69_media_lab/610_video-motion"
    create_dir "${ROOT_DIR}/60-69_media_lab/620_audio-music-voice"
    create_dir "${ROOT_DIR}/60-69_media_lab/630_installations"
    create_dir "${ROOT_DIR}/60-69_media_lab/640_exhibitions-print"

    # -------------------------------------------------------------------------
    # 70-79_islands_archipelago_studies
    # -------------------------------------------------------------------------
    log_section "70-79 ISLANDS & ARCHIPELAGO STUDIES"

    create_dir "${ROOT_DIR}/70-79_islands_archipelago_studies/700_theory-history"
    create_standard_zeros "${ROOT_DIR}/70-79_islands_archipelago_studies/700_theory-history" "700"

    # Distributed persona lenses (705-709)
    create_persona_lenses "${ROOT_DIR}/70-79_islands_archipelago_studies" "7"

    create_dir "${ROOT_DIR}/70-79_islands_archipelago_studies/710_case-studies"
    create_dir "${ROOT_DIR}/70-79_islands_archipelago_studies/720_atlases-gazetteers"
    create_dir "${ROOT_DIR}/70-79_islands_archipelago_studies/730_island-typologies"
    create_dir "${ROOT_DIR}/70-79_islands_archipelago_studies/740_metaphors-models"

    # -------------------------------------------------------------------------
    # 80-89_knowledge_systems
    # -------------------------------------------------------------------------
    log_section "80-89 KNOWLEDGE SYSTEMS"

    create_dir "${ROOT_DIR}/80-89_knowledge_systems/800_obsidian"
    create_standard_zeros "${ROOT_DIR}/80-89_knowledge_systems/800_obsidian" "800"

    # Distributed persona lenses (805-809)
    create_persona_lenses "${ROOT_DIR}/80-89_knowledge_systems" "8"

    create_dir "${ROOT_DIR}/80-89_knowledge_systems/810_zotero"
    create_dir "${ROOT_DIR}/80-89_knowledge_systems/820_calibre"
    create_dir "${ROOT_DIR}/80-89_knowledge_systems/830_backups-exports"
    create_dir "${ROOT_DIR}/80-89_knowledge_systems/840_conversion-pipelines"

    # -------------------------------------------------------------------------
    # 90-99_public_web_portfolio
    # -------------------------------------------------------------------------
    log_section "90-99 PUBLIC & WEB PORTFOLIO"

    create_dir "${ROOT_DIR}/90-99_public_web_portfolio/900_website"
    create_standard_zeros "${ROOT_DIR}/90-99_public_web_portfolio/900_website" "900"

    # Distributed persona lenses (905-909)
    create_persona_lenses "${ROOT_DIR}/90-99_public_web_portfolio" "9"

    create_dir "${ROOT_DIR}/90-99_public_web_portfolio/910_blog"
    create_dir "${ROOT_DIR}/90-99_public_web_portfolio/920_newsletters"
    create_dir "${ROOT_DIR}/90-99_public_web_portfolio/930_talks-workshops"
    create_dir "${ROOT_DIR}/90-99_public_web_portfolio/940_public-datasets"

    # =========================================================================
    # SUMMARY
    # =========================================================================
    echo ""
    echo "╔═══════════════════════════════════════════════════════════════════════════╗"
    echo "║                              COMPLETE                                     ║"
    echo "╚═══════════════════════════════════════════════════════════════════════════╝"
    echo ""
    log_info "Directories created: ${DIRS_CREATED}"
    log_info "Directories existed: ${DIRS_EXISTED}"
    log_info "Seed files created:  ${FILES_CREATED}"
    log_info "Total structure:     $(find "$ROOT_DIR" -type d 2>/dev/null | wc -l | tr -d ' ') directories"
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    log_info "SEED FILES CREATED:"
    echo ""
    echo "  Templates (000.03_templates/):"
    echo "    • tpl-standard-note.md"
    echo "    • tpl-persona.md"
    echo "    • tpl-zettel.md"
    echo "    • tpl-daily.md"
    echo "    • tpl-moc.md"
    echo ""
    echo "  Indexes:"
    echo "    • jdex.md (000.00_index/)"
    echo "    • persona-registry.md (050.00_index/)"
    echo "    • _zettel-guide.md (zettels/)"
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    log_info "DISTRIBUTED PERSONA STRUCTURE:"
    echo ""
    echo "  Central Registry: 05-09_persona_registry/"
    echo "    └── 051_fictional-voices/  ← Canonical character sheets"
    echo ""
    echo "  Distributed Traces (X05-X09 in each area):"
    echo "    • 005-009 in System"
    echo "    • 105-109 in Language & Theory"
    echo "    • 205-209 in Gesturalism Lab"
    echo "    • 305-309 in Research Dossiers"
    echo "    • 405-409 in Writing Studio"
    echo "    • 505-509 in Editorial"
    echo "    • 605-609 in Media Lab"
    echo "    • 705-709 in Islands Studies"
    echo "    • 805-809 in Knowledge Systems"
    echo "    • 905-909 in Public/Web"
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    log_info "NEXT STEPS:"
    echo ""
    echo "  1. Open Obsidian → Create new vault → Select: ${ROOT_DIR}"
    echo ""
    echo "  2. Install plugins (Settings → Community plugins):"
    echo "     • Dataview"
    echo "     • Templater"
    echo "     • QuickAdd (optional)"
    echo ""
    echo "  3. Configure Templater:"
    echo "     Template folder: 00-04_system/000_system-index/000.03_templates"
    echo ""
    echo "  4. Start with the JDex: Open jdex.md"
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    log_info "UTILITY COMMANDS (source script first: source create_gesturalist_hq.sh):"
    echo ""
    echo "  # Create new category range:"
    echo "  jd_create_category_range \"./10-19_language_theory\" 160 169 \"cognitive-science\""
    echo ""
    echo "  # Create ID folders within a category:"
    echo "  jd_create_id_range \"./10-19_language_theory/120_phenomenology\" 10 19"
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

# Run main if executed directly
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
