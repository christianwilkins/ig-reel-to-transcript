# Reel Intel

Analyze Instagram side-hustle reels for signal vs hype.

## What it does

- Pulls public reel text using `r.jina.ai` mirror
- Extracts title, caption, hashtags, and top comments
- Auto-loads cached local transcripts from `research/reels/transcripts/<shortcode>.txt` when available
- Scores hype risk (bullshit likelihood)
- Computes extraction confidence so sparse reels are flagged as provisional
- Weights transcript confidence by information density (token count + unique-token ratio) so repetitive CTA-heavy audio does not over-inflate extraction confidence
- Detects repeated opener ASR artifacts in transcripts (for example the same short phrase repeated several times at the start) and down-weights confidence when those artifacts appear
- Extracts explicit claim lines (including transcript-backed lines when available) to separate business claims from noisy reactions
- Reports `substantive_claim_lines` and `cta_only_claim_lines` counts in JSON and markdown output so decision logs can gate low-quality claim evidence quickly
- Filters most question-style prompt lines out of claim extraction so comments like `how?` or `is this real?` are less likely to be misclassified as business claims; numeric questions are only kept when they look like first-person self-report claims
- Prioritizes creator-origin claim evidence (caption, extracted body lines, transcript) before audience comments so claim-line quality is less sensitive to comment chatter
- Caps comment-derived claim lines and requires stronger self-report language for non-numeric audience comments so audience chatter is less likely to dominate claim evidence
- Excludes intent-only audience comments (`interested`, `link pls`, `dm me`) from claim-line extraction so CTA replies are less likely to appear as business claims
- Uses normalized claim-line dedupe keys so punctuation/casing variants do not inflate claim evidence counts
- Down-weights extraction confidence when extracted claim lines are mostly CTA-only prompts (`comment`, `dm`, `link in bio`) without substantive economics or execution detail
- Down-weights extraction confidence when claim lines are highly repetitive, since duplicate claim phrasing is weaker evidence than independent claims
- Caps CTA-dominant claim lines so keyword-funnel prompts cannot crowd out substantive claim evidence
- Prefers substantive creator/transcript claim evidence and keeps at most a very small amount of creator CTA claim context when substantive lines are still thin
- Treats CTA-dominant lines (for example `comment X below` / `dm X`) as non-substantive unless they also contain concrete self-report or economic anchors
- Down-weights extraction confidence when most claim lines are comment-derived instead of creator/transcript-origin evidence
- Applies a smaller confidence downgrade when even a small claim set still leans comment-derived (for example 2 of 3 claim lines)
- Down-weights confidence when substantive claim lines are absent from creator/transcript-origin text, so primary-source evidence is required before high-confidence reads
- Captures more Instagram reply-marker variants (`Reply`, `Reply 2d`, `Reply · 2d`, `2d Reply`, `2mo Reply`, `View replies`, `View 3 replies`) for explicit comment extraction
- Backtracks from `Reply` markers to recover nearby comment text even when `Like`/metadata lines sit between the comment and reply controls
- Tracks how many explicit reply-block comment candidates were recovered before filtering and reports that count in outputs
- Reports `kept_comment_count` in JSON and markdown output so decision logs can compare retained audience context against recovered explicit candidates without manual counting
- Reports explicit comment retention ratio (kept vs recovered explicit candidates) and applies extra confidence penalties when retention is very low, so noisy reply blocks do not overstate audience-context quality
- Applies an extra confidence penalty when explicit reply blocks are plentiful but only a couple comments survive filtering, so low-depth audience context is treated conservatively even if ratio-based retention is not extreme
- Avoids inferred comment backfill when explicit reply-block comments were detected but filtered as low-signal, so confidence stays conservative instead of mixing in body-text guesses
- Recovers very short comment bodies too (for example `how?`, `scam?`, `now`) and relies on low-signal filters so concise skepticism is preserved while CTA spam is dropped
- Filters common Instagram boilerplate lines (`Never miss a post from`, `audio muted`, `add comment`) and shared placeholder-preview lines (`Watch this reel by ... on Instagram`, `Check out this reel by ... on Instagram`, `Shared this reel by ... on Instagram`) from signal extraction
- Preserves substantive lines that mention Instagram mechanics (for example algorithm/distribution claims) while still filtering title-chrome lines like `X on Instagram: ...`
- Filters translation prompts and like or view count control lines from signal extraction
- Filters see more or see less control lines and original audio or reels remix labels from signal extraction
- Filters view-all comment counts, view more or previous comments, view or hide replies controls, and similar UI lines from signal extraction
- Filters login-form and blob-image markup lines (`mobile number, username or email`, `password`, `blob:http://localhost`) from signal extraction
- Filters relative-time metadata lines (for example `2d`, `3h edited`) from comment and signal recovery
- Filters `Replying to @handle` thread-context lines and standalone `author` markers so comment extraction is less likely to treat UI metadata as audience evidence
- Filters pinned and social-context metadata lines (`Pinned`, `Liked by creator`, `Creator liked this`, `Followed by`) from signal/comment extraction
- Filters common creator CTA lines from recovered comments (`follow & comment`, `comment X below`, `dm X`, `link in bio`) so comment context is less likely to include caption/funnel text
- Filters likely username-handle lines from signal and comment recovery so account labels are less likely to be treated as evidence
- Strips leading @mention chains from recovered comments (for example `@name @name2 interested`) so duplicate intent replies dedupe more reliably, and drops pure mention-only tag chains from comment evidence
- Adds fallback comment-context inference when explicit comment blocks are missing
- Allows short inferred skepticism comments (`scam?`, `proof?`, `how?`) when reply blocks are absent so confidence can include concise but meaningful audience pushback
- Filters low signal CTA echo comments (for example `now please bro` replies to a `comment "now"` prompt) so confidence is not inflated by keyword spam
- Filters mention-heavy tag replies with only lightweight tails (`@user1 @user2 done`) so giveaway or funnel tagging noise does not inflate audience-context confidence
- Filters generic acknowledgement-only replies (`done`, `sent`, `check dm`, `inbox me`) even when CTA keyword extraction is incomplete, including mention plus acknowledgement variants like `@user done`
- Filters short help-solicitation replies (`who need help?`, `need help`) when they carry no substantive execution or skepticism detail
- Filters self-promo solicitation replies (`dm me I can help`, `I do this too`) when those comments are short offer-funnel chatter without substantive detail
- Filters tag-a-friend referral chatter (`@name check this`, `tag @name`, `sent this to @name`) when those lines are non-question social distribution noise without substantive detail
- Filters delivery-acknowledgement reply noise (`check your DM`, `DM sent`, `sent you a DM`, `check inbox`, `check PM`, `PM sent`) when those lines contain no substantive execution or skepticism detail
- Filters meta routing comments (`check pinned`, `read caption`, `details in bio`, `pin this`) when those lines are non-question funnel logistics without substantive execution detail
- Filters short availability/logistics intent comments (`still available?`, `where link?`, `any spots left?`) when they are funnel-intent chatter without substantive execution or skepticism detail
- Preserves short legal or execution due-diligence questions (`title company?`, `escrow?`, `assignment legal?`, `closing costs?`, `buyer list?`) so real process-risk context is less likely to be filtered out as low-signal intent chatter
- Filters follow-back reciprocity chatter (`follow back`, `f4f`, `follow me back`, `mutuals`) when those lines are growth-loop noise without substantive execution detail
- Filters growth-loop exchange chatter (`sub4sub`, `support for support`, `let's grow together`, `follow train`) when those lines are non-question reciprocity noise without substantive execution detail
- Filters story-share/repost acknowledgements (`shared to my story`, `reposted`, `posted this`) when those lines are non-question distribution chatter without substantive execution detail
- Filters short manifestation/faith-affirmation chatter (`amen`, `claiming this`, `manifesting`) when those lines are non-question social-agreement noise without substantive execution detail
- Filters short giveaway-entry chatter (`pick me`, `choose me`, `hope I win`, `I need this`) when those lines are non-question participation noise without substantive execution detail
- Filters short testimonial-vouch chatter (`vouch`, `he sent`, `got mine`, `works for me`, `I can't stop winning`) when those lines are non-question social-proof snippets without substantive detail
- Filters short agreement-only snippets (`me too`, `same here`) when those lines are non-question social-proof echoes without substantive detail
- Filters short gratitude-only snippets (`thanks`, `thank you`, `appreciate it`, `thx`) when those lines are non-question acknowledgement chatter without substantive detail
- Filters short engagement-task completion chatter (`liked and followed`, `done shared`, `commented + saved`) when those lines are non-question giveaway/funnel logistics without substantive detail
- Filters short save-for-later chatter (`saving this`, `for later`, `bookmarking`, `come back to this`) when those lines are non-question reminder noise without substantive detail
- Filters year-check nostalgia chatter (`who's here in 2026`, `anyone watching in 2025`) when those lines are low-context engagement noise without substantive execution detail
- Filters location roll-call chatter (`who's here from Nigeria`, `watching from India`, `anyone from Brazil`) when those lines are low-context engagement noise without substantive execution detail
- Filters day-streak engagement chatter (`day 12 of asking`, `part 5 of commenting`, `until he notices`) when those lines are non-substantive algorithmic persistence noise
- Filters public contact-handoff chatter (`whatsapp me`, `telegram`, `text me`, phone/email drops) when those lines contain no substantive execution or skepticism detail
- Filters low-context reaction-only comments (`wow`, `fire`, `facts`, `ugh`, `nah`, `lmao`, `cap`, `sus`) and short stopword-heavy reaction phrases (`this is crazy bro`, `that is cap`) unless they include substantive skepticism or execution questions
- Filters algorithm-bump engagement noise (`algo`, `fyp`, `cfbr`, `boost`, `bump`, `first`) and short distribution-push phrases (`for the algo`, `for reach`, `push this`) when those comments lack substantive execution context
- Filters short visibility-push chatter (`for visibility`, `visibility bump`, `bumping this`, `commenting to boost`) when those comments are non-substantive algorithm-gaming noise
- Filters emoji-only and symbol-only chatter (`🔥🔥`, `🙏`, `...`) so reaction glyph noise does not inflate audience-context quality
- Filters short numeric-only approval comments (`100`, `100%`, `10/10`) so lightweight cheer reactions do not inflate audience-context quality
- Down-weights extraction confidence when captured comments are mostly intent-only CTA replies (`interested`, `link pls`, `dm me`) with limited analytical depth
- Down-weights extraction confidence when surviving comments are highly repetitive around one phrase, since that usually reflects funnel noise rather than diverse audience context
- Tracks how many recovered comments were filtered as low-signal CTA echoes and applies an extra confidence downgrade when filtered noise dominates comment context
- Tags filtered low-signal comments by dominant pattern (`empty_or_symbol`, `thread_metadata`, `numeric_cheer`, `cta_keyword_echo`, `intent_only`, `algorithm_chatter`, `year_check_nostalgia`, `location_rollcall`, `day_streak_chatter`, `generic_reaction`, `meta_routing`, `dm_logistics`, `contact_handoff`, `help_solicitation`, `mention_only`, `mention_filler`, `giveaway_entry`, `self_promo_solicitation`, `followback_reciprocity`, `growth_loop_exchange`, `tag_referral`, `story_share_repost`, `manifestation_affirmation`, `testimonial_vouch`, `save_for_later`, `gratitude_only`, and related buckets) and reports the primary pattern in outputs
- Reports dominant low-signal pattern share and applies an extra confidence downgrade when one low-context noise pattern dominates the filtered pool
- Applies an extra confidence penalty when filtered low-signal comment volume is at least 2x the kept comment count, even if some comments survive
- Applies an extra confidence penalty when comment context is inferred and filtered low-signal comment volume remains high, since usable audience evidence is likely fragile
- Filters hashtag-heavy caption or body lines from claim and signal extraction so dense hashtag blocks do not inflate evidence quality
- Downgrades caption confidence when extracted caption text is mostly hashtag-heavy and low in semantic detail
- Normalizes elongated CTA keyword echoes (`freeee`, `nowww`) so stretched variants are filtered as low-signal replies
- Normalizes reaction and intent token matching after repeated-letter cleanup so low-signal classifiers still catch variants like `goood`, `niiice`, and `reeady`
- Extracts comment CTA keywords from caption, early signal lines, and transcript snippets so keyword-gated replies are still filtered when CTA prompts appear only in spoken audio (for example `DM me the word prompt`)
- Scans CTA-like transcript prompts across the full transcript (not only early lines) so late-spoken keyword gates are less likely to leak into kept comments
- Captures reply prompt keywords when captions use reply or respond instead of comment
- Captures comment-below keyword prompts (comment below X, comment the word X, drop or type the word X)
- Down-weights transcript confidence when a large share of transcript lines are CTA-dominant prompts, so repetitive funnel audio does not overstate evidence depth
- Weights comment provenance by source quality in confidence scoring (explicit > mixed > inferred)
- Applies extra confidence penalty when claim lines are thin and comment context is inferred-only
- Applies extra confidence penalty when comment context is inferred or none, no explicit reply blocks were detected, and only 0 to 1 comments are kept (unless a transcript is available)
- Down-weights confidence when body signal lines are duplicates of caption/comments instead of independent evidence
- Down-weights confidence when captured body lines are mostly CTA or handle-style text rather than independent claims
- Detects Instagram access-wall, login, and unavailable-page payloads and applies a strong confidence penalty with explicit notes
- Detects instagram shell placeholder payloads (for example `www.instagram.com`, `# www.instagram.com`, `Instagram photos and videos`, `Login • Instagram`, `Watch this reel by ... on Instagram`, `Check out this reel by ... on Instagram`, `Shared this reel by ... on Instagram`) and down-weights confidence so transcript-only evidence is not over-trusted
- Filters access-wall/login/unavailable-page copy out of claim-line extraction to avoid false business-claim matches (for example `See everyday moments from your close friends` or `Sorry, this page isn't available`)
- Penalizes confidence when extracted payload is overly repetitive (including small 3-line payloads)
- Normalizes hashtags to lowercase to reduce duplicate tag variants (`AI` vs `ai`)
- Applies an uncertainty buffer when extraction confidence is low, with stronger penalties under 20 confidence
- Applies a neutral-risk floor (50/100) when extraction evidence is extremely thin to avoid false low-hype verdicts
- Adds niche-specific checks:
  - real estate wholesaling / foreclosure angle
  - AI OnlyFans-style creator hustle
  - UI/web build content
