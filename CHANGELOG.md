# Selfie AI Studio Changelog

## Prompt Catalog candidate
- Added a read-only SQLite Prompt Catalog tab for the normalized METACAMP v2 database.
- Added cross-field search and category, kind, adult-level, model, and favorite filters.
- Added explicit append-to-Generate and append-to-Negative actions without automatic generation.
- Added validated ZIP/SQLite installation with automatic backup of an existing catalog.
- Kept catalog user state in a separate JSON overlay so catalog updates do not erase favorites or usage history.
- Kept the existing Prompt Library, History, Adopted DB, Character, and Project stores unchanged.

## 1.5
- Tracks the v1.5 development line; the application display and persistence version remain v1.2.3.
- Project / Character / Prompt Library / Prompt Builder management.
- Model and LoRA management.
- Forge single and queued generation.
- Added Shiori Theme foundation, UI Theme switching, and persistent theme settings.
- Added UI Font switching with Current / Default, Yu Gothic UI, Meiryo UI, and safe fallback behavior.
- Redesigned the Generate workspace with collapsible production preparation, summaries, five-step workflow status, generation status, inline advanced settings, improved Preview, and an independently scrollable session gallery.
- Improved Generate presets with Seed, Scheduler, Positive Prompt, Negative Prompt, and update-in-place support.
- Added Master Reference backend and UI for external Master selection, Master display/comparison, and application to single or queued generation.
- Added Post Color Correction for Contrast, Saturation, and Brightness, including reusable presets and non-destructive corrected copies.
- Added A/B comparison controls for generated, Master, adopted, and color-corrected images.
- Expanded History with Character filtering, production status, Generate restoration, and Send to Selfie workflow support.
- Expanded evaluation and adoption handling with adoption metadata and Adopted DB integration.
- Production session save and restore.
- AI Assistant foundation and OpenAI API integration.
- Image review and AI image-analysis workflow.
- Added Send to Selfie review Markdown with image path, generation settings, review goal selection, clipboard notification, and folder-open support.
- Added `Start_Selfie_AI_Studio.vbs` as the primary Windows launcher, with hidden startup and logging to `startup.log`; existing CMD launchers remain available for compatibility.
- v1.5 release self-check.
- Workflow and layout cleanup.
- Git helper scripts renamed to ASCII-only filenames to avoid Windows cmd encoding issues.