- Generates action checklist and content repurposing hooks
- Adds 21st.dev integration prompt pack when UI/web topic is detected

## Optional transcript mode

Use `--try-transcript` for audio transcription.

Default behavior first checks local transcript cache files in `research/reels/transcripts/` by reel shortcode. If not found, `--try-transcript` adds live audio transcription attempts.

It tries:
1. `yt-dlp` + local `whisper` CLI
2. `yt-dlp` + local `whisper-cpp` (`whisper-cli` + local model)
3. `yt-dlp` + OpenAI transcription API (requires `OPENAI_API_KEY`)

If dependencies are missing, it falls back to caption/comments only and reports why.

## Setup (optional for full transcripts)

```bash
bash tools/reel-intel/setup.sh
```

This installs `yt-dlp` if Homebrew is available and gives next steps for Whisper.

## Usage

```bash
python3 tools/reel-intel/reel_intel.py "https://www.instagram.com/reel/XXXXX/"
```

Multiple links:

```bash
python3 tools/reel-intel/reel_intel.py URL1 URL2 URL3
```

Save JSON + markdown reports:

```bash
python3 tools/reel-intel/reel_intel.py URL1 URL2 \
  --save-dir research/reels
```

Try transcript:

```bash
python3 tools/reel-intel/reel_intel.py URL --try-transcript
```

JSON output only:

```bash
python3 tools/reel-intel/reel_intel.py URL --json-only
```

## Recommended workflow in this chat

1. Drop reel links here.
2. I run `reel_intel.py` on those links.
3. I return:
   - transcript or fallback extracted text
   - hype score and verdict
   - actionable go / no-go
   - content idea to post next

## Notes

- Private reels and bot-protected pages may reduce extraction quality. Low confidence runs now get an automatic uncertainty penalty.
- Very low confidence runs (especially access-wall or unavailable-page payloads) now avoid false low-hype outputs by applying a neutral-risk floor.
- If Instagram markup does not expose explicit replies, the tool now infers likely audience-comment lines from extracted signal text, labels the comment context source (`explicit`/`inferred`/`mixed`/`none`), and marks repetitive payloads as lower confidence.
- Confidence now degrades slightly when captured comments are mostly ultra short (1 to 2 token) replies, since those often carry weak context.
- Explicit comment extraction now handles reply marker variants like `Reply 2d` and `2d Reply`, and backtracks over `Like`/metadata lines to recover the nearest likely comment body.
- This is decision support, not legal advice.
- Real estate and adult-content-related ideas have legal/compliance complexity. Validate locally before execution.
